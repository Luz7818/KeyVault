// background.js 单测（app-background）：C1 消息面全表 30 动作、C7 调度
// （自动锁定 / 启动 sweep / 剪贴板擦除）、reset 鉴权（C1#30）、
// storage.session 卫生（验收 6：只允许 raw key 的 b64、last-active、
// pending wipe 元数据）与「无检测实现痕迹」的机械检查（验收 1）。
//
// chrome.* 用内存 mock：node 无 chrome API 与 indexedDB，后端自动落到
// memory-backend（C2 已声明的 node 边界；IndexedDB 后端与 offscreen 页面
// 的真机行为靠浏览器走查）。mock 必须先于 import background.js 就位，
// 因为 SW 顶层就注册 listener、建 alarm、跑启动 sweep。

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// ---------------------------------------------------------------- chrome mock

function makeChromeMock() {
  const sessionData = new Map();
  const alarms = new Map();
  const sent = [];
  const offscreenDocs = [];
  const messageListeners = [];
  const alarmListeners = [];
  let offscreenOpen = false;
  let offscreenFails = false;
  const api = {
    runtime: {
      id: "mock-keyvault",
      getManifest: () => ({ version: "1.0.0" }),
      getURL: (path) => `chrome-extension://mock-keyvault/${path}`,
      getContexts: async () => (offscreenOpen ? [{ contextType: "OFFSCREEN_DOCUMENT" }] : []),
      sendMessage: async (msg) => {
        if (!offscreenOpen) {
          throw new Error("Could not establish connection. Receiving end does not exist.");
        }
        sent.push(msg);
        return { ok: true };
      },
      onMessage: { addListener: (fn) => messageListeners.push(fn) },
    },
    storage: {
      session: {
        async get(key) {
          return sessionData.has(key) ? { [key]: sessionData.get(key) } : {};
        },
        async set(patch) {
          for (const [k, v] of Object.entries(patch)) sessionData.set(k, v);
        },
        async remove(key) {
          sessionData.delete(key);
        },
        async clear() {
          sessionData.clear();
        },
      },
    },
    alarms: {
      create: (name, info) => alarms.set(name, info),
      clear: async (name) => alarms.delete(name),
      onAlarm: { addListener: (fn) => alarmListeners.push(fn) },
    },
    offscreen: {
      createDocument: async (opts) => {
        if (offscreenFails) throw new Error("offscreen createDocument 失败（模拟）");
        offscreenOpen = true;
        offscreenDocs.push(opts);
      },
    },
  };
  return {
    chrome: api,
    sessionData,
    alarms,
    sent,
    offscreenDocs,
    messageListeners,
    alarmListeners,
    setOffscreenOpen: (value) => { offscreenOpen = value; },
    setOffscreenFails: (value) => { offscreenFails = value; },
  };
}

const mock = makeChromeMock();
globalThis.chrome = mock.chrome;

const background = await import("../background.js");
const { handleMessage, HANDLERS, runStartupSweep } = background;

// ---------------------------------------------------------------- 样本与工具

const MASTER = "master-pass-8";
const SK_TOKEN = `sk-${"A".repeat(48)}`; // 金标 bare-sk 配方：键名加持 → deepseek/high
const NEW_TOKEN = `sk-${"B".repeat(48)}`;
const DIRECT_TOKEN = `sk-${"C".repeat(48)}`; // store-direct 检测路径（裸 sk- → openai-compatible）
const GH_TOKEN = `ghp_${"b".repeat(36)}`;
const DEEPSEEK_TEXT = `DEEPSEEK_API_KEY=${SK_TOKEN}`;
const MULTI_TEXT = `DEEPSEEK_API_KEY=${SK_TOKEN}\nGITHUB_TOKEN=${GH_TOKEN}`;
const TOTP_SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"; // RFC 6238 "12345678901234567890"
const SEED_B64 = Buffer.from("12345678901234567890", "utf8").toString("base64");

// C1 全表 30 个动作（CONTRACTS.md C1）
const ALL_KINDS = [
  "status", "setup", "unlock", "lock", "capture", "store", "list", "get",
  "reveal", "totp", "copied", "clipboard-changed", "rotate", "revoke",
  "delete", "rename", "set-platform", "corrections", "delete-correction",
  "import-text", "mark-expired", "stats", "audit", "export-backup",
  "restore-backup", "get-settings", "set-settings", "scan-texts",
  "store-direct", "reset",
];

function sendViaListener(msg) {
  return new Promise((resolve) => {
    const keepOpen = mock.messageListeners[0](msg, {}, resolve);
    assert.equal(keepOpen, true, "listener 必须返回 true（sendResponse 异步送达）");
  });
}

/** 带 sender 的 listener 调用（sender 形状对齐 chrome.runtime.MessageSender）。 */
function sendViaListenerAs(msg, sender) {
  return new Promise((resolve) => {
    const keepOpen = mock.messageListeners[0](msg, sender, resolve);
    assert.equal(keepOpen, true, "listener 必须返回 true（sendResponse 异步送达）");
  });
}

function fireAlarm(name) {
  assert.ok(mock.alarmListeners.length >= 1, "onAlarm listener 未注册");
  return Promise.all(mock.alarmListeners.map((fn) => fn({ name })));
}

/** 构造一条带过期时间的 KVBE1 备份并恢复入库（C5 payload）——markExpired
 * 走查的造数通道：C1 消息面没有改 expires_at 的动作，备份是唯一入口。 */
async function restoreExpiredRecord(name, value, expiredMsAgo) {
  const { stableStringify } = await import("../src/core/masking.js");
  const crypto = await import("../src/vault/crypto.js");
  const payload = stableStringify({
    version: 1,
    exportedAt: new Date().toISOString(),
    records: [{
      name, value, kind: "token", platform: "legacy-service", keyName: "OLD",
      tags: [], note: "", sourceUrl: "", status: "active",
      expiresAt: new Date(Date.now() - expiredMsAgo).toISOString(),
      createdAt: new Date().toISOString(), lastUsedAt: null, lastRevealedAt: null,
    }],
  });
  const salt = crypto.randomBytes(16);
  const raw = await crypto.deriveRawKey("backup-pass-1", salt, 600000);
  const box = await crypto.encryptText(await crypto.importRawKey(raw), payload);
  const blob = ["KVBE1", crypto.bytesToB64(salt), crypto.bytesToB64(box.iv), crypto.bytesToB64(box.ct)].join("|");
  const summary = await handleMessage({ kind: "restore-backup", password: "backup-pass-1", blob });
  assert.deepEqual(summary, { created: 1, deduped: 0, rejected: 0 });
}

// ---------------------------------------------------------------- 装配与未知 kind

describe("模块装配（C1 信封与接线）", () => {
  it("HANDLERS 恰好覆盖契约全表 30 个动作", () => {
    assert.equal(Object.keys(HANDLERS).length, 30);
    for (const kind of ALL_KINDS) {
      assert.equal(typeof HANDLERS[kind], "function", `缺动作 ${kind}`);
    }
    assert.deepEqual(Object.keys(HANDLERS).sort(), [...ALL_KINDS].sort());
  });

  it("顶层已建 kv-autolock 周期 alarm（periodInMinutes:1）", () => {
    assert.deepEqual(mock.alarms.get("kv-autolock"), { periodInMinutes: 1 });
  });

  it("未知 kind 返回 {error:\"未知请求类型：…\"}", async () => {
    assert.deepEqual(await handleMessage({ kind: "no-such-kind" }), {
      error: "未知请求类型：no-such-kind",
    });
    assert.deepEqual(await sendViaListener({ kind: "no-such-kind" }), {
      error: "未知请求类型：no-such-kind",
    });
  });

  it("kind 缺失 / 非 object / target=offscreen 的消息不进 handleMessage", () => {
    let replied = false;
    const reply = () => { replied = true; };
    assert.equal(mock.messageListeners[0](null, {}, reply), false);
    assert.equal(mock.messageListeners[0]({ noKind: true }, {}, reply), false);
    assert.equal(mock.messageListeners[0]({ target: "offscreen", kind: "status" }, {}, reply), false);
    assert.equal(replied, false);
  });

  it("status 未初始化：{initialized:false, unlocked:false, settings 默认, version}", async () => {
    assert.deepEqual(await handleMessage({ kind: "status" }), {
      initialized: false,
      unlocked: false,
      settings: { autoLockMinutes: 15, captureEnabled: true, ignoredHosts: [] },
      version: "1.0.0",
    });
  });
});

// ---------------------------------------------------------------- 发送方约束

describe("发送方约束（C1 末行）", () => {
  // sender 形状对齐真实 chrome.runtime.MessageSender：内容脚本 = tab + 网页
  // url；popup/options/offscreen = chrome-extension:// url（options_ui 的
  // open_in_tab:true 时同样带 tab——判据必须看 url 前缀，不能只看 tab）。
  const CONTENT_SENDER = { id: "mock-keyvault", tab: { id: 1 }, url: "https://example.com/x" };
  const PAGE_SENDER = { id: "mock-keyvault", url: "chrome-extension://mock-keyvault/popup.html" };
  const TAB_PAGE_SENDER = { id: "mock-keyvault", tab: { id: 2 },
    url: "chrome-extension://mock-keyvault/options.html" };

  it("capture/store/clipboard-changed 只接受内容脚本（tab + 非 chrome-extension:// 页面）", async () => {
    assert.equal("candidate" in (await sendViaListenerAs({
      kind: "capture", text: DEEPSEEK_TEXT, source_url: "https://api.deepseek.com/chat",
    }, CONTENT_SENDER)), true);
    assert.match((await sendViaListenerAs({
      kind: "capture", text: DEEPSEEK_TEXT, source_url: "https://api.deepseek.com/chat",
    }, PAGE_SENDER)).error, /动作 capture 只能由内容脚本发送/);
    assert.match((await sendViaListenerAs({ kind: "clipboard-changed" }, PAGE_SENDER)).error,
      /动作 clipboard-changed 只能由内容脚本发送/);
  });

  it("其余动作只接受扩展页面，内容脚本拿不到完整 C1 面", async () => {
    assert.equal("initialized" in (await sendViaListenerAs({ kind: "status" }, PAGE_SENDER)), true);
    assert.match((await sendViaListenerAs({ kind: "status" }, CONTENT_SENDER)).error,
      /动作 status 只能由扩展页面发送/);
    assert.match((await sendViaListenerAs({ kind: "reveal", id: 1 }, CONTENT_SENDER)).error,
      /动作 reveal 只能由扩展页面发送/);
  });

  it("标签页中的扩展页（options open_in_tab）带 tab 也放行——判据看 url 前缀不看 tab", async () => {
    // 复审 high：新版 Chrome 会为标签页中的扩展页置 sender.tab，单看 tab 会
    // 把整个 options 管理页拒掉
    assert.equal("initialized" in (await sendViaListenerAs({ kind: "status" }, TAB_PAGE_SENDER)), true);
    assert.equal("rows" in (await sendViaListenerAs({ kind: "list" }, TAB_PAGE_SENDER)), true);
    assert.match((await sendViaListenerAs({
      kind: "capture", text: DEEPSEEK_TEXT, source_url: "https://api.deepseek.com/chat",
    }, TAB_PAGE_SENDER)).error, /动作 capture 只能由内容脚本发送/);
  });

  it("不带 sender 的调用（SW 内部/测试路径）不受影响", async () => {
    assert.equal("initialized" in (await handleMessage({ kind: "status" })), true);
    assert.equal("candidate" in (await handleMessage({
      kind: "capture", text: DEEPSEEK_TEXT, source_url: "https://api.deepseek.com/chat",
    })), true);
  });
});

// ---------------------------------------------------------------- 初始化

describe("初始化（C1#2 setup）", () => {
  it("口令 <8 位拒绝且库未建", async () => {
    assert.deepEqual(await handleMessage({ kind: "setup", password: "short" }), {
      error: "主密码至少 8 位",
    });
    const { initialized } = await handleMessage({ kind: "status" });
    assert.equal(initialized, false);
  });

  it("setup 成功即解锁（capture→store 走查流程无中间解锁步）", async () => {
    assert.deepEqual(await handleMessage({ kind: "setup", password: MASTER }), { ok: true });
    const status = await handleMessage({ kind: "status" });
    assert.equal(status.initialized, true);
    assert.equal(status.unlocked, true);
  });

  it("重复初始化拒绝", async () => {
    assert.deepEqual(await handleMessage({ kind: "setup", password: MASTER }), {
      error: "vault 已经初始化过了",
    });
  });
});

// ---------------------------------------------------------------- capture

describe("capture 调度（C1#5）", () => {
  it("捕获开关关 → {disabled:true}", async () => {
    await handleMessage({ kind: "set-settings", patch: { captureEnabled: false } });
    assert.deepEqual(await handleMessage({
      kind: "capture", text: DEEPSEEK_TEXT, source_url: "https://deepseek.com/chat",
    }), { disabled: true });
    await handleMessage({ kind: "set-settings", patch: { captureEnabled: true } });
  });

  it("来源主机在 ignoredHosts → {ignored:true}；主机名写入时归一化为小写", async () => {
    const { settings } = await handleMessage({
      kind: "set-settings", patch: { ignoredHosts: ["Example.COM"] },
    });
    assert.deepEqual(settings.ignoredHosts, ["example.com"]);
    assert.deepEqual(await handleMessage({
      kind: "capture", text: DEEPSEEK_TEXT, source_url: "https://example.com/chat",
    }), { ignored: true });
    // 非忽略主机不受影响
    const result = await handleMessage({
      kind: "capture", text: DEEPSEEK_TEXT, source_url: "https://api.deepseek.com/chat",
    });
    assert.ok(result.candidate, "非忽略主机应正常走检测");
    await handleMessage({ kind: "set-settings", patch: { ignoredHosts: [] } });
  });

  it("恰一条 promptable → {candidate, value}；value=捕获原文全文，candidate 不含明文", async () => {
    const result = await handleMessage({
      kind: "capture", text: DEEPSEEK_TEXT,
      source_url: "https://api.deepseek.com/chat", title: "DeepSeek Platform",
    });
    // value 是捕获原文全文（C1#5），content 原样回传给 store 重新检测——
    // 若只回裸值，捕获时的键名/主机名 verdict 会在重检测时丢失（判定漂移）
    assert.equal(result.value, DEEPSEEK_TEXT);
    assert.equal(result.candidate.platform, "deepseek");
    assert.equal(result.candidate.confidence, "high");
    assert.equal(result.candidate.keyName, "DEEPSEEK_API_KEY");
    assert.ok(typeof result.candidate.preview === "string" && result.candidate.preview.length > 0);
    assert.ok(Array.isArray(result.candidate.maskStyle));
    for (const field of ["platform", "keyName", "kind", "confidence", "evidence", "maskStyle", "preview"]) {
      assert.ok(field in result.candidate, `candidate 缺字段 ${field}`);
    }
    assert.equal("value" in result.candidate, false, "candidate 里不允许有明文");
  });

  it("多条候选 → {results:[掩码行]}，不弹条且行内无 value", async () => {
    const result = await handleMessage({
      kind: "capture", text: MULTI_TEXT, source_url: "https://example.com/x",
    });
    assert.equal(Array.isArray(result.results), true);
    assert.equal(result.results.length, 2);
    assert.equal("candidate" in result, false);
    assert.equal("value" in result, false);
    for (const row of result.results) {
      assert.equal("value" in row, false, "掩码行不允许有明文");
      assert.ok(row.platform !== "unknown");
    }
  });

  it("零候选 → {results:[]}", async () => {
    assert.deepEqual(await handleMessage({
      kind: "capture", text: "这里只有一个普通句子。", source_url: "https://example.com/x",
    }), { results: [] });
  });

  it("set-settings 的 patch 非对象 → {error}", async () => {
    assert.deepEqual(await handleMessage({ kind: "set-settings", patch: "nope" }), {
      error: "patch 必须是对象",
    });
  });

  it("set-settings 拒绝未知字段与非法值（不静默禁用自动锁定）", async () => {
    assert.deepEqual(await handleMessage({
      kind: "set-settings", patch: { unknownField: 1 },
    }), { error: "未知设置字段：unknownField" });
    assert.match((await handleMessage({
      kind: "set-settings", patch: { autoLockMinutes: "abc" },
    })).error, /autoLockMinutes/);
    assert.match((await handleMessage({
      kind: "set-settings", patch: { autoLockMinutes: 999 },
    })).error, /autoLockMinutes/);
    assert.match((await handleMessage({
      kind: "set-settings", patch: { captureEnabled: "yes" },
    })).error, /captureEnabled/);
    assert.match((await handleMessage({
      kind: "set-settings", patch: { ignoredHosts: "example.com" },
    })).error, /ignoredHosts/);
    // 拒绝之后现有设置原样保留（autoLockMinutes 仍为 15，不是 0）
    const { settings } = await handleMessage({ kind: "get-settings" });
    assert.equal(settings.autoLockMinutes, 15);
  });
});

// ---------------------------------------------------------------- store 与读取

describe("store 与读取（C1#6/7/8/9）", () => {
  it("store（真实 wire 形状）：capture 的 value 原样回传 → created，判定与弹条一致", async () => {
    // content.js 只把 capture 响应的 value 作为 store 的 text 回传——
    // 这里按真实链路走：value 是捕获原文全文，重检测对全文确定性成立，
    // 落库判定（deepseek/high）必须与弹条判定一致，detectSource="extension"
    // （C1#6 承诺）。
    const captured = await handleMessage({
      kind: "capture", text: DEEPSEEK_TEXT,
      source_url: "https://api.deepseek.com/chat", title: "DeepSeek Platform",
    });
    assert.equal(captured.value, DEEPSEEK_TEXT);
    const result = await handleMessage({
      kind: "store", text: captured.value,
      source_url: "https://api.deepseek.com/chat", title: "DeepSeek Platform",
    });
    assert.deepEqual(result, { ok: true, outcome: "created", name: "deepseek-api-key", warning: "" });
    const { rows } = await handleMessage({ kind: "list", query: "deepseek-api-key" });
    const { record } = await handleMessage({ kind: "get", id: rows[0].id });
    assert.equal(record.platform, "deepseek", "落库平台不得漂移成 openai-compatible");
    assert.equal(record.confidence, "high");
    assert.equal(record.keyName, "DEEPSEEK_API_KEY");
    assert.equal(record.detectSource, "extension");
    assert.equal(record.sourceUrl, "https://api.deepseek.com/chat");
  });

  it("同一文本再 store → 去重 {outcome:\"deduped\"}，不插第二行", async () => {
    const captured = await handleMessage({
      kind: "capture", text: DEEPSEEK_TEXT, source_url: "https://api.deepseek.com/chat",
    });
    const result = await handleMessage({
      kind: "store", text: captured.value, source_url: "https://api.deepseek.com/chat",
    });
    assert.equal(result.outcome, "deduped");
    const { rows } = await handleMessage({ kind: "list" });
    assert.equal(rows.filter((r) => r.name === "deepseek-api-key").length, 1);
  });

  it("list：字段形状与契约一致，绝无明文/密文字段", async () => {
    const { rows } = await handleMessage({ kind: "list" });
    assert.equal(rows.length, 1);
    const row = rows[0];
    for (const field of ["id", "name", "platform", "status", "confidence", "keyName", "kind",
      "preview", "tags", "aliases", "note", "sourceUrl", "origin", "extra",
      "createdAt", "updatedAt", "expiresAt", "lastUsedAt"]) {
      assert.ok(field in row, `list 行缺字段 ${field}`);
    }
    assert.equal(row.sourceUrl, "https://api.deepseek.com/chat");
    assert.equal(row.status, "active");
    assert.equal("valueIv" in row, false);
    assert.equal("valueCt" in row, false);
    assert.equal(row.preview.includes(SK_TOKEN), false, "preview 必须是掩码");
  });

  it("list 搜索：不区分大小写子串", async () => {
    assert.equal((await handleMessage({ kind: "list", query: "DEEPSEEK" })).rows.length, 1);
    assert.equal((await handleMessage({ kind: "list", query: "no-such-thing" })).rows.length, 0);
  });

  it("get：详情含 fingerprints（16 位前缀）且无 valueIv/valueCt", async () => {
    const { rows } = await handleMessage({ kind: "list" });
    const { record } = await handleMessage({ kind: "get", id: rows[0].id });
    assert.equal(record.name, "deepseek-api-key");
    assert.equal("valueIv" in record, false);
    assert.equal("valueCt" in record, false);
    assert.equal(record.fingerprints.length, 1);
    assert.equal(record.fingerprints[0].sha256Prefix.length, 16);
    assert.equal(record.fingerprints[0].status, "active");
  });

  it("reveal：{value, name} 且明文等于原文", async () => {
    const { rows } = await handleMessage({ kind: "list" });
    const result = await handleMessage({ kind: "reveal", id: rows[0].id });
    assert.deepEqual(result, { value: SK_TOKEN, name: "deepseek-api-key" });
  });

  it("reveal 的 event 只允许 reveal/copy/totp", async () => {
    const { rows } = await handleMessage({ kind: "list" });
    const result = await handleMessage({ kind: "reveal", id: rows[0].id, event: "hacker" });
    assert.match(result.error, /非法事件类型/);
  });
});

// ---------------------------------------------------------------- 锁定语义

describe("锁定语义（C1：LockedError 统一映射）", () => {
  it("lock → {ok:true}", async () => {
    assert.deepEqual(await handleMessage({ kind: "lock" }), { ok: true });
  });

  it("锁定后 reveal/store/scan-texts/import-text/export-backup/totp 全部 {error, locked:true}", async () => {
    for (const msg of [
      { kind: "reveal", id: 1 },
      { kind: "store", text: DEEPSEEK_TEXT },
      { kind: "scan-texts", texts: [{ name: "t", content: DEEPSEEK_TEXT }] },
      { kind: "import-text", text: DEEPSEEK_TEXT },
      { kind: "export-backup", password: "backup-pass-1" },
      { kind: "totp", id: 1 },
      { kind: "rotate", id: 1, value: NEW_TOKEN },
      { kind: "revoke", id: 1 },
      { kind: "delete", id: 1 },
      { kind: "rename", id: 1, name: "x" },
      { kind: "set-platform", id: 1, platform: "github" },
      { kind: "restore-backup", password: "backup-pass-1", blob: "KVBE1|a|b|c" },
    ]) {
      const result = await handleMessage(msg);
      assert.deepEqual(result, { error: "vault 已锁定——先解锁", locked: true }, `动作 ${msg.kind}`);
    }
  });

  it("unlock 错口令 → {error:\"主密码不正确\", locked:true}", async () => {
    assert.deepEqual(await handleMessage({ kind: "unlock", password: "wrong-pass-9" }), {
      error: "主密码不正确",
      locked: true,
    });
    const status = await handleMessage({ kind: "status" });
    assert.equal(status.unlocked, false);
  });

  it("unlock 对口令 → {ok:true} 且解锁", async () => {
    assert.deepEqual(await handleMessage({ kind: "unlock", password: MASTER }), { ok: true });
    const status = await handleMessage({ kind: "status" });
    assert.equal(status.unlocked, true);
  });
});

// ---------------------------------------------------------------- totp

describe("totp（C1#10）", () => {
  it("store-direct 显式平台存入种子记录", async () => {
    const result = await handleMessage({
      kind: "store-direct", value: TOTP_SEED, name: "totp-seed",
      keyName: "seed", platform: "example-2fa",
    });
    assert.equal(result.outcome, "created");
  });

  it("totp {id}：6 位码 + remaining 1..30，且审计 reveal 通道", async () => {
    const { rows } = await handleMessage({ kind: "list", query: "totp-seed" });
    const { code, remaining } = await handleMessage({ kind: "totp", id: rows[0].id });
    assert.match(code, /^\d{6}$/);
    assert.ok(remaining >= 1 && remaining <= 30);
  });

  it("totp {seedB64}：不解密不审计（锁定态也可用）", async () => {
    await handleMessage({ kind: "lock" });
    const result = await handleMessage({ kind: "totp", seedB64: SEED_B64 });
    assert.match(result.code, /^\d{6}$/);
    await handleMessage({ kind: "unlock", password: MASTER });
  });

  it("值不是 base32 种子 → {error}", async () => {
    await handleMessage({
      kind: "store-direct", value: "not-a-seed!!", name: "bad-seed", platform: "misc",
    });
    const { rows } = await handleMessage({ kind: "list", query: "bad-seed" });
    const result = await handleMessage({ kind: "totp", id: rows[0].id });
    assert.match(result.error, /base32/);
  });
});

// ---------------------------------------------------------------- 记录操作

describe("记录操作（C1#13-19）", () => {
  it("rename → {ok:true}；重名拒绝 {error:\"同名记录已存在：…\"}", async () => {
    const { rows } = await handleMessage({ kind: "list", query: "totp-seed" });
    assert.deepEqual(await handleMessage({ kind: "rename", id: rows[0].id, name: "authenticator" }), {
      ok: true,
    });
    const conflict = await handleMessage({
      kind: "rename", id: rows[0].id, name: "deepseek-api-key",
    });
    assert.equal(conflict.error, "同名记录已存在：deepseek-api-key");
  });

  it("rotate：换值 → ok，旧指纹 revoked + 新指纹 active", async () => {
    const { rows } = await handleMessage({ kind: "list", query: "deepseek" });
    assert.deepEqual(await handleMessage({ kind: "rotate", id: rows[0].id, value: NEW_TOKEN }), {
      ok: true,
    });
    const { record } = await handleMessage({ kind: "get", id: rows[0].id });
    const statuses = record.fingerprints.map((f) => f.status).sort();
    assert.deepEqual(statuses, ["active", "revoked"]);
    const revealed = await handleMessage({ kind: "reveal", id: rows[0].id });
    assert.equal(revealed.value, NEW_TOKEN);
  });

  it("rotate 同值 / 与他记录重复 / 空值均报错", async () => {
    const { rows } = await handleMessage({ kind: "list", query: "deepseek" });
    const same = await handleMessage({ kind: "rotate", id: rows[0].id, value: NEW_TOKEN });
    assert.match(same.error, /新值和旧值相同/);
    const dup = await handleMessage({ kind: "rotate", id: rows[0].id, value: TOTP_SEED });
    assert.match(dup.error, /重复/);
    const empty = await handleMessage({ kind: "rotate", id: rows[0].id, value: "" });
    assert.match(empty.error, /新值为空/);
  });

  it("set-platform + remember 写纠正规则；corrections/delete-correction 往返", async () => {
    const { rows } = await handleMessage({ kind: "list", query: "deepseek" });
    assert.deepEqual(await handleMessage({
      kind: "set-platform", id: rows[0].id, platform: "custom-llm",
      remember: { kind: "keyname", pattern: "DEEPSEEK_API_KEY" },
    }), { ok: true });
    const { record } = await handleMessage({ kind: "get", id: rows[0].id });
    assert.equal(record.platform, "custom-llm");
    assert.equal(record.confidence, "manual");

    const { rows: corrs } = await handleMessage({ kind: "corrections" });
    assert.equal(corrs.length, 1);
    assert.equal(corrs[0].pattern, "DEEPSEEK_API_KEY");
    assert.equal(corrs[0].platform, "custom-llm");
    for (const field of ["id", "kind", "pattern", "platform", "note", "createdAt"]) {
      assert.ok(field in corrs[0], `corrections 行缺字段 ${field}`);
    }
    assert.deepEqual(await handleMessage({ kind: "delete-correction", id: corrs[0].id }), { ok: true });
    assert.deepEqual((await handleMessage({ kind: "corrections" })).rows, []);
  });

  it("revoke → status=revoked（保留在场，供 stats.flagged 计数）", async () => {
    const { rows } = await handleMessage({ kind: "list", query: "bad-seed" });
    assert.deepEqual(await handleMessage({ kind: "revoke", id: rows[0].id }), { ok: true });
    assert.equal((await handleMessage({ kind: "get", id: rows[0].id })).record.status, "revoked");
  });

  it("delete → 记录消失（审计保留 name_snapshot）", async () => {
    await handleMessage({
      kind: "store-direct", value: "doomed-value-0009", name: "delete-me", platform: "misc",
    });
    const { rows } = await handleMessage({ kind: "list", query: "delete-me" });
    assert.deepEqual(await handleMessage({ kind: "delete", id: rows[0].id }), { ok: true });
    assert.deepEqual((await handleMessage({ kind: "list", query: "delete-me" })).rows, []);
  });
});

// ---------------------------------------------------------------- 批量、扫描与统计

describe("批量导入、扫描与统计（C1#20/22/23/28）", () => {
  it("import-text：只入 promptable 候选，返回 {created,deduped,rejected,skipped}", async () => {
    const summary = await handleMessage({ kind: "import-text", text: MULTI_TEXT, tags: ["bulk"] });
    assert.equal(summary.created, 2);
    assert.equal(summary.deduped, 0);
    assert.equal(summary.rejected, 0);
    assert.deepEqual(summary.skipped, []);
    const again = await handleMessage({ kind: "import-text", text: MULTI_TEXT });
    assert.equal(again.created, 0);
    assert.equal(again.deduped, 2);
  });

  it("import-text 无候选 → 全零统计", async () => {
    const summary = await handleMessage({ kind: "import-text", text: "普通句子，没有密钥。" });
    assert.deepEqual(summary, { created: 0, deduped: 0, rejected: 0, skipped: [] });
  });

  it("scan-texts：命中含 12 位 sha 前缀；被轮换下来的旧值（revoked 指纹）仍算泄露证据", async () => {
    // 旧值 SK_TOKEN 出现在两处指纹：deepseek-api-key 轮换换下的 revoked 行
    // （密文随吊销移入指纹行）+ 上一条 import 把旧值重新入库的 active 记录
    // （rotate 后 secret 行 sha 已换成 NEW_TOKEN，旧值 sha 查不到 → 新建）。
    const { hits } = await handleMessage({
      kind: "scan-texts", texts: [{ name: "leak.txt", content: `cfg: ${SK_TOKEN} in repo` }],
    });
    assert.equal(hits.length, 2);
    const revokedHit = hits.find((h) => h.status === "revoked");
    assert.ok(revokedHit, "revoked 指纹行必须参与扫描（吊销的旧值仍是泄露证据）");
    assert.equal(revokedHit.name, "deepseek-api-key");
    for (const hit of hits) {
      assert.equal(hit.textName, "leak.txt");
      assert.equal(hit.lineNo, 1);
      assert.equal(hit.sha256Prefix.length, 12);
    }
    assert.notEqual(hits[0].secretId, hits[1].secretId);
  });

  it("stats：字段齐全且 flagged 计入吊销", async () => {
    const stats = await handleMessage({ kind: "stats" });
    for (const field of ["total", "active", "flagged", "expiring", "platforms"]) {
      assert.ok(field in stats, `stats 缺字段 ${field}`);
    }
    assert.ok(stats.total >= 3);
    assert.ok(stats.flagged >= 1);
    assert.ok(stats.platforms.length >= 1);
  });

  it("audit：最新在前 + 链完好", async () => {
    const { rows, chainOk, firstBadId } = await handleMessage({ kind: "audit" });
    assert.equal(chainOk, true);
    assert.equal(firstBadId, null);
    assert.ok(rows.length >= 2);
    for (let i = 1; i < rows.length; i++) {
      assert.ok(rows[i - 1].id > rows[i].id, "rows 必须最新在前");
    }
    for (const row of rows) {
      for (const field of ["id", "ts", "event", "actor", "secretId", "nameSnapshot", "shaPrefix", "detail"]) {
        assert.ok(field in row, `audit 行缺字段 ${field}`);
      }
    }
  });

  it("mark-expired：{flipped:int}（C1#21 保留动作）", async () => {
    const result = await handleMessage({ kind: "mark-expired" });
    assert.equal(typeof result.flipped, "number");
    assert.ok(Number.isInteger(result.flipped));
  });

  it("get-settings：{settings} 与默认合并形状", async () => {
    const { settings } = await handleMessage({ kind: "get-settings" });
    assert.deepEqual(settings, { autoLockMinutes: 15, captureEnabled: true, ignoredHosts: [] });
  });
});

// ---------------------------------------------------------------- 备份

describe("备份 KVBE1（C1#24/25）", () => {
  it("export 口令 <8 拒绝", async () => {
    assert.deepEqual(await handleMessage({ kind: "export-backup", password: "short" }), {
      error: "备份口令至少 8 位",
    });
  });

  it("export → {blob:\"KVBE1|…\"}", async () => {
    const { blob } = await handleMessage({ kind: "export-backup", password: "backup-pass-1" });
    assert.ok(blob.startsWith("KVBE1|"));
    assert.equal(blob.split("|").length, 4);
  });

  it("restore 错口令 → {error:\"口令错误或备份已损坏…\"}", async () => {
    const result = await handleMessage({
      kind: "restore-backup", password: "wrong-pass-9", blob: "KVBE1|aGVsbG8=|aXY=|Y3Q=",
    });
    assert.match(result.error, /口令错误或备份已损坏/);
  });

  it("restore 一条带过期时间的备份（供启动 sweep 的 markExpired 走查）", async () => {
    // 直接构造 KVBE1（C5）：payload=stableStringify({version,exportedAt,records})
    await restoreExpiredRecord("expired-one", "ExpiredValue-7h2Ks-0001", 86400000);
  });
});

// ---------------------------------------------------------------- 剪贴板擦除调度（C7）

describe("剪贴板 TTL 擦除调度（C1#11/12）", () => {
  it("copied → {ok:true, ttl:30}，写 pending 元数据 + 排 30s 单次 alarm", async () => {
    assert.deepEqual(await handleMessage({ kind: "copied" }), { ok: true, ttl: 30 });
    const pending = mock.sessionData.get("kv-pending-wipe");
    assert.equal(pending.alarmName.startsWith("wipe-"), true);
    assert.equal(pending.deadlineAt - pending.at, 30000);
    assert.equal(typeof mock.alarms.get(pending.alarmName)?.when, "number");
  });

  it("再次 copied → 旧 alarm 被取代", async () => {
    const first = mock.sessionData.get("kv-pending-wipe");
    await handleMessage({ kind: "copied" });
    const second = mock.sessionData.get("kv-pending-wipe");
    assert.ok(second.at >= first.at, "pending 应指向更新的拷贝");
    // 同毫秒重复 copied 时 alarmName 相同，create 幂等覆盖 when——同样等价于取代
    assert.equal(mock.alarms.has(second.alarmName), true);
    assert.equal(mock.alarms.get(second.alarmName).when, second.deadlineAt);
    if (second.alarmName !== first.alarmName) {
      assert.equal(mock.alarms.has(first.alarmName), false, "旧 alarm 应被清掉");
    }
  });

  it("clipboard-changed → 取消在飞擦除（pending + alarm 全清）", async () => {
    const pending = mock.sessionData.get("kv-pending-wipe");
    assert.deepEqual(await handleMessage({ kind: "clipboard-changed" }), { ok: true });
    assert.equal(mock.sessionData.has("kv-pending-wipe"), false);
    assert.equal(mock.alarms.has(pending.alarmName), false);
  });

  it("wipe alarm 到点但 offscreen 起不来 → 放弃本次（宁可不擦也不错擦），pending 留给 sweep", async () => {
    await handleMessage({ kind: "copied" });
    const pending = mock.sessionData.get("kv-pending-wipe");
    mock.setOffscreenFails(true);
    await fireAlarm(pending.alarmName);
    assert.equal(mock.sent.filter((m) => m.kind === "wipe-clipboard").length, 0);
    assert.equal(mock.sessionData.has("kv-pending-wipe"), true, "失败后 pending 保留");
    mock.setOffscreenFails(false);
  });

  it("wipe alarm 到点且 offscreen 可用 → 转发 {target:\"offscreen\", kind:\"wipe-clipboard\"} 并清 pending", async () => {
    const pending = mock.sessionData.get("kv-pending-wipe");
    await fireAlarm(pending.alarmName);
    const wipeMessages = mock.sent.filter(
      (m) => m.target === "offscreen" && m.kind === "wipe-clipboard",
    );
    assert.equal(wipeMessages.length, 1);
    assert.equal(mock.sessionData.has("kv-pending-wipe"), false);
  });

  it("alarm 名与 pending 不匹配 → 不擦（被更新的拷贝或取消取代）", async () => {
    await handleMessage({ kind: "copied" });
    const wipeCount = mock.sent.filter((m) => m.kind === "wipe-clipboard").length;
    await fireAlarm("wipe-1234567890");
    assert.equal(mock.sent.filter((m) => m.kind === "wipe-clipboard").length, wipeCount);
    assert.equal(mock.sessionData.has("kv-pending-wipe"), true);
    await handleMessage({ kind: "clipboard-changed" });
  });
});

// ---------------------------------------------------------------- 自动锁定（C7）

describe("自动锁定（C1#4 + alarm「kv-autolock」）", () => {
  it("未超时 → 不锁定", async () => {
    await fireAlarm("kv-autolock"); // handleMessage 刚刷新过 last-active
    const status = await handleMessage({ kind: "status" });
    assert.equal(status.unlocked, true);
  });

  it("超过 autoLockMinutes → session.lock()", async () => {
    mock.sessionData.set("kv-last-active", Date.now() - 16 * 60 * 1000);
    await fireAlarm("kv-autolock");
    const status = await handleMessage({ kind: "status" });
    assert.equal(status.unlocked, false);
  });

  it("autoLockMinutes=0 禁用 → 不锁定", async () => {
    await handleMessage({ kind: "unlock", password: MASTER });
    await handleMessage({ kind: "set-settings", patch: { autoLockMinutes: 0 } });
    mock.sessionData.set("kv-last-active", Date.now() - 16 * 60 * 1000);
    await fireAlarm("kv-autolock");
    const status = await handleMessage({ kind: "status" });
    assert.equal(status.unlocked, true);
    await handleMessage({ kind: "set-settings", patch: { autoLockMinutes: 15 } });
  });
});

// ---------------------------------------------------------------- 启动 sweep（C7）

describe("启动 sweep：过期标记 + pending wipe 安全网", () => {
  it("sweep 把 expiresAt 已过的记录翻成 expired（验收 4：仪表盘吊销/过期计数来源）", async () => {
    // 用新造的记录验证（此前 unlock 自带的 markExpired 可能已翻掉旧记录）
    await restoreExpiredRecord("expired-sweep", "ExpiredValue-3n8Qz-0003", 3600000);
    const before = await handleMessage({ kind: "stats" });
    await runStartupSweep();
    const { rows } = await handleMessage({ kind: "list", status: "expired" });
    assert.equal(rows.some((r) => r.name === "expired-sweep"), true);
    const after = await handleMessage({ kind: "stats" });
    assert.equal(after.flagged, before.flagged + 1);
  });

  it("unlock 成功后也会再跑一次 markExpired（C1#3）", async () => {
    await restoreExpiredRecord("expired-two", "ExpiredValue-9x4Lm-0002", 7200000);
    await handleMessage({ kind: "lock" });
    assert.deepEqual(await handleMessage({ kind: "unlock", password: MASTER }), { ok: true });
    const { rows } = await handleMessage({ kind: "list", status: "expired" });
    assert.equal(rows.some((r) => r.name === "expired-two"), true);
  });

  it("pending wipe 已到期 → sweep 立即补擦并清 pending（wipe.py:15-19 安全网等价）", async () => {
    mock.setOffscreenOpen(true);
    const wipeCount = mock.sent.filter((m) => m.kind === "wipe-clipboard").length;
    mock.sessionData.set("kv-pending-wipe", {
      alarmName: "wipe-1000", at: Date.now() - 60000, deadlineAt: Date.now() - 1000,
    });
    await runStartupSweep();
    assert.equal(mock.sent.filter((m) => m.kind === "wipe-clipboard").length, wipeCount + 1);
    assert.equal(mock.sessionData.has("kv-pending-wipe"), false);
  });

  it("pending wipe 未到期 → sweep 补挂 alarm（扩展重载清光 alarms 的兜底）", async () => {
    mock.sessionData.set("kv-pending-wipe", {
      alarmName: "wipe-2000", at: Date.now(), deadlineAt: Date.now() + 10000,
    });
    await runStartupSweep();
    assert.equal(mock.alarms.get("wipe-2000")?.when > Date.now(), true);
    assert.equal(mock.sessionData.has("kv-pending-wipe"), true);
  });
});

// ---------------------------------------------------------------- store-direct（C1#29）

describe("store-direct：手动添加", () => {
  it("显式平台 → 直接入库（confidence=manual）", async () => {
    const result = await handleMessage({
      kind: "store-direct", value: "plain-value-xyz-001", name: "manual-entry", platform: "custom-tool",
    });
    assert.equal(result.outcome, "created");
    const { rows } = await handleMessage({ kind: "list", query: "manual-entry" });
    const { record } = await handleMessage({ kind: "get", id: rows[0].id });
    assert.equal(record.platform, "custom-tool");
    assert.equal(record.confidence, "manual");
  });

  it("平台未给时走检测：裸 sk-（C9#1 放宽口径）→ openai-compatible，detectSource=extension", async () => {
    const result = await handleMessage({ kind: "store-direct", value: DIRECT_TOKEN });
    assert.equal(result.outcome, "created");
    const { rows } = await handleMessage({ kind: "list", query: result.name });
    assert.equal(rows[0].platform, "openai-compatible");
    const { record } = await handleMessage({ kind: "get", id: rows[0].id });
    assert.equal(record.detectSource, "extension");
  });

  it("识别不可信 → {error:\"识别结果不可信，拒绝落盘…\"} 且不落库", async () => {
    const result = await handleMessage({ kind: "store-direct", value: "hello world" });
    assert.match(result.error, /识别结果不可信，拒绝落盘/);
    assert.equal((await handleMessage({ kind: "list", query: "hello" })).rows.length, 0);
  });
});

// ---------------------------------------------------------------- storage.session 卫生（验收 6）

describe("storage.session 卫生", () => {
  it("键集合只允许 raw key 的 b64、last-active、pending wipe 元数据", () => {
    const allowed = new Set(["kv-vault-raw-key", "kv-last-active", "kv-pending-wipe"]);
    for (const key of mock.sessionData.keys()) {
      assert.ok(allowed.has(key), `storage.session 出现意外键：${key}`);
    }
  });

  it("全量序列化不含任何明文值（主密码 / 各密钥原文 / 种子）", () => {
    const dump = JSON.stringify([...mock.sessionData.entries()]);
    for (const secret of [MASTER, SK_TOKEN, NEW_TOKEN, DIRECT_TOKEN, GH_TOKEN, TOTP_SEED,
      "ExpiredValue-7h2Ks-0001", "ExpiredValue-9x4Lm-0002", "plain-value-xyz-001"]) {
      assert.equal(dump.includes(secret), false, `storage.session 泄漏明文：${secret}`);
    }
  });
});

// ---------------------------------------------------------------- reset 鉴权（C1#30 / 验收 5）

describe("reset 鉴权", () => {
  it("锁定态错口令 → {error:\"主密码不正确\"} 且数据完好", async () => {
    await handleMessage({ kind: "lock" });
    assert.deepEqual(await handleMessage({ kind: "reset", password: "wrong-pass-9" }), {
      error: "主密码不正确",
    });
    const status = await handleMessage({ kind: "status" });
    assert.equal(status.initialized, true);
    assert.equal((await handleMessage({ kind: "list" })).rows.length > 0, true);
  });

  it("锁定态正确口令 reset：清库回未初始化，session 只留 pending wipe（清库不清擦除）", async () => {
    // 先安排一次擦除：30 秒内复制、还躺在剪贴板里的明文不因清库而免擦
    // （桌面分离 helper 不受 vault 清除影响，wipe.py:5-17——浏览器版必须在
    // session.clear() 后把 pending wipe 原样放回并重建 alarm）
    assert.deepEqual(await handleMessage({ kind: "copied" }), { ok: true, ttl: 30 });
    const pending = mock.sessionData.get("kv-pending-wipe");
    assert.deepEqual(await handleMessage({ kind: "reset", password: MASTER }), { ok: true });
    // session 清空断言在 status/list 之前——每条消息本身都会 touchActivity
    // 写入 kv-last-active（正常行为）；清空后只允许在飞擦除的 pending 残留
    for (const key of mock.sessionData.keys()) {
      assert.equal(key, "kv-pending-wipe", `session 残留意外键：${key}`);
    }
    assert.deepEqual(mock.sessionData.get("kv-pending-wipe"), pending, "pending 必须原样放回");
    assert.equal(mock.alarms.has(pending.alarmName), true, "alarm 必须重建");
    const status = await handleMessage({ kind: "status" });
    assert.equal(status.initialized, false);
    assert.equal(status.unlocked, false);
    assert.equal((await handleMessage({ kind: "list" })).rows.length, 0);
    // 在飞擦除到点仍须送达 offscreen
    mock.setOffscreenOpen(true);
    const before = mock.sent.filter((m) => m.kind === "wipe-clipboard").length;
    await fireAlarm(pending.alarmName);
    assert.equal(
      mock.sent.filter((m) => m.kind === "wipe-clipboard").length,
      before + 1,
      "清库后擦除到点仍须执行",
    );
    assert.equal(mock.sessionData.has("kv-pending-wipe"), false);
  });

  it("reset 未初始化的 vault → {error:\"vault 未初始化——先设置主密码\"}", async () => {
    const result = await handleMessage({ kind: "reset", password: MASTER });
    assert.match(result.error, /vault 未初始化/);
  });

  it("清库后重建：setup → store 全流程可再走一遍", async () => {
    await handleMessage({ kind: "setup", password: MASTER });
    const result = await handleMessage({
      kind: "store", text: DEEPSEEK_TEXT, source_url: "https://api.deepseek.com/chat",
    });
    assert.equal(result.outcome, "created");
  });
});

// ---------------------------------------------------------------- 机械检查（验收 1）

describe("background.js 无检测实现痕迹（机械可查）", () => {
  const source = readFileSync(new URL("../background.js", import.meta.url), "utf8");
  // 剥行注释后检查（background.js 的字符串字面量里没有 //，行级剥离安全）
  const code = source.split("\n").map((line) => line.split("//")[0]).join("\n");

  it("不含 sha256 / canonicalize 调用痕迹", () => {
    assert.equal(code.includes("sha256"), false, "background.js 不允许自己碰指纹");
    assert.equal(code.includes("canonicalize"), false, "background.js 不允许自己规范化值");
  });

  it("不含正则字面量与正则 API（检测一律 import detect.js 的 detectText）", () => {
    for (const forbidden of [
      "new RegExp(", ".test(", ".exec(", ".match(", ".replace(", ".replaceAll(",
      ".search(", "RegExp",
    ]) {
      assert.equal(code.includes(forbidden), false, `background.js 出现 ${forbidden}`);
    }
    // 正则字面量形态：剥注释后的代码里不允许出现「非字符串的裸 /…/ 分隔对」；
    // 本文件合法的斜杠只有 import 路径、字符串与除法——逐一核对特征最稳。
    assert.equal((code.match(/=\s*\/[^/*\s]/g) ?? []).length, 0, "疑似正则字面量赋值");
    assert.equal((code.match(/return\s+\/[^/*\s]/g) ?? []).length, 0, "疑似正则字面量返回");
  });

  it("检测入口唯一：import 的是 src/vault/detect.js 的 detectText", () => {
    assert.equal(
      (source.match(/import\s+\{[^}]*detectText[^}]*\}\s+from\s+"\.\/src\/vault\/detect\.js";/) ?? []).length,
      1,
    );
  });
});
