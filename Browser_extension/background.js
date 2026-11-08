// KeyVault MV3 service worker —— 只做编排（CONTRACTS.md C1/C7）。
//
// 本文件没有一行检测/加密/存储逻辑：检测一律调 src/vault/detect.js 的
// detectText（C4 唯一组合入口），其余全部落在 src/vault/。职责只有四件：
// ① C1 全表 30 个动作的消息分发；② 捕获调度（开关/忽略站点 → 弹条或掩码行，
// 候选绝不驻留 SW 内存——入库走 store 的重新检测，纯函数确定性）；
// ③ 自动锁定（每分钟 alarm 检查 kv-last-active）与冷启动 sweep（pending
// wipe 安全网 + 过期标记，对齐桌面 security.py:98「标记过期记录」与
// cli.py expire 的常驻触发点）；④ 剪贴板 TTL 擦除的 alarm 调度与 offscreen
// 文档的创建/复用、消息转发（擦除页面本身归 app-package）。
//
// 桌面 kv/host/ 的 native messaging 宿主与本模块无关：检测与存储全在 SW
// 本地完成，无 Python、无宿主进程。

import { b64ToBytes, LockedError, Session } from "./src/vault/crypto.js";
import { idbBackend } from "./src/vault/db.js";
import { memoryBackend } from "./src/vault/memory-backend.js";
import { detectText } from "./src/vault/detect.js";
import { scanTexts } from "./src/vault/scan.js";
import { parseSeed, SeedError, generate as totpGenerate } from "./src/vault/totp.js";
import * as store from "./src/vault/store.js";

const AUTOLOCK_ALARM = "kv-autolock";
const AUTOLOCK_PERIOD_MINUTES = 1;
const WIPE_TTL_SECONDS = 30;
const WIPE_ALARM_PREFIX = "wipe-";
const LAST_ACTIVE_KEY = "kv-last-active";
const PENDING_WIPE_KEY = "kv-pending-wipe";
const SETTINGS_META_KEY = "settings";
const OFFSCREEN_URL = "offscreen.html";
const REVEAL_EVENTS = ["reveal", "copy", "totp"];
// C7 的默认设置：与 meta 键 "settings" 里存的补丁浅合并
const DEFAULT_SETTINGS = { autoLockMinutes: 15, captureEnabled: true, ignoredHosts: [] };

// C2 单连接纪律：后端实例模块级创建并复用（node 测试无 indexedDB，
// 自动落到内存后端——IndexedDB 后端的真机行为靠浏览器走查，已声明边界）。
const backend = typeof indexedDB === "undefined" ? memoryBackend() : idbBackend();

// 解锁后的原始密钥字节存 chrome.storage.session（浏览器会话结束即清空，
// 锁定 = 删除）——验收检查点：session 里只有 raw key 的 b64、last-active
// 与 pending wipe 元数据，绝无明文值。
const session = new Session({
  async get(key) {
    const bag = await chrome.storage.session.get(key);
    return key in bag ? bag[key] : null;
  },
  async set(key, value) {
    await chrome.storage.session.set({ [key]: value });
  },
  async remove(key) {
    await chrome.storage.session.remove(key);
  },
});

// ---------------------------------------------------------------- 状态辅助

/** 每处理一条消息刷新活动时间戳——自动锁定只认「最近一次扩展交互」。 */
function touchActivity() {
  return chrome.storage.session.set({ [LAST_ACTIVE_KEY]: Date.now() });
}

async function getLastActive() {
  const bag = await chrome.storage.session.get(LAST_ACTIVE_KEY);
  return typeof bag[LAST_ACTIVE_KEY] === "number" ? bag[LAST_ACTIVE_KEY] : null;
}

async function readSettings() {
  const stored = await backend.metaGet(SETTINGS_META_KEY);
  return { ...DEFAULT_SETTINGS, ...(stored ?? {}) };
}

/** 从 capture 的 source_url 提取主机名（小写）；解析不了返回空串——
 * 无从谈起主机名时按不忽略处理，宁可多弹一次条也不静默吞掉捕获。 */
function sourceHostname(sourceUrl) {
  if (typeof sourceUrl !== "string" || sourceUrl === "") return "";
  try {
    return new URL(sourceUrl).hostname.toLowerCase();
  } catch (_) {
    return "";
  }
}

/** C7：ignoredHosts 是主机名小写精确匹配。 */
function isIgnoredHost(settings, sourceUrl) {
  const host = sourceHostname(sourceUrl);
  return host !== "" && settings.ignoredHosts.includes(host);
}

/** 掩码行：candidate 与 results 共用形状，绝不带 value（C0）。 */
function maskRow(finding) {
  return {
    platform: finding.platform,
    keyName: finding.keyName,
    kind: finding.kind,
    confidence: finding.confidence,
    evidence: finding.evidence,
    maskStyle: finding.maskStyle,
    preview: finding.preview,
  };
}

// ---------------------------------------------------------------- 擦除调度（C7）

async function readPendingWipe() {
  const bag = await chrome.storage.session.get(PENDING_WIPE_KEY);
  return bag[PENDING_WIPE_KEY] ?? null;
}

/** copied：清掉旧拷贝 → 记 pending → 排一个 30 秒单次 alarm。 */
async function scheduleWipe() {
  const previous = await readPendingWipe();
  if (previous) await chrome.alarms.clear(previous.alarmName);
  const at = Date.now();
  const deadlineAt = at + WIPE_TTL_SECONDS * 1000;
  const alarmName = `${WIPE_ALARM_PREFIX}${at}`;
  await chrome.storage.session.set({ [PENDING_WIPE_KEY]: { alarmName, at, deadlineAt } });
  chrome.alarms.create(alarmName, { when: deadlineAt });
  return { ok: true, ttl: WIPE_TTL_SECONDS };
}

/** clipboard-changed：取消在飞擦除（清 alarm + 清 pending）。 */
async function cancelWipe() {
  const pending = await readPendingWipe();
  if (!pending) return { ok: true };
  await chrome.alarms.clear(pending.alarmName);
  await chrome.storage.session.remove(PENDING_WIPE_KEY);
  return { ok: true };
}

async function ensureOffscreen() {
  const contexts = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"],
    documentUrls: [chrome.runtime.getURL(OFFSCREEN_URL)],
  });
  if (Array.isArray(contexts) && contexts.length > 0) return;
  await chrome.offscreen.createDocument({
    url: OFFSCREEN_URL,
    reasons: ["CLIPBOARD"],
    justification: "30 秒 TTL 到期后把剪贴板覆盖为无密文的安全提示文本",
  });
}

/** 把擦除指令送进 offscreen 文档。失败返回 false——宁可不擦，也不错擦；
 * pending 留在 session 里，由启动 sweep 补擦（wipe.py:15-19 安全网等价）。 */
async function deliverWipe() {
  try {
    await ensureOffscreen();
    const response = await chrome.runtime.sendMessage({ target: "offscreen", kind: "wipe-clipboard" });
    return Boolean(response && response.ok);
  } catch (error) {
    console.warn("剪贴板擦除未送达：", error?.message ?? error);
    return false;
  }
}

/** wipe alarm 到点：pending 还在（没被 clipboard-changed 取消、没被更新的
 * 拷贝取代）才擦，成功后清 pending。 */
async function runWipeAlarm(alarmName) {
  const pending = await readPendingWipe();
  if (!pending || pending.alarmName !== alarmName) return;
  if (!(await deliverWipe())) return;
  await chrome.storage.session.remove(PENDING_WIPE_KEY);
}

// ---------------------------------------------------------------- 自动锁定与启动 sweep（C7）

async function enforceAutolock() {
  const settings = await readSettings();
  const minutes = Number(settings.autoLockMinutes);
  if (!minutes || minutes <= 0) return; // 0 = 禁用
  const lastActive = await getLastActive();
  if (lastActive === null) return; // 浏览器会话刚开始，还没有活动痕迹
  if (Date.now() - lastActive < minutes * 60000) return;
  await session.lock();
}

/** SW 冷启动 sweep，顶层执行（含 onStartup 时机）：
 * ① pending wipe 安全网——扩展重载会清掉全部 alarms，这里按 deadline
 *   补挂未到期的、立即补擦已到期的（wipe.py:15-19「下次调用时清扫」）；
 * ② store.markExpired——桌面「标记过期记录」按钮（security.py:98）与
 *   kv expire（cli.py:321-323）的常驻触发点，没有它 expired 永远数不进
 *   仪表盘。解锁成功后 unlock 处理器还会再跑一次（C1#3）。 */
export async function runStartupSweep() {
  const pending = await readPendingWipe();
  if (pending) {
    if (pending.deadlineAt <= Date.now()) {
      if (await deliverWipe()) await chrome.storage.session.remove(PENDING_WIPE_KEY);
    } else {
      chrome.alarms.create(pending.alarmName, { when: pending.deadlineAt });
    }
  }
  await store.markExpired(backend);
}

// ---------------------------------------------------------------- 消息处理器（C1 全表）

function finishAdd(result) {
  if (result.outcome === "rejected") return { error: result.warning };
  return { ok: true, outcome: result.outcome, name: result.name, warning: result.warning };
}

async function hStatus() {
  return {
    initialized: await store.isInitialized(backend),
    unlocked: await session.isUnlocked(),
    settings: await readSettings(),
    version: chrome.runtime.getManifest().version,
  };
}

async function hSetup(msg) {
  const raw = await store.setupVault(backend, msg.password);
  // 设完即解锁：capture → store 的走查流程里没有任何中间解锁步
  await session.saveRawKey(raw);
  return { ok: true };
}

async function hUnlock(msg) {
  const ok = await store.unlock(backend, session, msg.password);
  if (!ok) return { error: "主密码不正确", locked: true };
  await store.markExpired(backend); // C1#3：解锁成功后补一次过期标记
  return { ok: true };
}

async function hLock() {
  await session.lock();
  return { ok: true };
}

async function hCapture(msg) {
  const settings = await readSettings();
  if (!settings.captureEnabled) return { disabled: true };
  if (isIgnoredHost(settings, msg.source_url)) return { ignored: true };
  // 检测唯一入口（C4）；pageTitle 喂 resolve 的第 5 级窗口/页面信号
  const text = String(msg.text ?? "");
  const findings = await detectText(backend, text, {
    pageTitle: msg.title ?? null,
  });
  const promptable = findings.filter((finding) => finding.promptable);
  if (promptable.length === 1) {
    // value 是捕获原文全文（C1#5）：content 把它原样回传给 store 重新检测，
    // 「纯函数确定性」保证落库判定与弹条判定完全一致——如果只回裸值，捕获时
    // 靠键名/主机名拿到的 verdict 会在重检测时丢失（deepseek/high 漂成
    // openai-compatible/ambiguous）。桌面对位：review.decide 直接以捕获时
    // verdict 落盘（kv/ops/review.py:146-148），本设计是浏览器无状态形态下的
    // 同效等价物。
    return { candidate: maskRow(promptable[0]), value: text };
  }
  // 0 条或多条：回掩码行不弹条（promptable 口径 C9#1，检测层已放宽）
  return { results: findings.map(maskRow) };
}

async function hStore(msg) {
  // SW 无状态：入库前拿原文重新检测（纯函数确定性），候选绝不驻留内存。
  // 前提：capture 的 value 是捕获原文全文（见 hCapture），content 原样回传。
  const findings = await detectText(backend, String(msg.text ?? ""), {
    pageTitle: msg.title ?? null,
  });
  const promptable = findings.filter((finding) => finding.promptable);
  if (promptable.length === 0) return { error: "没有识别到可信的密钥候选" };
  if (promptable.length > 1) {
    return { error: `检测到 ${promptable.length} 条候选，无法确定入库哪一条` };
  }
  const finding = promptable[0];
  return finishAdd(await store.add(backend, session, {
    value: finding.value,
    kind: finding.kind,
    keyName: finding.keyName,
    platform: finding.platform,
    confidence: finding.confidence,
    evidence: finding.evidence,
    // C1#6 承诺 detectSource="extension"（消息面契约，下游按它接）；桌面
    // kv/ops/save.py:158 存的是 verdict 阶段名——此字段语义分歧建议入 C9
    source: "extension",
    maskStyle: finding.maskStyle,
    origin: "extension",
    actor: "extension",
    sourceUrl: msg.source_url ?? "",
  }));
}

async function hList(msg) {
  return {
    rows: await store.listSecrets(backend, {
      query: msg.query ?? "",
      platform: msg.platform ?? "",
      status: msg.status ?? "",
    }),
  };
}

async function hGet(msg) {
  return { record: await store.getSecret(backend, msg.id) };
}

async function hReveal(msg) {
  const event = msg.event ?? "reveal";
  if (!REVEAL_EVENTS.includes(event)) return { error: `非法事件类型：${event}` };
  const { value, record } = await store.reveal(backend, session, msg.id, { event });
  return { value, name: record.name };
}

async function hTotp(msg) {
  // 带 seedB64 不解密、不审计（C1#10）
  if (msg.seedB64) {
    return totpGenerate(b64ToBytes(String(msg.seedB64)));
  }
  const { value } = await store.reveal(backend, session, msg.id, { event: "totp" });
  try {
    return await totpGenerate(parseSeed(value));
  } catch (error) {
    if (error instanceof SeedError) return { error: error.message };
    throw error;
  }
}

async function hRotate(msg) {
  const result = await store.rotate(backend, session, msg.id, msg.value);
  if (result.outcome === "rejected") return { error: result.warning };
  return { ok: true };
}

async function hRevoke(msg) {
  await store.revoke(backend, session, msg.id);
  return { ok: true };
}

async function hDelete(msg) {
  await store.deleteSecret(backend, session, msg.id);
  return { ok: true };
}

async function hRename(msg) {
  await store.rename(backend, session, msg.id, msg.name);
  return { ok: true };
}

async function hSetPlatform(msg) {
  await store.setPlatform(backend, session, msg.id, msg.platform, {
    remember: msg.remember ?? null,
  });
  return { ok: true };
}

async function hCorrections() {
  return { rows: await store.listCorrections(backend) };
}

async function hDeleteCorrection(msg) {
  await store.deleteCorrection(backend, msg.id);
  return { ok: true };
}

async function hImportText(msg) {
  return store.importText(backend, session, msg.text, {
    tags: Array.isArray(msg.tags) ? msg.tags : [],
  });
}

async function hMarkExpired() {
  return store.markExpired(backend);
}

async function hStats() {
  return store.stats(backend);
}

async function hAudit(msg) {
  return store.auditList(backend, { limit: msg.limit ?? 200 });
}

async function hExportBackup(msg) {
  return { blob: await store.exportBackup(backend, session, msg.password) };
}

async function hRestoreBackup(msg) {
  return store.restoreBackup(backend, session, msg.password, msg.blob);
}

async function hGetSettings() {
  return { settings: await readSettings() };
}

/** settings 形状冻结为 C7 的三字段；SW 侧校验与 options 的 clampAutoLock
 * 同口径（autoLockMinutes = 0..240 整数，0=禁用），但取拒绝而非静默收敛——
 * 非法值让调用方看到 {error}，而不是悄悄把自动锁定关掉。 */
async function hSetSettings(msg) {
  if (!msg.patch || typeof msg.patch !== "object" || Array.isArray(msg.patch)) {
    return { error: "patch 必须是对象" };
  }
  const KNOWN_FIELDS = new Set(["autoLockMinutes", "captureEnabled", "ignoredHosts"]);
  for (const field of Object.keys(msg.patch)) {
    if (!KNOWN_FIELDS.has(field)) return { error: `未知设置字段：${field}` };
  }
  const merged = { ...(await readSettings()), ...msg.patch };
  if (typeof merged.captureEnabled !== "boolean") {
    return { error: "captureEnabled 必须是布尔值" };
  }
  const minutes = Number(merged.autoLockMinutes);
  if (!Number.isInteger(minutes) || minutes < 0 || minutes > 240) {
    return { error: "autoLockMinutes 必须是 0..240 的整数（0=禁用）" };
  }
  if (!Array.isArray(merged.ignoredHosts)) {
    return { error: "ignoredHosts 必须是主机名数组" };
  }
  // C7：主机名小写精确匹配——写入时归一化，比较端就不用再防大小写
  merged.ignoredHosts = merged.ignoredHosts
    .map((host) => String(host).trim().toLowerCase())
    .filter((host) => host !== "");
  merged.autoLockMinutes = minutes;
  await backend.metaSet(SETTINGS_META_KEY, merged);
  return { ok: true, settings: merged };
}

async function hScanTexts(msg) {
  return { hits: await scanTexts(backend, session, msg.texts) };
}

async function hStoreDirect(msg) {
  const value = typeof msg.value === "string" ? msg.value : "";
  const name = msg.name ?? "";
  const note = msg.note ?? "";
  const keyName = msg.keyName ?? null;
  if (msg.platform) {
    // 显式平台 = 用户拍板，对齐 set-platform 的人工语义
    return finishAdd(await store.add(backend, session, {
      value, name, note, keyName, platform: msg.platform,
      confidence: "manual", evidence: "用户在扩展里手动添加", source: "manual",
      origin: "extension", actor: "extension",
    }));
  }
  // 平台未给时走检测；不可信即拒绝落盘（C1#29）
  const findings = await detectText(backend, value);
  const promptable = findings.filter((finding) => finding.promptable);
  if (promptable.length !== 1) {
    return { error: "识别结果不可信，拒绝落盘——请指定平台，或粘贴带完整上下文的文本" };
  }
  const finding = promptable[0];
  return finishAdd(await store.add(backend, session, {
    value, name, note, keyName: keyName ?? finding.keyName,
    platform: finding.platform, confidence: finding.confidence,
    // C1#29 与 C1#6 同形：detectSource 同样取 "extension"（字段语义分歧同见 hStore 注）
    evidence: finding.evidence, source: "extension", kind: finding.kind,
    maskStyle: finding.maskStyle, origin: "extension", actor: "extension",
  }));
}

async function hReset(msg) {
  // 鉴权单点在 store.destroyVault：verifier 校验（错口令抛「主密码不正确」、
  // 未初始化抛「vault 未初始化——先设置主密码」），校验通过才清库——
  // 本处理器不经手任何明文值。
  await store.destroyVault(backend, session, msg.password);
  // 清库不清擦除：30 秒内复制、还躺在剪贴板里的明文不因清库而免擦。桌面
  // 版擦除由分离 helper 进程执行、不受 vault 清除影响（wipe.py:5-17 明言
  // 「父进程退出后仍然存活」）；浏览器版的 pending wipe 活在 storage.session
  // 里，session.clear() 会连它一起清——必须在 clear 后原样放回并重建 alarm
  // （扩展未重载时 create 对同名幂等覆盖，无害）。
  const pending = await readPendingWipe();
  await chrome.storage.session.clear();
  if (pending) {
    await chrome.storage.session.set({ [PENDING_WIPE_KEY]: pending });
    chrome.alarms.create(pending.alarmName, { when: pending.deadlineAt });
  }
  return { ok: true };
}

/** C1 全表 30 个动作。未知 kind 在 handleMessage 兜底。 */
export const HANDLERS = {
  status: hStatus,
  setup: hSetup,
  unlock: hUnlock,
  lock: hLock,
  capture: hCapture,
  store: hStore,
  list: hList,
  get: hGet,
  reveal: hReveal,
  totp: hTotp,
  copied: scheduleWipe,
  "clipboard-changed": cancelWipe,
  rotate: hRotate,
  revoke: hRevoke,
  delete: hDelete,
  rename: hRename,
  "set-platform": hSetPlatform,
  corrections: hCorrections,
  "delete-correction": hDeleteCorrection,
  "import-text": hImportText,
  "mark-expired": hMarkExpired,
  stats: hStats,
  audit: hAudit,
  "export-backup": hExportBackup,
  "restore-backup": hRestoreBackup,
  "get-settings": hGetSettings,
  "set-settings": hSetSettings,
  "scan-texts": hScanTexts,
  "store-direct": hStoreDirect,
  reset: hReset,
};

// C1 末行的发送方约束：capture/store/clipboard-changed 只接受内容脚本，
// 其余动作只接受扩展自身页面。当前所有扩展上下文皆第一方代码，这是纵深
// 防御：内容脚本上下文即使出现中继缺陷，也拿不到完整 C1 面。
const CONTENT_SCRIPT_ONLY = new Set(["capture", "store", "clipboard-changed"]);

/** 返回拒绝文案；null = 放行。sender 缺省（SW 内部调用）视为可信。
 *
 * 内容脚本的判据不能只看 sender.tab：Chromium 官方 schema 对
 * MessageSender.tab 的原文是「This property will only be present when the
 * connection was opened from a tab (including content scripts)」——它标记
 * 「从标签页发起」而非「内容脚本专属」，而 manifest 的 options_ui.open_in_tab
 * 为 true，管理页就活在标签页里，新版 Chrome 同样会为它置 tab。因此补一条
 * 页面来源判别：扩展页（popup/options/offscreen）的 sender.url 必为
 * chrome-extension://<id>/ 前缀，标签页里不是这个前缀的才是内容脚本。 */
function senderRejection(kind, sender) {
  if (!sender) return null;
  const fromExtensionPage = typeof sender.url === "string"
    && sender.url.startsWith(`chrome-extension://${chrome.runtime.id}/`);
  const fromContentScript = sender.tab != null && !fromExtensionPage;
  if (CONTENT_SCRIPT_ONLY.has(kind)) {
    return fromContentScript ? null : `动作 ${kind} 只能由内容脚本发送`;
  }
  return fromContentScript ? `动作 ${kind} 只能由扩展页面发送` : null;
}

/** C1 消息面唯一入口。锁定错误按 name 判定统一映射（C1）；
 * 其余异常翻译成 {error}，绝不让原始异常逃出消息通道。 */
export async function handleMessage(msg, sender = null) {
  try {
    await touchActivity();
  } catch (_) {
    // 活动时间戳写不进去只影响自动锁定计时，不该挡住消息本身
  }
  const handler = HANDLERS[msg.kind];
  if (!handler) return { error: `未知请求类型：${msg.kind}` };
  const rejection = senderRejection(msg.kind, sender);
  if (rejection) return { error: rejection };
  try {
    return await handler(msg);
  } catch (error) {
    if (error instanceof LockedError || error?.name === "LockedError") {
      return { error: "vault 已锁定——先解锁", locked: true };
    }
    return { error: error?.message ?? String(error) };
  }
}

// ---------------------------------------------------------------- MV3 顶层接线

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || typeof msg !== "object") return false;
  if (msg.target === "offscreen") return false; // offscreen 页自己的消息，不进 handleMessage
  if (typeof msg.kind !== "string") return false;
  handleMessage(msg, sender).then(sendResponse, (error) => {
    sendResponse({ error: error?.message ?? String(error) });
  });
  return true; // sendResponse 异步送达
});

chrome.alarms.onAlarm.addListener((alarm) => {
  // 返回处理 promise：Chrome 本身忽略返回值，测试与调用方借此可靠等待
  if (!alarm || typeof alarm.name !== "string") return undefined;
  if (alarm.name === AUTOLOCK_ALARM) {
    return enforceAutolock().catch((error) => console.warn("自动锁定检查失败：", error?.message ?? error));
  }
  if (alarm.name.startsWith(WIPE_ALARM_PREFIX)) {
    return runWipeAlarm(alarm.name).catch((error) => console.warn("擦除调度失败：", error?.message ?? error));
  }
  return undefined;
});

// 每次冷启动都重建周期 alarm：扩展重载会清掉全部 alarms，create 对同名幂等覆盖
chrome.alarms.create(AUTOLOCK_ALARM, { periodInMinutes: AUTOLOCK_PERIOD_MINUTES });

runStartupSweep().catch((error) => {
  console.warn("启动 sweep 失败：", error?.message ?? error);
});
