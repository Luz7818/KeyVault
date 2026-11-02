// SecretRepo —— 移植桌面版 saveops/rotate/review/corrections/audit 的语义，
// 存储后端注入（浏览器 = IndexedDB（db.js），测试 = 内存替身）。
//
// 与桌面版对齐的核心语义：
// - canonicalize + sha256 指纹去重：同值合并 tags/别名，绝不产生第二条；
// - rotate 保留旧指纹（status=revoked）——历史可追溯；
// - 审计 append-only + hash 链（sha256 串链，与桌面 audit 链同构）；
// - 重名不同值报 AlreadyExists；重名同值走去重。
// - 备份格式 KVBE1（口令派生 AES-GCM），与桌面 .kvb 同理念（口令是唯一防线）。

import { canonicalize, fingerprint, splitMask, preview, inferMaskStyle,
  stableStringify } from "../core/masking.js";
import { sha256Hex } from "../core/sha256.js";
import { parse, emit } from "../core/parse.js";
import { resolve, isTrusted } from "../core/resolve.js";
import { CorrectionSet, suggestCorrectionScope } from "../core/corrections.js";
import { deriveRawKey, encryptBytes, decryptBytes, encryptText, decryptText,
  importRawKey, randomBytes, bytesToB64, b64ToBytes } from "./crypto.js";

const KDF_ITERATIONS = 600000;
const VERIFIER_PLAINTEXT = "kv-vault-ok";
const BACKUP_MAGIC = "KVBE1";

export class LockedError extends Error {
  constructor() {
    super("vault 已锁定——先解锁");
    this.name = "LockedError";
  }
}

export class AlreadyExistsError extends Error {
  constructor(name) {
    super(`同名记录已存在：${name}（同值会走去重，不同值请改名）`);
    this.name = "AlreadyExistsError";
  }
}

function nowIso() {
  return new Date().toISOString();
}

function assertUnlocked(session) {
  if (!session.isUnlocked()) {
    throw new LockedError();
  }
  return session;
}

function canonicalJson(value) {
  return stableStringify(value);
}

// ---------------------------------------------------------------- 初始化/解锁

export async function isInitialized(backend) {
  return (await backend.metaGet("vault-meta")) !== null;
}

/** 建库：生成 salt，写校验器（加密已知明文）。已有库重复 setup 是错误。 */
export async function setupVault(backend, password) {
  if (await isInitialized(backend)) {
    throw new Error("vault 已经初始化过了");
  }
  const salt = randomBytes(16);
  const raw = await deriveRawKey(password, salt, KDF_ITERATIONS);
  const key = await importRawKey(raw);
  const verifier = await encryptText(key, VERIFIER_PLAINTEXT);
  await backend.metaSet("vault-meta", {
    kdf: "PBKDF2-SHA256", iterations: KDF_ITERATIONS,
    salt: bytesToB64(salt),
    verifierIv: bytesToB64(verifier.iv), verifierCt: bytesToB64(verifier.ct),
    createdAt: nowIso(),
  });
  await auditAppend(backend, { event: "init", actor: "extension", detail: {} });
  return raw;
}

/** 解锁：派生 → 解校验器 → 原始密钥进会话。口令错返回 false（不泄露原因）。 */
export async function unlock(backend, session, password) {
  const meta = await backend.metaGet("vault-meta");
  if (!meta) throw new Error("vault 未初始化——先设置主密码");
  const raw = await deriveRawKey(password, b64ToBytes(meta.salt), meta.iterations);
  try {
    const key = await importRawKey(raw);
    const plain = await decryptText(key, {
      iv: b64ToBytes(meta.verifierIv), ct: b64ToBytes(meta.verifierCt),
    });
    if (plain !== VERIFIER_PLAINTEXT) return false;
  } catch (_) {
    return false; // GCM 认证失败 = 口令错误
  }
  await session.saveRawKey(raw);
  return true;
}

async function currentKey(session) {
  assertUnlocked(session);
  return session.key();
}

// ---------------------------------------------------------------- 审计链

export async function auditAppend(backend, { event, actor, secretId = null,
                                              nameSnapshot = null,
                                              shaPrefix = null, detail = {} }) {
  const last = await backend.auditLast();
  const ts = nowIso();
  const prevHash = last ? last.hash : "";
  const hash = sha256Hex([
    prevHash, ts, event, actor,
    secretId ?? "-", nameSnapshot ?? "-", shaPrefix ?? "-",
    canonicalJson(detail),
  ].join("|"));
  await backend.auditAppend({
    ts, event, actor, secretId, nameSnapshot, shaPrefix, detail, prevHash, hash,
  });
}

export async function auditList(backend, { limit = 100 } = {}) {
  const rows = await backend.auditList(limit);
  // 校验链条完整性（从最旧往最新复算）
  let prev = "";
  let firstBadId = null;
  for (const row of [...rows].reverse()) {
    const expected = sha256Hex([
      prev, row.ts, row.event, row.actor,
      row.secretId ?? "-", row.nameSnapshot ?? "-", row.shaPrefix ?? "-",
      canonicalJson(row.detail),
    ].join("|"));
    if (expected !== row.hash && firstBadId === null) firstBadId = row.id;
    prev = row.hash;
  }
  return { rows, chainOk: firstBadId === null, firstBadId };
}

// ---------------------------------------------------------------- 记录操作

function suggestionFor(platform, keyName, existingNames) {
  const base = keyName ? keyName.toLowerCase().replace(/_?api_?key$|_?token$|_?key$|_?secret$/, "")
    .replace(/^_+|_+$/g, "") || platform : platform;
  let name = `${base}-${platform}`;
  let n = 1;
  while (existingNames.has(name)) {
    n += 1;
    name = `${base}-${platform}-${n}`;
  }
  return name;
}

/**
 * 新增一条。语义对齐 saveops.commit：规范化 → 指纹 → 去重合并 → 命名 → 落库。
 * value 是明文字符串；platform/confidence/evidence 来自调用方的检测结果。
 */
export async function add(backend, session, spec) {
  const key = await currentKey(session);
  const value = spec.value ?? "";
  const kind = spec.kind ?? "token";
  const canonical = canonicalize(value, kind);
  if (!canonical.trim()) {
    return { outcome: "rejected", warning: "值为空。空值表示未配置，不该进库" };
  }
  const sha = fingerprint(value, kind);

  const existing = await backend.secretBySha256(sha);
  const mergedTags = [];
  if (existing) {
    const tagSet = new Set(existing.tags);
    for (const tag of spec.tags ?? []) {
      if (!tagSet.has(tag)) { tagSet.add(tag); mergedTags.push(tag); }
    }
    existing.tags = [...tagSet];
    if (!existing.keyName && spec.keyName) existing.keyName = spec.keyName;
    if (!existing.note && spec.note) existing.note = spec.note;
    if (!existing.sourceUrl && spec.sourceUrl) existing.sourceUrl = spec.sourceUrl;
    if (existing.status === "revoked") {
      existing.warning = "这把 key 曾被吊销，重新入库——确认轮换后更新";
    }
    existing.updatedAt = nowIso();
    await backend.secretPut(existing);
    await auditAppend(backend, {
      event: "dedupe_hit", actor: spec.actor ?? "extension",
      secretId: existing.id, nameSnapshot: existing.name, shaPrefix: sha.slice(0, 12),
      detail: { merged_tags: mergedTags },
    });
    return {
      outcome: "deduped", secretId: existing.id, name: existing.name,
      mergedTags, warning: existing.warning ?? "",
    };
  }

  // 命名：显式 name 冲突即错；自动名避开现有
  const all = await backend.secretAll();
  const names = new Set(all.map((r) => r.name));
  let name = (spec.name || "").trim();
  if (name) {
    if (names.has(name)) throw new AlreadyExistsError(name);
  } else {
    const platform = spec.platform || "unknown";
    name = suggestionFor(platform, spec.keyName, names);
  }

  const [head, tail] = splitMask(canonical, ...(spec.maskStyle ?? inferMaskStyle(canonical)));
  const ts = nowIso();
  const box = await encryptText(key, canonical);
  const record = {
    id: null, name,
    platform: spec.platform || "unknown",
    confidence: spec.confidence || "none",
    evidence: spec.evidence || "",
    detectSource: spec.source ?? "manual",
    keyName: spec.keyName || null,
    kind,
    valueIv: bytesToB64(box.iv), valueCt: bytesToB64(box.ct),
    sha256: sha, maskHead: head, maskTail: tail,
    valueLen: canonical.length,
    status: "active",
    note: spec.note || "",
    sourceUrl: spec.sourceUrl || "",
    origin: spec.origin ?? "manual",
    extra: spec.extra ?? {},
    createdAt: ts, updatedAt: ts,
    expiresAt: spec.expiresAt || null,
    lastUsedAt: null, lastRevealedAt: null,
    tags: [...(spec.tags ?? [])], aliases: [],
  };
  record.id = await backend.secretPut(record);
  await backend.fpPut({
    sha256: sha, secretId: record.id, status: "active",
    firstSeenAt: ts, revokedAt: null,
  });
  await auditAppend(backend, {
    event: "save", actor: spec.actor ?? "extension",
    secretId: record.id, nameSnapshot: name, shaPrefix: sha.slice(0, 12),
    detail: { origin: record.origin, platform: record.platform },
  });
  return { outcome: "created", secretId: record.id, name, mergedTags: [], warning: "" };
}

/** 列表：只出掩码行，绝不带密文/明文。 */
export async function listSecrets(backend, { query = "", platform = "", status = "" } = {}) {
  const all = await backend.secretAll();
  const lowered = query.trim().toLowerCase();
  return all
    .filter((r) => (platform ? r.platform === platform : true))
    .filter((r) => (status ? r.status === status : true))
    .filter((r) => {
      if (!lowered) return true;
      const hay = [r.name, r.platform, r.note, r.keyName, r.sourceUrl,
        ...(r.tags ?? []), ...(r.aliases ?? [])].join("\n").toLowerCase();
      return hay.includes(lowered);
    })
    .sort((a, b) => (b.createdAt ?? "").localeCompare(a.createdAt ?? ""))
    .map((r) => ({
      id: r.id, name: r.name, platform: r.platform, status: r.status,
      confidence: r.confidence, keyName: r.keyName, kind: r.kind,
      preview: preview(r.maskHead, r.maskTail),
      tags: r.tags, aliases: r.aliases, note: r.note, sourceUrl: r.sourceUrl,
      origin: r.origin, extra: r.extra,
      createdAt: r.createdAt, updatedAt: r.updatedAt, expiresAt: r.expiresAt,
      lastUsedAt: r.lastUsedAt,
    }));
}

export async function getSecret(backend, id) {
  const record = await backend.secretGet(id);
  if (!record) throw new Error(`记录不存在：#${id}`);
  const { valueIv, valueCt, ...safe } = record;
  void valueIv; void valueCt;
  const fps = await backend.fpListBySecret(id);
  return {
    ...safe,
    preview: preview(record.maskHead, record.maskTail),
    fingerprints: fps.map((f) => ({
      sha256Prefix: f.sha256.slice(0, 16), status: f.status, firstSeenAt: f.firstSeenAt,
    })),
  };
}

/** 看明文的唯一路径（与桌面 reveal 同语义：审计记指纹，明文只交调用方）。 */
export async function reveal(backend, session, id, { event = "reveal", actor = "extension" } = {}) {
  const key = await currentKey(session);
  const record = await backend.secretGet(id);
  if (!record) throw new Error(`记录不存在：#${id}`);
  const plain = await decryptText(key, {
    iv: b64ToBytes(record.valueIv), ct: b64ToBytes(record.valueCt),
  });
  await backend.secretPut({ ...record, lastRevealedAt: nowIso() });
  await auditAppend(backend, {
    event, actor, secretId: id, nameSnapshot: record.name,
    shaPrefix: record.sha256.slice(0, 12), detail: {},
  });
  return { record, value: plain };
}

/** 轮换：旧指纹保留 revoked，新值替换。 */
export async function rotate(backend, session, id, newValue) {
  const key = await currentKey(session);
  const record = await backend.secretGet(id);
  if (!record) throw new Error(`记录不存在：#${id}`);
  const canonical = canonicalize(newValue, record.kind);
  if (!canonical.trim()) return { outcome: "rejected", warning: "新值为空" };

  const oldSha = record.sha256;
  const now = nowIso();
  const oldFps = await backend.fpListBySecret(id);
  for (const fp of oldFps) {
    if (fp.sha256 === oldSha && fp.status === "active") {
      await backend.fpPut({ ...fp, status: "revoked", revokedAt: now });
    }
  }

  const sha = fingerprint(newValue, record.kind);
  const [head, tail] = splitMask(canonical, ...(record.maskStyle
    ?? inferMaskStyle(canonical)));
  const box = await encryptText(key, canonical);
  record.valueIv = bytesToB64(box.iv);
  record.valueCt = bytesToB64(box.ct);
  record.sha256 = sha;
  record.maskHead = head;
  record.maskTail = tail;
  record.valueLen = canonical.length;
  record.updatedAt = now;
  await backend.secretPut(record);
  await backend.fpPut({
    sha256: sha, secretId: id, status: "active", firstSeenAt: now, revokedAt: null,
  });
  await auditAppend(backend, {
    event: "rotate", actor: "extension", secretId: id,
    nameSnapshot: record.name, shaPrefix: sha.slice(0, 12),
    detail: { old_sha_prefix: oldSha.slice(0, 12) },
  });
  return { outcome: "created", name: record.name };
}

export async function revoke(backend, id) {
  const record = await backend.secretGet(id);
  if (!record) throw new Error(`记录不存在：#${id}`);
  record.status = "revoked";
  record.updatedAt = nowIso();
  await backend.secretPut(record);
  await auditAppend(backend, {
    event: "revoke", actor: "extension", secretId: id,
    nameSnapshot: record.name, shaPrefix: record.sha256.slice(0, 12), detail: {},
  });
}

export async function deleteSecret(backend, id) {
  const record = await backend.secretGet(id);
  if (!record) throw new Error(`记录不存在：#${id}`);
  await backend.secretDelete(id);
  await auditAppend(backend, {
    event: "delete", actor: "extension", secretId: null,
    nameSnapshot: record.name, shaPrefix: record.sha256.slice(0, 12),
    detail: { value_len: record.valueLen },
  });
}

export async function rename(backend, id, newName) {
  const record = await backend.secretGet(id);
  if (!record) throw new Error(`记录不存在：#${id}`);
  const all = await backend.secretAll();
  if (all.some((r) => r.name === newName && r.id !== id)) {
    throw new AlreadyExistsError(newName);
  }
  const oldName = record.name;
  record.name = newName;
  record.updatedAt = nowIso();
  if (!record.aliases.includes(oldName)) record.aliases.push(oldName);
  await backend.secretPut(record);
  await auditAppend(backend, {
    event: "rename", actor: "extension", secretId: id,
    nameSnapshot: newName, shaPrefix: record.sha256.slice(0, 12),
    detail: { old_name: oldName },
  });
}

/** set-platform：可顺带记住纠正规则（keyname/hostname 最窄范围）。 */
export async function setPlatform(backend, id, platform, { remember = null } = {}) {
  const record = await backend.secretGet(id);
  if (!record) throw new Error(`记录不存在：#${id}`);
  record.platform = platform;
  record.confidence = "manual";
  record.evidence = "用户在扩展里指定";
  record.updatedAt = nowIso();
  await backend.secretPut(record);
  await auditAppend(backend, {
    event: "platform_set", actor: "extension", secretId: id,
    nameSnapshot: record.name, shaPrefix: record.sha256.slice(0, 12),
    detail: { platform },
  });
  if (remember) {
    await addCorrection(backend, {
      kind: remember.kind, pattern: remember.pattern, platform,
      note: "浏览器扩展里记住",
    });
  }
}

// ---------------------------------------------------------------- 纠正规则

export async function addCorrection(backend, { kind, pattern, platform, note = "" }) {
  await backend.corrPut({ kind, pattern, platform, note, createdAt: nowIso() });
  await auditAppend(backend, {
    event: "correction_add", actor: "extension", detail: { kind, platform },
  });
}

export async function deleteCorrection(backend, corrId) {
  await backend.corrDelete(corrId);
  await auditAppend(backend, {
    event: "correction_delete", actor: "extension", detail: { id: corrId },
  });
}

export async function listCorrections(backend) {
  return backend.corrAll();
}

function correctionSet(backend_rows) {
  return CorrectionSet.fromRows(
    backend_rows.map((r) => ({ ...r, createdAt: r.createdAt ?? "" })));
}

// ---------------------------------------------------------------- 批量导入文本

/** 粘贴 .env / JSON / curl 文本 → 解析 → 判定 → 只入可信候选。 */
export async function importText(backend, session, text, { tags = [], actor = "extension" } = {}) {
  const corrections = correctionSet(await backend.corrAll());
  const result = parse(text, { sourceHint: "import" });
  const summary = { created: 0, deduped: 0, rejected: 0, skipped: [] };
  for (const cand of result.candidates) {
    const verdict = resolve(cand, corrections);
    if (!isTrusted(verdict) || verdict.platform === "unknown") {
      summary.skipped.push({
        platform: verdict.platform, confidence: verdict.confidence,
        preview: cand.preview(),
      });
      continue;
    }
    const res = await add(backend, session, {
      value: cand.value, kind: cand.kind, keyName: cand.keyName,
      platform: verdict.platform, confidence: verdict.confidence,
      evidence: verdict.evidence, source: "import", tags, actor,
      maskStyle: verdict.maskStyle, origin: "import",
    });
    if (res.outcome === "created") summary.created += 1;
    else if (res.outcome === "deduped") summary.deduped += 1;
    else summary.rejected += 1;
  }
  return summary;
}

// ---------------------------------------------------------------- 过期与统计

/** 把 expires_at 已过的 active 记录翻成 expired。返回翻转数。 */
export async function markExpired(backend) {
  const now = nowIso();
  const all = await backend.secretAll();
  let count = 0;
  for (const record of all) {
    if (record.status === "active" && record.expiresAt && record.expiresAt <= now) {
      record.status = "expired";
      record.updatedAt = now;
      await backend.secretPut(record);
      await auditAppend(backend, {
        event: "expire", actor: "extension", secretId: record.id,
        nameSnapshot: record.name, shaPrefix: record.sha256.slice(0, 12), detail: {},
      });
      count += 1;
    }
  }
  return count;
}

export async function stats(backend) {
  const all = await backend.secretAll();
  const soon = new Date(Date.now() + 30 * 24 * 3600 * 1000).toISOString();
  const platforms = {};
  for (const r of all) {
    if (r.status !== "active") continue;
    platforms[r.platform] = (platforms[r.platform] ?? 0) + 1;
  }
  return {
    total: all.length,
    active: all.filter((r) => r.status === "active").length,
    flagged: all.filter((r) => r.status === "revoked" || r.status === "expired").length,
    expiring: all.filter((r) => r.status === "active" && r.expiresAt
      && r.expiresAt <= soon).length,
    platforms: Object.entries(platforms)
      .sort((a, b) => b[1] - a[1])
      .map(([platform, count]) => ({ platform, count })),
  };
}

// ---------------------------------------------------------------- 备份恢复

/** 加密备份：口令派生（独立 salt）→ AES-GCM 整包。格式 KVBE1|salt|iv|ct（b64）。 */
export async function exportBackup(backend, session, password) {
  await currentKey(session); // 导出要求 vault 处于解锁态（记录要先解出来）
  const records = [];
  for (const record of await backend.secretAll()) {
    const plain = await decryptText(await session.key(), {
      iv: b64ToBytes(record.valueIv), ct: b64ToBytes(record.valueCt),
    });
    records.push({
      name: record.name, platform: record.platform, kind: record.kind,
      keyName: record.keyName, value: plain, tags: record.tags,
      note: record.note, sourceUrl: record.sourceUrl, status: record.status,
      expiresAt: record.expiresAt, createdAt: record.createdAt,
      lastUsedAt: record.lastUsedAt, lastRevealedAt: record.lastRevealedAt,
    });
  }
  const payload = canonicalJson({
    version: 1, exportedAt: nowIso(), records,
  });
  const salt = randomBytes(16);
  const raw = await deriveRawKey(password, salt, KDF_ITERATIONS);
  const key = await importRawKey(raw);
  const box = await encryptBytes(key, new TextEncoder().encode(payload));
  return [BACKUP_MAGIC, bytesToB64(salt), bytesToB64(box.iv), bytesToB64(box.ct)].join("|");
}

/** 恢复：口令独立于主密码（导出时另设）。解包后逐条走 add（去重生效）。 */
export async function restoreBackup(backend, session, password, blobText,
                                    { actor = "extension" } = {}) {
  const parts = blobText.trim().split("|");
  if (parts[0] !== BACKUP_MAGIC || parts.length !== 4) {
    throw new Error("不是 KeyVault 浏览器版备份（KVBE1）格式");
  }
  const raw = await deriveRawKey(password, b64ToBytes(parts[1]), KDF_ITERATIONS);
  const key = await importRawKey(raw);
  let payload;
  try {
    payload = await decryptText(key, { iv: b64ToBytes(parts[2]), ct: b64ToBytes(parts[3]) });
  } catch (_) {
    throw new Error("口令错误或备份已损坏（AES-GCM 认证失败）");
  }
  const parsed = JSON.parse(payload);
  const summary = { created: 0, deduped: 0, rejected: 0 };
  for (const record of parsed.records ?? []) {
    const res = await add(backend, session, {
      value: record.value, kind: record.kind ?? "token", keyName: record.keyName,
      platform: record.platform ?? "unknown", confidence: "manual",
      evidence: "备份恢复", source: "restore", tags: record.tags ?? [],
      note: record.note ?? "", name: record.name, origin: "restore",
      expiresAt: record.expiresAt ?? null, actor,
    });
    if (res.outcome === "created") summary.created += 1;
    else if (res.outcome === "deduped") summary.deduped += 1;
    else summary.rejected += 1;
  }
  await auditAppend(backend, {
    event: "restore", actor, detail: summary,
  });
  return summary;
}

export { emit };
