// SecretRepo —— 桌面 vault 门面（kv/core/vault.py）+ saveops.commit
// （kv/ops/save.py）+ rotate（kv/ops/rotate.py）+ 审计链（kv/core/audit.py，
// 公式见 CONTRACTS.md C4——与桌面 FIELD_SEP/GENESIS 公式不同，两侧审计本就
// 不互通，自洽即可）+ importer（kv/ops/importer.py）+ backup（kv/ops/backup.py，
// 浏览器格式 KVBE1 见 C5）的浏览器等价物。存储后端注入（浏览器 = IndexedDB
// （db.js），测试 = 内存替身（memory-backend.js））。
//
// 与桌面版对齐的核心语义：
// - canonicalize + sha256 指纹去重：同值合并 tags/补别名/回填空位，绝不插第二行
//   （save.py:82-135）；
// - 自动命名对齐 suggest_name（save.py:63-79）：key_name 小写化、非字母数字转-、
//   冲突加 -2/-3 后缀；显式重名不同值抛 AlreadyExistsError（save.py:184-189）；
// - rotate 保留旧指纹（status=revoked + revokedAt），同值拒绝（rotate.py:44），
//   新值与他人记录重复拒绝；旧密文随吊销移入指纹行——吊销的旧值仍是泄露证据
//   （repo.py:300 all_scan_fingerprints 同语义），且任何时刻同一密文只有一份；
// - reveal 是看明文的唯一路径：审计记指纹前缀与长度，明文只交调用方；
// - detail 只允许非机密字段（sanitizeDetail，audit.py:68-94 同规则）；
// - 备份 KVBE1：口令派生 AES-GCM 整包加密，口令是唯一防线（backup.py:100-105）。
//
// 锁定语义：带 session 的全部写操作先 assertUnlocked——锁定态整库冻结。
// restore 不做检测（C5）；importText 的检测经 detect.js，只入 promptable 候选
// （C4/C9#2），unknown/ambiguous 之外的不可信候选进 skipped 列表。

import {
  canonicalize, fingerprint, splitMask, preview, inferMaskStyle, stableStringify,
} from "../core/masking.js";
import { sha256Hex } from "../core/sha256.js";
import {
  LockedError, randomBytes, bytesToB64, b64ToBytes, deriveRawKey, importRawKey,
  encryptText, decryptText, KDF_ITERATIONS, KDF_SALT_BYTES,
} from "./crypto.js";
import { detectText } from "./detect.js";

export { LockedError };

const VERIFIER_PLAINTEXT = "kv-vault-ok";
const META_KEY = "vault-meta";
const BACKUP_MAGIC = "KVBE1";
const SHA_PREFIX_WIDTH = 12;
const MAX_DETAIL_VALUE_LEN = 200;
const EXPIRING_DAYS = 30;

// audit.py:28-31 同一张黑名单：审计日志按构造不含机密。
const FORBIDDEN_DETAIL_KEYS = new Set([
  "value", "secret", "token", "password", "passwd", "key", "api_key", "apikey",
  "plaintext", "blob", "credential", "authorization",
]);

// save.py:30-36 的窄正则兜底：note/sourceUrl 是明文可搜索列，拒绝把看起来
// 像凭据的东西塞进去。
const CREDENTIAL_SMELL = new RegExp(
  "(?:\\b(?:sk-[a-z0-9_\\-]{16,}|(?:ghp|gho|ghu|ghs|ghr)_[a-z0-9]{20,}" +
  "|github_pat_[a-z0-9_]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\\-]{35}" +
  "|glpat-[a-z0-9_\\-]{20,}|xox[baprs]-[a-z0-9\\-]{10,}|vercel_[a-z0-9]{20,}" +
  "|[0-9a-f]{32})\\b" +
  "|\\b[a-z][a-z0-9+.\\-]*://[^:/@\\s]+:[^@\\s]+@[^\\s]+)",
  "i",
);

export class AlreadyExistsError extends Error {
  constructor(message) {
    super(message);
    this.name = "AlreadyExistsError";
  }
}

export class RefusedError extends Error {
  constructor(message, hint = "") {
    super(message);
    this.name = "RefusedError";
    this.hint = hint;
  }
}

export class NotFoundError extends Error {
  constructor(message) {
    super(message);
    this.name = "NotFoundError";
  }
}

function nowIso() {
  return new Date().toISOString();
}

async function assertUnlocked(session) {
  if (!(await session.isUnlocked())) throw new LockedError();
  return session;
}

function requireRecord(record, id) {
  if (!record) throw new NotFoundError(`没有 id=${id} 的记录`);
  return record;
}

// ---------------------------------------------------------------- 初始化/解锁/销毁

export async function isInitialized(backend) {
  return (await backend.metaGet(META_KEY)) !== null;
}

/** 建库：生成盐，写校验器（加密已知明文）。已初始化或口令 <8 位都是错误。 */
export async function setupVault(backend, password) {
  if (await isInitialized(backend)) throw new Error("vault 已经初始化过了");
  if (typeof password !== "string" || password.length < 8) {
    throw new Error("主密码至少 8 位");
  }
  const salt = randomBytes(KDF_SALT_BYTES);
  const raw = await deriveRawKey(password, salt, KDF_ITERATIONS);
  const verifier = await encryptText(await importRawKey(raw), VERIFIER_PLAINTEXT);
  await backend.metaSet(META_KEY, {
    kdf: "PBKDF2-SHA256",
    iterations: KDF_ITERATIONS,
    salt: bytesToB64(salt),
    verifierIv: bytesToB64(verifier.iv),
    verifierCt: bytesToB64(verifier.ct),
    createdAt: nowIso(),
  });
  await auditAppend(backend, { event: "init", actor: "extension", detail: {} });
  return raw;
}

/** 派生并解校验器。口令对 → 返回原始密钥字节；错 → null（不泄露原因）。 */
async function deriveAndVerify(meta, password) {
  const raw = await deriveRawKey(password, b64ToBytes(meta.salt), meta.iterations ?? KDF_ITERATIONS);
  try {
    const plain = await decryptText(await importRawKey(raw), {
      iv: b64ToBytes(meta.verifierIv),
      ct: b64ToBytes(meta.verifierCt),
    });
    return plain === VERIFIER_PLAINTEXT ? raw : null;
  } catch (_) {
    return null; // GCM 认证失败 = 口令错误
  }
}

async function requireMeta(backend) {
  const meta = await backend.metaGet(META_KEY);
  if (!meta) throw new Error("vault 未初始化——先设置主密码");
  return meta;
}

/** 解锁：派生 → 解校验器 → 原始密钥进会话。口令错返回 false。 */
export async function unlock(backend, session, password) {
  const meta = await requireMeta(backend);
  const raw = await deriveAndVerify(meta, password);
  if (!raw) return false;
  await session.saveRawKey(raw);
  return true;
}

/** C1#30 reset 的存储侧：verifier 校验通过才允许清库——鉴权在 store 层，
 * UI 的 confirm() 不是认证门。审计随库消失是格式性极限（导出备份是唯一前置）。 */
export async function destroyVault(backend, session, password) {
  const meta = await requireMeta(backend);
  const raw = await deriveAndVerify(meta, password);
  if (!raw) throw new Error("主密码不正确");
  await backend.clearAll();
  await session.lock();
}

// ---------------------------------------------------------------- 审计链（C4 公式）

function sanitizeDetail(detail) {
  if (!detail) return {};
  const clean = {};
  for (const [rawKey, rawValue] of Object.entries(detail)) {
    const key = String(rawKey).toLowerCase();
    if (FORBIDDEN_DETAIL_KEYS.has(key)) {
      throw new Error(`审计 detail 里出现了机密味的键名 ${rawKey}——要记的是长度、指纹前缀、错误码`);
    }
    if (rawValue instanceof Uint8Array) {
      throw new Error(`审计 detail 的 ${rawKey} 是字节数组——传长度或 sha256 前缀`);
    }
    if (typeof rawValue === "string" && rawValue.length > MAX_DETAIL_VALUE_LEN) {
      clean[rawKey] = rawValue.slice(0, MAX_DETAIL_VALUE_LEN) + `…(+${rawValue.length - MAX_DETAIL_VALUE_LEN})`;
    } else {
      clean[rawKey] = rawValue;
    }
  }
  return clean;
}

function chainHash(prevHash, ts, event, actor, secretId, nameSnapshot, shaPrefix, detail) {
  return sha256Hex([
    prevHash, ts, event, actor,
    secretId ?? "-", nameSnapshot ?? "-", shaPrefix ?? "-",
    stableStringify(detail ?? {}),
  ].join("|"));
}

// 审计写并发互斥：append 必须「读末行 → 算链 → 写入」原子成串，否则两条并发
// append 会读到同一个 prevHash，链从第二行起永久判坏（桌面版靠调用方单事务
// 串行保证，audit.py:108；MV3 SW 的消息处理器天然可并发，这里用 promise 队列
// 补齐）。WeakMap 按后端实例各排一条队列，互不拖累。前提：同一 backend 实例
// 须被模块级复用（C2 单连接纪律）——每条消息新建实例会让跨消息互斥失效。
const auditWriteQueues = new WeakMap();

function auditQueueFor(backend) {
  let queue = auditWriteQueues.get(backend);
  if (!queue) {
    queue = Promise.resolve();
    auditWriteQueues.set(backend, queue);
  }
  return queue;
}

/** 追加一条审计行。首行 prevHash=""；shaPrefix 截到 12 位。同一后端上并发
 * 调用按调用顺序串行落库。 */
export function auditAppend(backend, { event, actor, secretId = null, nameSnapshot = null, shaPrefix = null, detail = {} }) {
  const run = auditQueueFor(backend).then(() =>
    appendAuditNow(backend, { event, actor, secretId, nameSnapshot, shaPrefix, detail }));
  // 单次失败不毒化队列：后续 append 照常排队（错误仍从本次的 run 抛给调用方）
  auditWriteQueues.set(backend, run.catch(() => {}));
  return run;
}

async function appendAuditNow(backend, { event, actor, secretId, nameSnapshot, shaPrefix, detail }) {
  const last = await backend.auditLast();
  if (shaPrefix && shaPrefix.length > SHA_PREFIX_WIDTH) {
    shaPrefix = shaPrefix.slice(0, SHA_PREFIX_WIDTH);
  }
  const clean = sanitizeDetail(detail);
  const ts = nowIso();
  const prevHash = last ? last.hash : "";
  const hash = chainHash(prevHash, ts, event, actor, secretId, nameSnapshot, shaPrefix, clean);
  await backend.auditAppend({
    ts, event, actor, secretId, nameSnapshot, shaPrefix, detail: clean, prevHash, hash,
  });
}

/** 校验整条链（全量复算，等价桌面 audit.verify）并返回最新在前的分页行。 */
export async function auditList(backend, { limit = 200 } = {}) {
  // 后端契约：auditList 最新在前。链校验要从最旧往最新复算，先反转成时间序。
  const chronological = [...(await backend.auditList(Number.MAX_SAFE_INTEGER))].reverse();
  let prevHash = "";
  let firstBadId = null;
  for (const row of chronological) {
    const expected = chainHash(prevHash, row.ts, row.event, row.actor,
      row.secretId, row.nameSnapshot, row.shaPrefix, row.detail);
    if (firstBadId === null && expected !== row.hash) firstBadId = row.id;
    prevHash = row.hash;
  }
  return {
    rows: chronological.slice(-limit).reverse().map((row) => ({
      id: row.id, ts: row.ts, event: row.event, actor: row.actor,
      secretId: row.secretId, nameSnapshot: row.nameSnapshot,
      shaPrefix: row.shaPrefix, detail: row.detail,
    })),
    chainOk: firstBadId === null,
    firstBadId,
  };
}

// ---------------------------------------------------------------- 记录写入（saveops.commit 等价）

function guardMetadata(note, sourceUrl) {
  for (const [label, text] of [["note", note], ["sourceUrl", sourceUrl]]) {
    if (text && CREDENTIAL_SMELL.test(text)) {
      throw new RefusedError(
        `${label} 里出现了看起来像凭据的内容，拒绝写入`,
        `${label} 是明文可搜索列。要存密钥请建一条正式记录`,
      );
    }
  }
}

// 名字/别名比较一律小写化：桌面 secret.name 与 alias.name 都是
// UNIQUE COLLATE NOCASE（schema.sql），名字是句柄、大小写不敏感。
function nameEquals(a, b) {
  return String(a).toLowerCase() === String(b).toLowerCase();
}

async function nameTaken(backend, name) {
  for (const record of await backend.secretAll()) {
    if (nameEquals(record.name, name)) return true;
    if ((record.aliases ?? []).some((alias) => nameEquals(alias, name))) return true;
  }
  return false;
}

/** 自动命名，对齐 suggest_name（save.py:63-79）。 */
export async function suggestName(backend, platform, keyName) {
  let base;
  if (keyName) {
    base = String(keyName)
      .replace(/[^A-Za-z0-9.-]+/g, "-")
      .replace(/_/g, "-")
      .toLowerCase();
    base = base.replace(/^[-.]+|[-.]+$/g, "").slice(0, 48) || (platform || "secret");
  } else {
    base = (platform || "secret").toLowerCase();
  }
  let candidate = base;
  let suffix = 2;
  while (await nameTaken(backend, candidate)) {
    candidate = `${base}-${suffix}`;
    suffix += 1;
  }
  return candidate;
}

async function resolveName(backend, spec) {
  if (spec.name) {
    for (const record of await backend.secretAll()) {
      if (nameEquals(record.name, spec.name)) {
        throw new AlreadyExistsError(`同名记录已存在：${spec.name}（同值会走去重，不同值请改名）`);
      }
    }
    return spec.name;
  }
  return suggestName(backend, spec.platform || "unknown", spec.keyName);
}

/** 指纹命中已有记录：合并 tags、补别名、空位回填、touch，绝不插第二行
 * （save.py:82-135）。 */
async function mergeIntoExisting(backend, existing, spec, maskParts) {
  const added = [];
  const tagSet = new Set(existing.tags ?? []);
  for (const tag of spec.tags ?? []) {
    if (!tagSet.has(tag)) {
      tagSet.add(tag);
      added.push(tag);
    }
  }

  if (spec.name && spec.name.toLowerCase() !== existing.name.toLowerCase()) {
    if (!(await nameTaken(backend, spec.name))) {
      existing.aliases = [...(existing.aliases ?? []), spec.name];
      added.push(`alias:${spec.name}`);
    }
  }

  // note/sourceUrl/expiresAt/掩码段仅在原值为空时回填（save.py:105-116）
  if (spec.note && !existing.note) existing.note = spec.note;
  if (spec.sourceUrl && !existing.sourceUrl) existing.sourceUrl = spec.sourceUrl;
  if (spec.expiresAt && !existing.expiresAt) existing.expiresAt = spec.expiresAt;
  if (maskParts[0] && !existing.maskHead) existing.maskHead = maskParts[0];
  if (maskParts[1] && !existing.maskTail) existing.maskTail = maskParts[1];
  existing.tags = [...tagSet];
  existing.updatedAt = nowIso();
  await backend.secretPut(existing);

  let warning = "";
  if (existing.status === "revoked") {
    const fps = await backend.fpListBySecret(existing.id);
    const when = (fps.find((f) => f.revokedAt) ?? {}).revokedAt ?? "";
    warning = `这个值在 ${when || "某个时点"} 被吊销过 —— 你正在重新添加一把死 key。`
      + "确认它真的重新生效了，否则去厂商控制台轮换";
  }

  await auditAppend(backend, {
    event: "dedupe_hit", actor: spec.actor ?? "extension",
    secretId: existing.id, nameSnapshot: existing.name,
    shaPrefix: existing.sha256.slice(0, SHA_PREFIX_WIDTH),
    detail: { merged: added.length, revoked_readd: Boolean(warning) },
  });
  return { outcome: "deduped", secretId: existing.id, name: existing.name, mergedTags: added, warning };
}

/**
 * 新增一条。语义对齐 saveops.commit：规范化 → 指纹 → 去重合并 → 命名 →
 * 落库 → 指纹行 → 审计。spec 的 platform/confidence/evidence/source 由调用方
 * 的检测结果给（capture/store/store-direct/importText/restore）；不可信候选的
 * 信任闸在各 C1 入口，不在这里重复。
 */
export async function add(backend, session, spec) {
  await assertUnlocked(session);
  guardMetadata(spec.note, spec.sourceUrl);

  const kind = spec.kind ?? "token";
  const canonical = canonicalize(spec.value ?? "", kind);
  if (!canonical) {
    return {
      outcome: "rejected", secretId: null, name: "", mergedTags: [],
      warning: "值为空 —— 空值表示未配置，不该进库",
    };
  }

  const sha = fingerprint(canonical, kind);
  const maskStyle = spec.maskStyle ?? inferMaskStyle(canonical);
  const maskParts = splitMask(canonical, ...maskStyle);

  const existing = await backend.secretBySha256(sha);
  if (existing) return mergeIntoExisting(backend, existing, spec, maskParts);

  const name = await resolveName(backend, spec);
  const box = await encryptText(await session.key(), canonical);
  const now = nowIso();
  // 不写 id 字段：keyPath:'id' + autoIncrement 的 store 对显式 id:null/undefined
  // 会抛 DataError（key path 求值不是合法 key），只有完全省略属性才自增。
  const record = {
    name,
    platform: spec.platform || "unknown",
    confidence: spec.confidence || "none",
    evidence: spec.evidence || "",
    detectSource: spec.source || "manual",
    keyName: spec.keyName || null,
    kind,
    valueIv: bytesToB64(box.iv),
    valueCt: bytesToB64(box.ct),
    sha256: sha,
    maskHead: maskParts[0],
    maskTail: maskParts[1],
    valueLen: canonical.length,
    status: "active",
    note: spec.note || "",
    sourceUrl: spec.sourceUrl || "",
    origin: spec.origin ?? "manual",
    extra: spec.extra ?? {},
    createdAt: now,
    updatedAt: now,
    expiresAt: spec.expiresAt || null,
    lastUsedAt: null,
    lastRevealedAt: null,
    tags: [...(spec.tags ?? [])],
    aliases: [],
  };
  const secretId = await backend.secretPut(record);
  await backend.fpPut({
    sha256: sha, secretId, status: "active", firstSeenAt: now, revokedAt: null,
  });
  await auditAppend(backend, {
    event: "save", actor: spec.actor ?? "extension",
    secretId, nameSnapshot: name, shaPrefix: sha.slice(0, SHA_PREFIX_WIDTH),
    detail: {
      platform: record.platform, confidence: record.confidence,
      len: canonical.length, origin: record.origin, tags: record.tags.length,
    },
  });
  return { outcome: "created", secretId, name, mergedTags: [], warning: "" };
}

// ---------------------------------------------------------------- 读取

/** 列表：只出掩码行，绝不带明文/密文字段（C1#7）。 */
export async function listSecrets(backend, { query = "", platform = "", status = "" } = {}) {
  const lowered = String(query ?? "").trim().toLowerCase();
  const rows = (await backend.secretAll())
    .filter((r) => (platform ? r.platform === platform : true))
    .filter((r) => (status ? r.status === status : true))
    .filter((r) => {
      if (!lowered) return true;
      // 搜索字段 = name/platform/note/keyName/sourceUrl/aliases，不含 tags
      // （对齐桌面 _query_predicate repo.py:192-202，C9#5）
      return [r.name, r.platform, r.note, r.keyName, r.sourceUrl, ...(r.aliases ?? [])]
        .some((field) => String(field ?? "").toLowerCase().includes(lowered));
    });
  // repo.py:252：createdAt DESC 再 id DESC
  rows.sort((a, b) => {
    if (a.createdAt !== b.createdAt) return a.createdAt < b.createdAt ? 1 : -1;
    return b.id - a.id;
  });
  return rows.map((r) => ({
    id: r.id, name: r.name, platform: r.platform, status: r.status,
    confidence: r.confidence, keyName: r.keyName, kind: r.kind,
    preview: preview(r.maskHead, r.maskTail),
    tags: r.tags ?? [], aliases: r.aliases ?? [],
    note: r.note, sourceUrl: r.sourceUrl, origin: r.origin, extra: r.extra ?? {},
    createdAt: r.createdAt, updatedAt: r.updatedAt, expiresAt: r.expiresAt,
    lastUsedAt: r.lastUsedAt,
  }));
}

/** 详情：记录字段（无 valueIv/valueCt）+ 指纹历史（前缀 16 位，gui/detail_window.py:31）。 */
export async function getSecret(backend, id) {
  const record = requireRecord(await backend.secretGet(id), id);
  const fps = await backend.fpListBySecret(id);
  const { valueIv, valueCt, ...safe } = record;
  void valueIv;
  void valueCt;
  return {
    ...safe,
    preview: preview(record.maskHead, record.maskTail),
    fingerprints: fps.map((f) => ({
      sha256Prefix: f.sha256.slice(0, 16),
      status: f.status,
      firstSeenAt: f.firstSeenAt,
    })),
  };
}

/** 看明文的唯一路径（桌面 vault.reveal 同语义：审计记指纹，明文只交调用方）。 */
export async function reveal(backend, session, id, { event = "reveal", actor = "extension" } = {}) {
  await assertUnlocked(session);
  const record = requireRecord(await backend.secretGet(id), id);
  const value = await decryptText(await session.key(), {
    iv: b64ToBytes(record.valueIv),
    ct: b64ToBytes(record.valueCt),
  });
  await backend.secretPut({ ...record, lastRevealedAt: nowIso() });
  await auditAppend(backend, {
    event, actor, secretId: record.id, nameSnapshot: record.name,
    shaPrefix: record.sha256.slice(0, SHA_PREFIX_WIDTH),
    detail: { len: record.valueLen },
  });
  return { record, value };
}

// ---------------------------------------------------------------- 轮换/吊销/删除/改名/平台

/** 轮换（rotate.py）：旧指纹保留 revoked + revokedAt，新指纹 active。
 * 旧密文移入指纹行——吊销的旧值仍是泄露证据（repo.py:300），且密文不存两份。 */
export async function rotate(backend, session, id, newValue, { actor = "extension" } = {}) {
  await assertUnlocked(session);
  const record = requireRecord(await backend.secretGet(id), id);
  const canonical = canonicalize(newValue ?? "", record.kind);
  if (!canonical) {
    return {
      outcome: "rejected", secretId: record.id, name: record.name, mergedTags: [],
      warning: "新值为空 —— 空值表示未配置，不该进库",
    };
  }
  const newSha = fingerprint(canonical, record.kind);
  const duplicate = await backend.secretBySha256(newSha);
  if (duplicate && duplicate.id !== record.id) {
    throw new AlreadyExistsError(
      `新值和记录 ${duplicate.name} 重复（sha256 前缀 ${newSha.slice(0, SHA_PREFIX_WIDTH)}）`,
    );
  }
  const now = nowIso();
  const fps = await backend.fpListBySecret(record.id);
  const activeFp = [...fps].reverse().find((f) => f.status === "active");
  if (!activeFp) throw new Error(`记录 ${record.name} 没有活跃的指纹行`);
  const oldSha = activeFp.sha256;
  if (oldSha === newSha) throw new Error("新值和旧值相同，无需轮换");

  await backend.fpPut({
    ...activeFp,
    status: "revoked",
    revokedAt: now,
    valueIv: record.valueIv,
    valueCt: record.valueCt,
  });
  // 掩码风格沿用原记录的头长（rotate.py:75 get_mask_head_len）
  const maskParts = splitMask(canonical, record.maskHead ? record.maskHead.length : 0, 4);
  const box = await encryptText(await session.key(), canonical);
  record.valueIv = bytesToB64(box.iv);
  record.valueCt = bytesToB64(box.ct);
  record.sha256 = newSha;
  record.maskHead = maskParts[0];
  record.maskTail = maskParts[1];
  record.valueLen = canonical.length;
  record.updatedAt = now;
  await backend.secretPut(record);
  // 新值若曾属于本记录（A→B→A 回转）：复用历史指纹行而不是再插一行——指纹
  // sha 同记录内唯一（桌面 schema.sql fingerprint.sha256 UNIQUE 同语义），其上
  // 的旧密文随行清除，新值密文由 secret 行独占。
  const historical = fps.find((f) => f.sha256 === newSha);
  if (historical) {
    const { valueIv: staleIv, valueCt: staleCt, ...reactivated } = historical;
    void staleIv;
    void staleCt;
    await backend.fpPut({ ...reactivated, status: "active", revokedAt: null });
  } else {
    await backend.fpPut({
      sha256: newSha, secretId: record.id, status: "active", firstSeenAt: now, revokedAt: null,
    });
  }
  await auditAppend(backend, {
    event: "rotate", actor, secretId: record.id, nameSnapshot: record.name,
    shaPrefix: newSha.slice(0, SHA_PREFIX_WIDTH),
    detail: { old_sha256_prefix: oldSha.slice(0, SHA_PREFIX_WIDTH), new_len: canonical.length },
  });
  return {
    secretId: record.id, name: record.name,
    oldSha256: oldSha, newSha256: newSha, oldRevokedAt: now,
  };
}

/** 吊销：值不变，status → revoked，活跃指纹同步吊销（rotate.py:90-115）。 */
export async function revoke(backend, session, id, { actor = "extension" } = {}) {
  await assertUnlocked(session);
  const record = requireRecord(await backend.secretGet(id), id);
  const now = nowIso();
  const previousStatus = record.status;
  record.status = "revoked";
  record.updatedAt = now;
  await backend.secretPut(record);
  const fps = await backend.fpListBySecret(record.id);
  const activeFp = [...fps].reverse().find((f) => f.status === "active");
  if (activeFp) {
    await backend.fpPut({ ...activeFp, status: "revoked", revokedAt: now });
  }
  await auditAppend(backend, {
    event: "revoke", actor, secretId: record.id, nameSnapshot: record.name,
    shaPrefix: record.sha256.slice(0, SHA_PREFIX_WIDTH),
    detail: { previous_status: previousStatus },
  });
  return { secretId: record.id, name: record.name };
}

/** 删除记录。审计行先写（name_snapshot 留档），历史仍可读（vault.delete）。 */
export async function deleteSecret(backend, session, id, { actor = "extension" } = {}) {
  await assertUnlocked(session);
  const record = requireRecord(await backend.secretGet(id), id);
  await auditAppend(backend, {
    event: "delete", actor, secretId: record.id, nameSnapshot: record.name,
    shaPrefix: record.sha256.slice(0, SHA_PREFIX_WIDTH),
    detail: { platform: record.platform },
  });
  await backend.secretDelete(id);
  // 级联删除指纹行（含轮换换下、带旧密文的吊销行）——桌面 foreign_keys=ON +
  // fingerprint.secret_id ON DELETE CASCADE（schema.sql）同语义，不留孤儿密文。
  await backend.fpDeleteBySecret(id);
  return { secretId: id, name: record.name };
}

/** 改名：旧名进 aliases（C1#16）；重名拒绝。 */
export async function rename(backend, session, id, newName, { actor = "extension" } = {}) {
  await assertUnlocked(session);
  const record = requireRecord(await backend.secretGet(id), id);
  for (const other of await backend.secretAll()) {
    if (nameEquals(other.name, newName)) {
      throw new AlreadyExistsError(`同名记录已存在：${newName}`);
    }
  }
  const oldName = record.name;
  record.name = newName;
  record.updatedAt = nowIso();
  if (!(record.aliases ?? []).includes(oldName)) {
    record.aliases = [...(record.aliases ?? []), oldName];
  }
  await backend.secretPut(record);
  await auditAppend(backend, {
    event: "rename", actor, secretId: record.id, nameSnapshot: newName,
    shaPrefix: record.sha256.slice(0, SHA_PREFIX_WIDTH),
    detail: { from_len: oldName.length },
  });
  return { secretId: record.id, name: newName };
}

/** 指定平台：confidence=manual、evidence 固定文案；remember 非空时写纠正规则
 * （C1#17）。 */
export async function setPlatform(backend, session, id, platform, { remember = null, actor = "extension" } = {}) {
  await assertUnlocked(session);
  const record = requireRecord(await backend.secretGet(id), id);
  record.platform = platform;
  record.confidence = "manual";
  record.detectSource = "correction";
  record.evidence = "用户在扩展里指定";
  record.updatedAt = nowIso();
  await backend.secretPut(record);
  await auditAppend(backend, {
    event: "platform_set", actor, secretId: record.id, nameSnapshot: record.name,
    shaPrefix: record.sha256.slice(0, SHA_PREFIX_WIDTH),
    detail: { platform },
  });
  if (remember) {
    await addCorrection(backend, {
      kind: remember.kind, pattern: remember.pattern, platform,
      note: "浏览器扩展里记住",
    });
  }
  return { secretId: record.id, platform };
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

// ---------------------------------------------------------------- 批量导入

/** 粘贴文本批量导入（importer.py 浏览器等价）。检测一律经 detect.js——本文件
 * 不自行组装 parse/resolve。只入 promptable 候选（C4/C9#2 有意分歧：桌面对
 * 不可信候选 force 落盘，浏览器改为 skipped 列表反馈，skipped 不含值本身，
 * 只有掩码预览）；每批审计一条 import。 */
export async function importText(backend, session, text, { tags = [], actor = "extension" } = {}) {
  await assertUnlocked(session);
  const findings = await detectText(backend, String(text ?? ""), { sourceHint: "import" });
  const summary = { created: 0, deduped: 0, rejected: 0, skipped: [] };
  for (const finding of findings) {
    if (!finding.promptable) {
      summary.skipped.push({
        platform: finding.platform,
        confidence: finding.confidence,
        preview: finding.preview,
      });
      continue;
    }
    const result = await add(backend, session, {
      value: finding.value, kind: finding.kind, keyName: finding.keyName,
      platform: finding.platform, confidence: finding.confidence,
      evidence: finding.evidence, source: finding.source,
      maskStyle: finding.maskStyle, origin: "import", tags, actor,
    });
    if (result.outcome === "created") summary.created += 1;
    else if (result.outcome === "deduped") summary.deduped += 1;
    else summary.rejected += 1;
  }
  await auditAppend(backend, {
    event: "import", actor,
    detail: {
      source: "import", created: summary.created, deduped: summary.deduped,
      rejected: summary.rejected, total_candidates: findings.length,
    },
  });
  return summary;
}

// ---------------------------------------------------------------- 过期与统计

/** 把 expiresAt 已过且仍 active 的记录翻成 expired（kv/ops/expiry.py）。 */
export async function markExpired(backend) {
  const now = nowIso();
  let flipped = 0;
  for (const record of await backend.secretAll()) {
    if (record.status !== "active" || !record.expiresAt || record.expiresAt > now) continue;
    record.status = "expired";
    record.updatedAt = now;
    await backend.secretPut(record);
    for (const fp of await backend.fpListBySecret(record.id)) {
      if (fp.status === "active") await backend.fpPut({ ...fp, status: "expired" });
    }
    await auditAppend(backend, {
      event: "expire", actor: "extension", secretId: record.id,
      nameSnapshot: record.name, shaPrefix: record.sha256.slice(0, SHA_PREFIX_WIDTH),
      detail: { expiresAt: record.expiresAt },
    });
    flipped += 1;
  }
  return { flipped };
}

export async function stats(backend) {
  const all = await backend.secretAll();
  const soon = new Date(Date.now() + EXPIRING_DAYS * 24 * 3600 * 1000).toISOString();
  const platforms = {};
  for (const record of all) {
    if (record.status !== "active") continue;
    platforms[record.platform] = (platforms[record.platform] ?? 0) + 1;
  }
  return {
    total: all.length,
    active: all.filter((r) => r.status === "active").length,
    flagged: all.filter((r) => r.status === "revoked" || r.status === "expired").length,
    expiring: all.filter((r) => r.status === "active" && r.expiresAt && r.expiresAt <= soon).length,
    platforms: Object.entries(platforms)
      .sort((a, b) => b[1] - a[1])
      .map(([platform, count]) => ({ platform, count })),
  };
}

// ---------------------------------------------------------------- 备份（KVBE1，C5）

/** 全量明文导出（口令加密整包）。格式 "KVBE1|"+b64(salt16)+"|"+b64(iv12)+"|"+b64(ct)。
 * payload=stableStringify({version:1, exportedAt, records})，records 只带元数据
 * 与明文值，不含任何 valueIv/valueCt 密文字段。口令 ≥8 位（backup.py:100-105）。 */
export async function exportBackup(backend, session, password) {
  await assertUnlocked(session);
  if (typeof password !== "string" || password.length < 8) {
    throw new Error("备份口令至少 8 位");
  }
  const key = await session.key();
  const records = [];
  for (const record of await backend.secretAll()) {
    const value = await decryptText(key, {
      iv: b64ToBytes(record.valueIv), ct: b64ToBytes(record.valueCt),
    });
    records.push({
      name: record.name,
      value,
      kind: record.kind,
      platform: record.platform,
      keyName: record.keyName ?? "",
      tags: record.tags ?? [],
      note: record.note,
      sourceUrl: record.sourceUrl,
      status: record.status,
      expiresAt: record.expiresAt ?? "",
      createdAt: record.createdAt,
      lastUsedAt: record.lastUsedAt,
      lastRevealedAt: record.lastRevealedAt,
    });
  }
  const payload = stableStringify({ version: 1, exportedAt: nowIso(), records });
  const salt = randomBytes(KDF_SALT_BYTES);
  const box = await encryptText(
    await importRawKey(await deriveRawKey(password, salt, KDF_ITERATIONS)), payload,
  );
  await auditAppend(backend, {
    event: "export", actor: "extension", detail: { count: records.length },
  });
  return [BACKUP_MAGIC, bytesToB64(salt), bytesToB64(box.iv), bytesToB64(box.ct)].join("|");
}

/** 恢复：口令解包 → 逐条走 add（去重生效），不做检测（C5）。
 * 错口令在解包时就失败，此时还没有任何写入。 */
export async function restoreBackup(backend, session, password, blobText, { actor = "extension" } = {}) {
  await assertUnlocked(session);
  const parts = String(blobText ?? "").trim().split("|");
  if (parts[0] !== BACKUP_MAGIC || parts.length !== 4) {
    throw new Error("不是 KeyVault 浏览器版备份（KVBE1）格式");
  }
  let payloadText;
  try {
    payloadText = await decryptText(
      await importRawKey(await deriveRawKey(password, b64ToBytes(parts[1]), KDF_ITERATIONS)),
      { iv: b64ToBytes(parts[2]), ct: b64ToBytes(parts[3]) },
    );
  } catch (_) {
    throw new Error("口令错误或备份已损坏（AES-GCM 认证失败）");
  }
  let payload;
  try {
    payload = JSON.parse(payloadText);
  } catch (error) {
    throw new Error(`备份内容损坏：${error.message}`);
  }
  const records = payload.records;
  if (!Array.isArray(records)) throw new Error("备份内容损坏：缺少 records");

  const summary = { created: 0, deduped: 0, rejected: 0 };
  for (const rec of records) {
    const result = await add(backend, session, {
      value: rec.value, kind: rec.kind || "token", keyName: rec.keyName || null,
      platform: rec.platform || "unknown", confidence: "manual",
      evidence: "备份恢复", source: "restore", name: rec.name || "",
      tags: Array.isArray(rec.tags) ? rec.tags : [],
      note: rec.note || "", sourceUrl: rec.sourceUrl || "",
      expiresAt: rec.expiresAt || null, origin: "restore", actor,
    });
    if (result.outcome === "created") summary.created += 1;
    else if (result.outcome === "deduped") summary.deduped += 1;
    else summary.rejected += 1;
  }
  await auditAppend(backend, {
    event: "restore", actor, detail: { created: summary.created, deduped: summary.deduped },
  });
  return summary;
}
