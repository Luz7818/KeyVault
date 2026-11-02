// KeyVault 独立版 service worker —— 消息面 + 检测引擎 + vault 会话 + 自动锁定 + TTL 擦除。
//
// 与桌面宿主（kv/host/service.py）的消息面对位，但检测与存储都在本 SW 里完成
// （浏览器版没有 Python 宿主）。三条设计纪律：
// - **SW 无状态**：pending 候选不驻留内存（MV3 SW 随时被回收）——提示条上的
//   [存入] 把原文再发一次，SW 重新检测（纯函数、确定性）后入库。
// - **派生密钥只存 chrome.storage.session**：浏览器会话内有效、重启即失、
//   锁定=删除；这是 MV3 密码管理器的通行做法。
// - 捕获的 promptable 口径 = trusted 且平台非 unknown（含 ambiguous →
//   openai-compatible 诚实落点）：浏览器版没有桌面审批队列，可信歧义也该有
//   「存入」的出路，存后可改平台。
//
// 页面 copy 事件还有第二用途：作为「剪贴板已换新」信号取消在飞的 TTL 擦除——
// 恢复桌面版「不覆盖新内容」的核心安全性质（浏览器无法按指纹比对）。

import { Session } from "./src/vault/crypto.js";
import * as store from "./src/vault/store.js";
import { idbBackend } from "./src/vault/db.js";
import { parse } from "./src/core/parse.js";
import { resolve, isTrusted } from "./src/core/resolve.js";
import { CorrectionSet } from "./src/core/corrections.js";
import { parseSeed, generate } from "./src/core/totp.js";

const backend = idbBackend();
const session = new Session(chromeSessionKeyStore());

const DEFAULT_SETTINGS = { autoLockMinutes: 15, captureEnabled: true, ignoredHosts: [] };
const WIPE_TTL_SECONDS = 30;
const PENDING_WIPE_KEY = "kv-pending-wipe";
const LAST_ACTIVE_KEY = "kv-last-active";

// ---------------------------------------------------------------- 基础设施

function chromeSessionKeyStore() {
  return {
    async get(k) {
      const o = await chrome.storage.session.get(k);
      return o[k] ?? null;
    },
    async set(k, v) { await chrome.storage.session.set({ [k]: v }); },
    async remove(k) { await chrome.storage.session.remove(k); },
  };
}

async function getSettings() {
  const saved = (await backend.metaGet("settings")) ?? {};
  return { ...DEFAULT_SETTINGS, ...saved };
}

async function setSettings(patch) {
  const current = await getSettings();
  await backend.metaSet("settings", { ...current, ...patch });
  await chrome.alarms.create("kv-autolock", { periodInMinutes: 1 });
}

async function touchActivity() {
  await chrome.storage.session.set({ [LAST_ACTIVE_KEY]: Date.now() });
  await chrome.alarms.create("kv-autolock", { periodInMinutes: 1 });
}

async function maybeAutoLock() {
  const { autoLockMinutes } = await getSettings();
  if (autoLockMinutes <= 0) return;
  if (!(await session.isUnlocked())) return;
  const o = await chrome.storage.session.get(LAST_ACTIVE_KEY);
  const lastActive = o[LAST_ACTIVE_KEY] ?? 0;
  if (Date.now() - lastActive > autoLockMinutes * 60 * 1000) {
    await session.lock();
  }
}

// ---------------------------------------------------------------- 捕获与判定

async function correctionsFor() {
  return CorrectionSet.fromRows(await store.listCorrections(backend));
}

async function detectText(text, title, sourceUrl) {
  const result = parse(text, { sourceHint: "extension" });
  const corrections = await correctionsFor();
  const originHost = sourceUrl ? safeHostname(sourceUrl) : "";
  const out = [];
  for (const cand of result.candidates) {
    const verdict = resolve(cand, corrections, { windowTitle: title || null });
    out.push({
      value: cand.value,
      keyName: cand.keyName,
      kind: cand.kind,
      platform: verdict.platform,
      confidence: verdict.confidence,
      evidence: verdict.evidence,
      maskStyle: verdict.maskStyle,
      preview: cand.preview("*", ...(verdict.maskStyle ?? [0, 4])),
      promptable: isTrusted(verdict) && verdict.platform !== "unknown",
    });
  }
  void originHost;
  return out;
}

function safeHostname(url) {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch (_) {
    return "";
  }
}

// ---------------------------------------------------------------- TTL 擦除

async function scheduleWipe() {
  const alarmName = `wipe-${Date.now()}`;
  await chrome.storage.session.set({
    [PENDING_WIPE_KEY]: { alarmName, at: Date.now() },
  });
  await chrome.alarms.create(alarmName, { when: Date.now() + WIPE_TTL_SECONDS * 1000 });
}

async function cancelWipe() {
  const o = await chrome.storage.session.get(PENDING_WIPE_KEY);
  const pending = o[PENDING_WIPE_KEY];
  if (pending) {
    await chrome.alarms.clear(pending.alarmName);
    await chrome.storage.session.remove(PENDING_WIPE_KEY);
  }
}

async function performWipe() {
  const o = await chrome.storage.session.get(PENDING_WIPE_KEY);
  if (!o[PENDING_WIPE_KEY]) return;
  await chrome.storage.session.remove(PENDING_WIPE_KEY);
  try {
    await ensureOffscreen();
    await chrome.runtime.sendMessage({ target: "offscreen", kind: "wipe-clipboard" });
  } catch (_) {
    // offscreen 不可用（老浏览器等）：放弃本次擦除，宁可不擦也不错擦
  }
}

async function ensureOffscreen() {
  const context = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"],
  });
  if (context.length > 0) return;
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["CLIPBOARD"],
    justification: "到期的剪贴板自动擦除（桌面版同款 30 秒语义）",
  });
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "kv-autolock") {
    maybeAutoLock().catch(() => {});
  } else if (alarm.name.startsWith("wipe-")) {
    performWipe().catch(() => {});
  }
});

// ---------------------------------------------------------------- 消息面

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.target === "offscreen") return false; // 给 offscreen 页自己
  handleMessage(msg, sender).then(sendResponse).catch((error) => {
    sendResponse({ error: error?.message ?? String(error) });
  });
  return true; // sendResponse 异步
});

async function handleMessage(msg) {
  if (!msg || typeof msg.kind !== "string") return { error: "坏消息" };
  const handler = HANDLERS[msg.kind];
  if (!handler) return { error: `未知请求类型：${msg.kind}` };
  try {
    const response = await handler(msg);
    await touchActivity();
    return response;
  } catch (error) {
    if (error?.name === "LockedError") {
      return { error: error.message, locked: true };
    }
    return { error: error?.message ?? String(error) };
  }
}

const HANDLERS = {
  async status() {
    const [initialized, unlocked, settings] = await Promise.all([
      store.isInitialized(backend), session.isUnlocked(), getSettings(),
    ]);
    return { initialized, unlocked, settings, version: chrome.runtime.getManifest().version };
  },

  async setup(msg) {
    if (typeof msg.password !== "string" || msg.password.length < 8) {
      return { error: "主密码至少 8 位" };
    }
    await store.setupVault(backend, msg.password);
    await store.unlock(backend, session, msg.password);
    await setSettings(DEFAULT_SETTINGS);
    return { ok: true };
  },

  async unlock(msg) {
    const ok = await store.unlock(backend, session, String(msg.password ?? ""));
    if (!ok) return { error: "主密码不正确", locked: true };
    await touchActivity();
    return { ok: true };
  },

  async lock() {
    await session.lock();
    return { ok: true };
  },

  async capture(msg) {
    const settings = await getSettings();
    if (!settings.captureEnabled) return { disabled: true };
    const host = safeHostname(msg.sourceUrl ?? "");
    if (host && settings.ignoredHosts.includes(host)) return { ignored: true };

    const results = await detectText(String(msg.text ?? ""), msg.title, msg.sourceUrl);
    const promptable = results.filter((r) => r.promptable);
    if (promptable.length !== 1) {
      return { results: results.map(({ value, ...rest }) => rest) }; // 0 条或多条：页面不弹
    }
    const { value, ...safe } = promptable[0];
    return { candidate: safe, value }; // value 回给 content script，随 [存入] 再发回
  },

  async store(msg) {
    // [存入]：拿原文重新检测（确定性），与提示条展示一致后入库。
    const results = await detectText(String(msg.text ?? ""), msg.title, msg.sourceUrl);
    const promptable = results.filter((r) => r.promptable);
    if (promptable.length !== 1) return { error: "候选已变化，请重新复制" };
    const cand = promptable[0];
    const result = await store.add(backend, session, {
      value: cand.value, kind: cand.kind, keyName: cand.keyName,
      platform: cand.platform, confidence: cand.confidence,
      evidence: cand.evidence, source: "extension",
      sourceUrl: String(msg.sourceUrl ?? ""), maskStyle: cand.maskStyle,
      origin: "extension", actor: "extension",
    });
    if (result.outcome === "rejected") return { error: result.warning };
    return { ok: true, outcome: result.outcome, name: result.name,
             warning: result.warning ?? "" };
  },

  async list(msg) {
    const rows = await store.listSecrets(backend, {
      query: msg.query ?? "", platform: msg.platform ?? "", status: msg.status ?? "",
    });
    return { rows };
  },

  async get(msg) {
    return { record: await store.getSecret(backend, msg.id) };
  },

  async reveal(msg) {
    const { value, record } = await store.reveal(backend, session, msg.id, {
      event: msg.event ?? "reveal", actor: "extension",
    });
    return { value, name: record.name };
  },

  async totp(msg) {
    // 详情页已持有种子时的本地计算（避免每秒轮询打爆审计）；无种子则解一次。
    let seedBytes;
    if (msg.seedB64) {
      seedBytes = Uint8Array.from(atob(msg.seedB64), (c) => c.charCodeAt(0));
    } else {
      const { value } = await store.reveal(backend, session, msg.id, { event: "totp" });
      seedBytes = parseSeed(value);
    }
    const [code, remaining] = await generate(seedBytes, new Date());
    return { code, remaining };
  },

  async copied() {
    // 复制已发生：安排 TTL 擦除。
    await scheduleWipe();
    return { ok: true, ttl: WIPE_TTL_SECONDS };
  },

  async "clipboard-changed"() {
    // 页面上发生了新的复制：取消在飞的擦除（不覆盖新内容——桌面版指纹语义的浏览器等价）。
    await cancelWipe();
    return { ok: true };
  },

  async rotate(msg) {
    const result = await store.rotate(backend, session, msg.id, String(msg.value ?? ""));
    if (result.outcome === "rejected") return { error: result.warning };
    return { ok: true };
  },

  async revoke(msg) { await store.revoke(backend, msg.id); return { ok: true }; },

  async delete(msg) { await store.deleteSecret(backend, msg.id); return { ok: true }; },

  async rename(msg) {
    await store.rename(backend, msg.id, String(msg.name ?? ""));
    return { ok: true };
  },

  async "set-platform"(msg) {
    await store.setPlatform(backend, msg.id, String(msg.platform ?? ""), {
      remember: msg.remember ?? null,
    });
    return { ok: true };
  },

  async corrections() {
    return { rows: await store.listCorrections(backend) };
  },

  async "delete-correction"(msg) {
    await store.deleteCorrection(backend, msg.id);
    return { ok: true };
  },

  async "import-text"(msg) {
    const summary = await store.importText(backend, session, String(msg.text ?? ""),
      { tags: msg.tags ?? [] });
    await store.markExpired(backend);
    return summary;
  },

  async "mark-expired"() {
    return { flipped: await store.markExpired(backend) };
  },

  async stats() {
    return await store.stats(backend);
  },

  async audit(msg) {
    return await store.auditList(backend, { limit: msg.limit ?? 200 });
  },

  async "export-backup"(msg) {
    const blob = await store.exportBackup(backend, session, String(msg.password ?? ""));
    return { blob };
  },

  async "restore-backup"(msg) {
    const summary = await store.restoreBackup(backend, session,
      String(msg.password ?? ""), String(msg.blob ?? ""));
    return summary;
  },

  async "get-settings"() {
    return { settings: await getSettings() };
  },

  async "set-settings"(msg) {
    await setSettings(msg.patch ?? {});
    return { ok: true, settings: await getSettings() };
  },
};
