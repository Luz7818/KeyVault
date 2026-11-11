// background-idle.test.js —— KVX-1 自动锁空闲判定专测。
//
// 批设计验收（综合路线图 §3.1#1）：内容脚本连发 N 条 + 时钟推进 → 断言仍按
// autoLockMinutes 锁定；扩展页交互 → last-active 刷新。旧行为是 handleMessage
// 对每条消息无条件 touchActivity——用户在任意网页按 Ctrl+C（内容脚本每次
// copy/cut 都发 clipboard-changed + capture）就把自动锁计时续回去，
// autoLockMinutes 形同虚设。修复后：续命只认扩展 UI 交互（sender 判据与
// senderRejection 同源），内容脚本唯一例外是捕获条「存入」（store——用户
// 对 vault 的显式拍板）。空闲判定抽成时钟可注入的纯函数 isIdleExpired，
// 边界在此直测（对齐桌面 tests 的 FakeClock 模式）。
//
// chrome.* 用内存 mock（口径同 app-background.test.js：node 无 chrome API
// 与 indexedDB，后端自动落到 memory-backend）。时钟推进在 alarm 集成路径用
// Date.now 打桩模拟（enforceAutolock 读的就是它），finally 必还原，
// 绝不污染同文件后续用例。

import { describe, it } from "node:test";
import assert from "node:assert/strict";

// ---------------------------------------------------------------- chrome mock

function makeChromeMock() {
  const sessionData = new Map();
  const alarms = new Map();
  const sent = [];
  const messageListeners = [];
  const alarmListeners = [];
  let offscreenOpen = false;
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
      createDocument: async () => {
        offscreenOpen = true;
      },
    },
  };
  return { chrome: api, sessionData, alarms, sent, messageListeners, alarmListeners };
}

const mock = makeChromeMock();
globalThis.chrome = mock.chrome;

const background = await import("../background.js");
const { handleMessage, isIdleExpired } = background;

// 一次性初始化：setup 成功即解锁（C1#2 语义），后续用例直接处于解锁态
assert.deepEqual(await handleMessage({ kind: "setup", password: "master-pass-8" }), { ok: true });

// ---------------------------------------------------------------- 样本与工具

const MASTER = "master-pass-8";
const DEEPSEEK_TEXT = `DEEPSEEK_API_KEY=${"sk-" + "A".repeat(48)}`;

// sender 形状对齐真实 chrome.runtime.MessageSender（口径同 app-background）：
// 内容脚本 = tab + 网页 url；扩展页 = chrome-extension:// url（options_ui 的
// open_in_tab:true 时同样带 tab——判据看 url 前缀，不能只看 tab）。
const CONTENT_SENDER = { id: "mock-keyvault", tab: { id: 1 }, url: "https://example.com/x" };
const PAGE_SENDER = { id: "mock-keyvault", tab: { id: 2 },
  url: "chrome-extension://mock-keyvault/options.html" };

const LAST_ACTIVE_KEY = "kv-last-active";
const MINUTE = 60_000;

function fireAlarm(name) {
  assert.ok(mock.alarmListeners.length >= 1, "onAlarm listener 未注册");
  return Promise.all(mock.alarmListeners.map((fn) => fn({ name })));
}

async function unlockFresh() {
  await handleMessage({ kind: "lock" });
  assert.deepEqual(await handleMessage({ kind: "unlock", password: MASTER }), { ok: true });
}

/** 把 last-active 人为拨回 agoMs 前，返回拨回值（供「没被续命」的等值断言）。 */
function backdateLastActive(agoMs) {
  const stamp = Date.now() - agoMs;
  mock.sessionData.set(LAST_ACTIVE_KEY, stamp);
  return stamp;
}

// ---------------------------------------------------------------- 纯函数边界

describe("isIdleExpired（KVX-1 空闲判定，时钟可注入）", () => {
  it("恰满 autoLockMinutes 才过期：14min59s 不锁，15min 整锁", () => {
    const t0 = 1_000_000;
    assert.equal(isIdleExpired(t0, t0 + 15 * MINUTE - 1, 15), false);
    assert.equal(isIdleExpired(t0, t0 + 15 * MINUTE, 15), true);
    assert.equal(isIdleExpired(t0, t0 + 16 * MINUTE, 15), true);
  });

  it("autoLockMinutes=0/负 → 禁用，永不判过期", () => {
    assert.equal(isIdleExpired(0, Number.MAX_SAFE_INTEGER, 0), false);
    assert.equal(isIdleExpired(0, Number.MAX_SAFE_INTEGER, -5), false);
    assert.equal(isIdleExpired(0, Number.MAX_SAFE_INTEGER, "0"), false);
  });

  it("lastActive 非数值（会话刚开始，无活动痕迹）→ 不判过期", () => {
    assert.equal(isIdleExpired(null, Date.now(), 15), false);
    assert.equal(isIdleExpired(undefined, Date.now(), 15), false);
  });

  it("时钟由入参注入——不读 Date.now，任意年代任意量级都成立", () => {
    assert.equal(isIdleExpired(42, 42 + 16 * MINUTE, 15), true);
    assert.equal(isIdleExpired(9e12, 9e12 + 14 * MINUTE, 15), false);
  });
});

// ---------------------------------------------------------------- KVX-1 集成

describe("KVX-1：内容脚本页面事件不续命", () => {
  it("内容脚本连发 N 条 clipboard-changed/capture → last-active 一动不动", async () => {
    assert.equal(await handleMessage({ kind: "status" }).then((s) => s.unlocked), true);
    const t0 = backdateLastActive(30_000); // 拨回 30 秒前
    for (let i = 0; i < 5; i += 1) {
      await handleMessage({ kind: "clipboard-changed" }, CONTENT_SENDER);
      await handleMessage({
        kind: "capture", text: DEEPSEEK_TEXT,
        source_url: "https://api.deepseek.com/chat", title: "DeepSeek",
      }, CONTENT_SENDER);
    }
    assert.equal(
      mock.sessionData.get(LAST_ACTIVE_KEY), t0,
      "内容脚本页面事件不得刷新 last-active（旧行为：每条消息无条件 touchActivity）",
    );
  });

  it("内容脚本「存入」按钮（store）是用户对 vault 的拍板 → 续命", async () => {
    backdateLastActive(60_000);
    const reply = await handleMessage({
      kind: "store", text: DEEPSEEK_TEXT, source_url: "https://api.deepseek.com/chat",
    }, CONTENT_SENDER);
    assert.equal(reply.ok, true);
    assert.ok(
      mock.sessionData.get(LAST_ACTIVE_KEY) > Date.now() - 60_000,
      "用户点「存入」必须按交互续命",
    );
  });

  it("扩展页消息（含 open_in_tab 的 options 页，带 tab）→ 续命", async () => {
    backdateLastActive(60_000);
    const reply = await handleMessage({ kind: "status" }, PAGE_SENDER);
    assert.equal(reply.initialized, true);
    assert.ok(
      mock.sessionData.get(LAST_ACTIVE_KEY) > Date.now() - 60_000,
      "扩展 UI 交互必须续命",
    );
  });

  it("内容脚本发扩展页专属动作被 sender 门拒绝 → 也不续命", async () => {
    const t0 = backdateLastActive(60_000);
    const reply = await handleMessage({ kind: "status" }, CONTENT_SENDER);
    assert.match(reply.error, /动作 status 只能由扩展页面发送/);
    assert.equal(
      mock.sessionData.get(LAST_ACTIVE_KEY), t0,
      "被拒消息不是交互，不得续命",
    );
  });

  it("边复制边推进时钟（旧 bug 的真实形态）→ 第 16 分钟仍按 autoLockMinutes 锁定", async () => {
    await unlockFresh(); // senderless，last-active = 现在
    const base = Date.now();
    const realNow = Date.now;
    try {
      // 第 4/9/14 分钟各来一次页面复制（旧行为会把 last-active 推到第 14 分钟，
      // 第 16 分钟的 alarm 就够不着 15 分钟阈值——那正是 KVX-1 要修的 bug）
      for (const offset of [4, 9, 14]) {
        Date.now = () => base + offset * MINUTE;
        await handleMessage({ kind: "clipboard-changed" }, CONTENT_SENDER);
      }
      // 推进到第 16 分钟，自动锁 alarm 到点
      Date.now = () => base + 16 * MINUTE;
      await fireAlarm("kv-autolock");
    } finally {
      Date.now = realNow;
    }
    const status = await handleMessage({ kind: "status" });
    assert.equal(
      status.unlocked, false,
      "页面事件不续命：即使噪声贯穿全程，第 16 分钟也必须锁定",
    );
  });

  it("扩展页持续交互 → 第 45 分钟仍不锁（续命另一侧）", async () => {
    await unlockFresh();
    const base = Date.now();
    const realNow = Date.now;
    try {
      // 第 10/20/40 分钟各在扩展页里操作一次——last-active 一路被续到第 40 分钟
      for (const offset of [10, 20, 40]) {
        Date.now = () => base + offset * MINUTE;
        await handleMessage({ kind: "status" }, PAGE_SENDER);
      }
      Date.now = () => base + 45 * MINUTE; // 距最近一次交互 5 分钟 < 15
      await fireAlarm("kv-autolock");
    } finally {
      Date.now = realNow;
    }
    const status = await handleMessage({ kind: "status" });
    assert.equal(status.unlocked, true, "扩展页交互续命：距上次交互仅 5 分钟，不得锁定");
  });
});
