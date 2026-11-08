// content.js 单测：页内捕获与提示条（模块 app-content）。
//
// 零依赖桩：node 没有 DOM 与 chrome.runtime，这里按桌面 tests/fakes.py 的
// 同一思路手写最小桩（元素/文档/定时器/消息通道），驱动 createCapture 的
// 真实代码路径——不经任何打包或 DOM polyfill。
// 期望值锚点：CONTRACTS.md C1#5/#6/#12、C7（clipboard-changed 取消在飞擦除）、
// C9#1（歧义显示「厂商未定」）、kv/capture/watch.py:139-142（seen ring 语义；
// 容量 20 与桌面 SEEN_RING_SIZE=200 的分歧已在源码注释按 C9 声明）、
// kv/capture/wipe.py:121-146（不覆盖用户新复制内容的性质）。

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import "../content.js";

const kv = globalThis.KeyVaultContent;
const HERE = dirname(fileURLToPath(import.meta.url));

// ---------------------------------------------------------------- 桩

function fakeElement(tag) {
  const el = {
    tagName: String(tag).toUpperCase(),
    children: [],
    textContent: "",
    className: "",
    id: "",
    parentNode: null,
    shadow: null,
    listeners: new Map(),
    appendChild(child) {
      child.parentNode = el;
      el.children.push(child);
      return child;
    },
    removeChild(child) {
      const index = el.children.indexOf(child);
      if (index >= 0) {
        el.children.splice(index, 1);
        child.parentNode = null;
      }
      return child;
    },
    get firstChild() {
      return el.children.length > 0 ? el.children[0] : null;
    },
    addEventListener(type, fn) {
      if (!el.listeners.has(type)) el.listeners.set(type, []);
      el.listeners.get(type).push(fn);
    },
    removeEventListener(type, fn) {
      const list = el.listeners.get(type);
      if (!list) return;
      const index = list.indexOf(fn);
      if (index >= 0) list.splice(index, 1);
    },
    click(event = {}) {
      for (const fn of el.listeners.get("click") || []) fn(event);
    },
    attachShadow() {
      el.shadow = fakeElement("#shadow-root");
      return el.shadow;
    },
  };
  return el;
}

function fakeDocument(title) {
  const doc = fakeElement("#document");
  doc.title = title;
  doc.documentElement = fakeElement("html");
  doc.appendChild(doc.documentElement);
  doc.createElement = (tag) => fakeElement(tag);
  return doc;
}

function fakeTimers() {
  const jobs = [];
  let nextId = 1;
  return {
    jobs,
    setTimeout(fn, ms) {
      const id = nextId;
      nextId += 1;
      jobs.push({ id, fn, ms });
      return id;
    },
    clearTimeout(id) {
      const index = jobs.findIndex((job) => job.id === id);
      if (index >= 0) jobs.splice(index, 1);
    },
  };
}

function fakeRuntime() {
  const sent = [];
  const pending = [];
  return {
    sent,
    pending,
    send(message, onReply) {
      sent.push(message);
      pending.push({ message, onReply });
    },
    respondByKind(kind, response, error = null) {
      const index = pending.findIndex((call) => call.message.kind === kind);
      assert.ok(index >= 0, `没有待响应的 ${kind} 消息`);
      const [call] = pending.splice(index, 1);
      call.onReply({ response, error });
    },
  };
}

function makeEnv({ title = "DeepSeek 开放平台 - Google Chrome" } = {}) {
  const doc = fakeDocument(title);
  const timers = fakeTimers();
  const runtime = fakeRuntime();
  const location = { href: "https://example.com/console" };
  const controller = kv.createCapture({ runtime, document: doc, location, timers });
  return { doc, timers, runtime, location, controller };
}

function copyEvent(text, { trusted = true } = {}) {
  return {
    isTrusted: trusted,
    clipboardData: {
      getData: (type) => (type === "text/plain" ? text : ""),
    },
  };
}

function dispatch(doc, type, event) {
  const handlers = doc.listeners.get(type) || [];
  assert.ok(handlers.length > 0, `文档上没有注册 ${type} 监听`);
  for (const fn of handlers) fn(event);
}

function messagesOfKind(runtime, kind) {
  return runtime.sent.filter((message) => message.kind === kind);
}

function findBar(doc) {
  return doc.documentElement.children.find((child) => child.id === kv.HOST_ID) || null;
}

function findByClass(root, cls) {
  for (const child of root.children) {
    if (String(child.className).split(/\s+/).includes(cls)) return child;
    const hit = findByClass(child, cls);
    if (hit) return hit;
  }
  return null;
}

function showBar(env, text, candidate) {
  dispatch(env.doc, "copy", copyEvent(text));
  env.runtime.respondByKind("capture", { candidate, value: text });
  const bar = findBar(env.doc);
  assert.ok(bar, "提示条未挂载");
  return bar;
}

// 金标同源候选：键名命中（resolve.js keynameStage，confidence=high）的形状，
// 与 detect.test.js 的 DEEPSEEK_API_KEY 用例同一语义。
const CANDIDATE = {
  platform: "deepseek",
  keyName: "DEEPSEEK_API_KEY",
  kind: "token",
  confidence: "high",
  evidence: "键名 DEEPSEEK_API_KEY",
  maskStyle: [3, 4],
  preview: "sk-***AAAA",
};

const KEY_TEXT = `DEEPSEEK_API_KEY=sk-${"A".repeat(48)}`;

// ---------------------------------------------------- copy/cut 捕获闸门

describe("copy/cut 捕获闸门", () => {
  it("每一次 copy 都先无条件发 clipboard-changed（太短也发）", () => {
    const env = makeEnv();
    dispatch(env.doc, "copy", copyEvent("abc"));
    const changed = messagesOfKind(env.runtime, "clipboard-changed");
    assert.equal(changed.length, 1);
    assert.deepEqual(changed[0], { kind: "clipboard-changed" }); // C1#12：无附加字段
    assert.equal(messagesOfKind(env.runtime, "capture").length, 0);
  });

  it("送检闸门 8–200000 字符", () => {
    const env = makeEnv();
    dispatch(env.doc, "copy", copyEvent("1234567")); // 7 位：不送检
    dispatch(env.doc, "copy", copyEvent("12345678")); // 8 位：送检
    dispatch(env.doc, "copy", copyEvent("k".repeat(200000))); // 上限：送检
    dispatch(env.doc, "copy", copyEvent("k".repeat(200001))); // 超限：不送检
    const captures = messagesOfKind(env.runtime, "capture");
    assert.deepEqual(
      captures.map((message) => message.text.length),
      [8, 200000],
    );
  });

  it("capture 报文形状：{kind, text, source_url, title}", () => {
    const env = makeEnv();
    dispatch(env.doc, "copy", copyEvent(KEY_TEXT));
    const [capture] = messagesOfKind(env.runtime, "capture");
    assert.deepEqual(capture, {
      kind: "capture",
      text: KEY_TEXT,
      source_url: "https://example.com/console",
      title: "DeepSeek 开放平台 - Google Chrome",
    });
  });

  it("cut 事件与 copy 同路（cut 也动剪切板）", () => {
    const env = makeEnv();
    dispatch(env.doc, "cut", copyEvent(KEY_TEXT));
    assert.equal(messagesOfKind(env.runtime, "clipboard-changed").length, 1);
    assert.equal(messagesOfKind(env.runtime, "capture").length, 1);
  });

  it("isTrusted=false 的合成事件被忽略——页面伪造的 copy 没动剪切板，不该取消擦除", () => {
    const env = makeEnv();
    dispatch(env.doc, "copy", copyEvent(KEY_TEXT, { trusted: false }));
    assert.equal(env.runtime.sent.length, 0);
  });

  it("无 clipboardData / getData 抛异常都就地吞掉，clipboard-changed 照发", () => {
    const env = makeEnv();
    dispatch(env.doc, "copy", { isTrusted: true }); // 无 clipboardData
    dispatch(env.doc, "copy", {
      isTrusted: true,
      clipboardData: { getData: () => { throw new Error("页面捣乱"); } },
    });
    assert.equal(messagesOfKind(env.runtime, "clipboard-changed").length, 2);
    assert.equal(messagesOfKind(env.runtime, "capture").length, 0);
  });

  it("大文本走查：200000 字符指纹计算正常完成并送检", () => {
    const env = makeEnv();
    const big = `${"密".repeat(99990)}sk-${"A".repeat(10000)}`.slice(0, 200000);
    dispatch(env.doc, "copy", copyEvent(big));
    const [capture] = messagesOfKind(env.runtime, "capture");
    assert.equal(capture.text, big);
  });
});

describe("去重环（对齐 watch.py:139-142 seen ring）", () => {
  it("同文本重复复制：clipboard-changed 每次都发，capture 只送一次", () => {
    const env = makeEnv();
    dispatch(env.doc, "copy", copyEvent(KEY_TEXT));
    dispatch(env.doc, "copy", copyEvent(KEY_TEXT));
    dispatch(env.doc, "copy", copyEvent(KEY_TEXT));
    assert.equal(messagesOfKind(env.runtime, "clipboard-changed").length, 3);
    assert.equal(messagesOfKind(env.runtime, "capture").length, 1);
  });

  it("不同文本照常送检", () => {
    const env = makeEnv();
    dispatch(env.doc, "copy", copyEvent(KEY_TEXT));
    dispatch(env.doc, "copy", copyEvent(`GITHUB_TOKEN=ghp_${"b".repeat(36)}`));
    assert.equal(messagesOfKind(env.runtime, "capture").length, 2);
  });

  it("环容量 20：被挤出后同文本可再次送检", () => {
    const env = makeEnv();
    const texts = Array.from({ length: 21 }, (_, i) => `sample-key-${String(i).padStart(2, "0")}-xyz`);
    for (const text of texts) dispatch(env.doc, "copy", copyEvent(text));
    assert.equal(messagesOfKind(env.runtime, "capture").length, 21);
    dispatch(env.doc, "copy", copyEvent(texts[0])); // 已被挤出：再送检
    dispatch(env.doc, "copy", copyEvent(texts[20])); // 仍在环内：不送检
    const sentTexts = messagesOfKind(env.runtime, "capture").map((message) => message.text);
    assert.equal(sentTexts.filter((text) => text === texts[0]).length, 2);
    assert.equal(sentTexts.filter((text) => text === texts[20]).length, 1);
  });

  it("环里只存指纹，绝不存原文（watch.py:13-15 同一纪律）", () => {
    const env = makeEnv();
    dispatch(env.doc, "copy", copyEvent(KEY_TEXT));
    assert.equal(env.controller.ring.length, 1);
    const [digest] = env.controller.ring;
    assert.equal(typeof digest, "string");
    assert.ok(!digest.includes("DEEPSEEK"), "环里出现了原文片段");
    assert.equal(digest, kv.fingerprint(KEY_TEXT));
  });

  it("指纹稳定且可区分（含中文/emoji）", () => {
    assert.equal(kv.fingerprint("密钥abc🔑"), kv.fingerprint("密钥abc🔑"));
    assert.notEqual(kv.fingerprint("密钥abc🔑"), kv.fingerprint("密钥abc🙅"));
    assert.notEqual(kv.fingerprint(KEY_TEXT), kv.fingerprint(`${KEY_TEXT} `));
  });
});

// ------------------------------------------------ capture 响应与提示条

describe("capture 响应与提示条", () => {
  it("无 candidate 的响应一律安静（results/disabled/ignored/error/lastError）", () => {
    const env = makeEnv();
    const responses = [
      { results: [{ platform: "deepseek", preview: "sk-***AAAA" }] }, // 0 或 ≥2 条 promptable
      { disabled: true }, // 捕获开关关（C1#5）
      { ignored: true }, // 主机在 ignoredHosts（C1#5）
      { error: "vault 已锁定——先解锁" },
    ];
    let round = 0;
    for (const response of responses) {
      const text = `${KEY_TEXT}-${round}`;
      round += 1;
      dispatch(env.doc, "copy", copyEvent(text));
      env.runtime.respondByKind("capture", response);
      assert.equal(findBar(env.doc), null, `响应 ${JSON.stringify(response)} 不应弹条`);
    }
    dispatch(env.doc, "copy", copyEvent(`${KEY_TEXT}-x`));
    env.runtime.respondByKind("capture", null, "The message port closed"); // lastError
    assert.equal(findBar(env.doc), null);
  });

  it("恰一条 promptable：弹条且 meta 行为 平台 · 键名 · 掩码预览", () => {
    const env = makeEnv();
    const bar = showBar(env, KEY_TEXT, CANDIDATE);
    const shadow = bar.shadow;
    assert.equal(shadow.children[0].tagName, "STYLE");
    assert.ok(shadow.children[0].textContent.includes(":host { all: initial; }"));
    const card = shadow.children[1];
    assert.equal(card.className, "kv-card");
    assert.equal(findByClass(card, "kv-title").textContent, "检测到凭据");
    assert.equal(
      findByClass(card, "kv-meta").textContent,
      "deepseek · DEEPSEEK_API_KEY · sk-***AAAA",
    );
    const row = findByClass(card, "kv-row");
    assert.deepEqual(row.children.map((button) => button.textContent),
      ["存入 KeyVault", "忽略"]);
  });

  it("歧义 sk-（confidence=ambiguous → openai-compatible）显示「厂商未定」", () => {
    const env = makeEnv();
    showBar(env, `sk-${"A".repeat(48)}`, {
      platform: "openai-compatible",
      keyName: null,
      kind: "token",
      confidence: "ambiguous",
      evidence: "形态歧义（openai_compatible）",
      maskStyle: [3, 4],
      preview: "sk-***AAAA",
    });
    const meta = findByClass(findBar(env.doc).shadow, "kv-meta");
    assert.ok(meta.textContent.startsWith("厂商未定 · "), meta.textContent);
    assert.ok(!meta.textContent.includes("openai-compatible"));
  });

  it("键名缺失时 meta 行不留双分隔符", () => {
    assert.equal(
      kv.metaLine({ platform: "deepseek", keyName: null, confidence: "high", preview: "sk-***AAAA" }),
      "deepseek · sk-***AAAA",
    );
  });

  it("同屏至多一条：新候选替换旧条", () => {
    const env = makeEnv();
    showBar(env, KEY_TEXT, CANDIDATE);
    const second = `GITHUB_TOKEN=ghp_${"b".repeat(36)}`;
    showBar(env, second, { ...CANDIDATE, platform: "gitlab", keyName: "GITHUB_TOKEN" });
    const bars = env.doc.documentElement.children.filter((child) => child.id === kv.HOST_ID);
    assert.equal(bars.length, 1);
    assert.equal(findByClass(bars[0].shadow, "kv-meta").textContent,
      "gitlab · GITHUB_TOKEN · sk-***AAAA");
  });

  it("content 脚本对「恰一条」的裁决只认 candidate，不自行筛选", () => {
    // C1#5：SW 返回 {results:[…]} 即「不弹条」——content 不再数 promptable。
    const env = makeEnv();
    dispatch(env.doc, "copy", copyEvent(KEY_TEXT));
    env.runtime.respondByKind("capture", {
      results: [
        { platform: "a", promptable: true, preview: "***" },
        { platform: "b", promptable: true, preview: "***" },
      ],
    });
    assert.equal(findBar(env.doc), null);
  });
});

// ------------------------------------------------------ 存入与忽略

describe("存入与忽略", () => {
  it("[存入] 把原文随 kind:store 重发（C1#6），双击只发一次", () => {
    const env = makeEnv();
    const bar = showBar(env, KEY_TEXT, CANDIDATE);
    const save = findByClass(findByClass(bar.shadow, "kv-row"), "kv-primary");
    save.click();
    save.click();
    const stores = messagesOfKind(env.runtime, "store");
    assert.equal(stores.length, 1);
    assert.deepEqual(stores[0], {
      kind: "store",
      text: KEY_TEXT,
      source_url: "https://example.com/console",
      title: "DeepSeek 开放平台 - Google Chrome",
    });
  });

  it("created → 「已存入：<name>」，3.2s 自灭", () => {
    const env = makeEnv();
    const bar = showBar(env, KEY_TEXT, CANDIDATE);
    findByClass(findByClass(bar.shadow, "kv-row"), "kv-primary").click();
    env.runtime.respondByKind("store", { ok: true, outcome: "created", name: "DeepSeek #1", warning: null });
    const card = findBar(env.doc).shadow.children[1];
    const note = card.children[0];
    assert.equal(note.textContent, "已存入：DeepSeek #1");
    assert.equal(note.className, "kv-note");
    assert.equal(env.timers.jobs.length, 1);
    assert.equal(env.timers.jobs[0].ms, kv.RESULT_TTL_MS);
    assert.equal(kv.RESULT_TTL_MS, 3200);
    env.timers.jobs[0].fn(); // 时间到
    assert.equal(findBar(env.doc), null);
    assert.equal(env.timers.jobs.length, 0); // 自灭后无遗留定时器
  });

  it("deduped → 「已在库里（去重）：<name>」", () => {
    const env = makeEnv();
    const bar = showBar(env, KEY_TEXT, CANDIDATE);
    findByClass(findByClass(bar.shadow, "kv-row"), "kv-primary").click();
    env.runtime.respondByKind("store", { ok: true, outcome: "deduped", name: "DeepSeek #1", warning: null });
    assert.equal(findByClass(findBar(env.doc).shadow, "kv-note").textContent,
      "已在库里（去重）：DeepSeek #1");
  });

  it("warning 附加在结果文案之后", () => {
    const env = makeEnv();
    const bar = showBar(env, KEY_TEXT, CANDIDATE);
    findByClass(findByClass(bar.shadow, "kv-row"), "kv-primary").click();
    env.runtime.respondByKind("store", {
      ok: true, outcome: "deduped", name: "DeepSeek #1", warning: "记录已吊销，重新激活",
    });
    assert.equal(findByClass(findBar(env.doc).shadow, "kv-note").textContent,
      "已在库里（去重）：DeepSeek #1（记录已吊销，重新激活）");
  });

  it("store 出错 → 红色「存入失败：<error>」，同样 3.2s 自灭", () => {
    const env = makeEnv();
    const bar = showBar(env, KEY_TEXT, CANDIDATE);
    findByClass(findByClass(bar.shadow, "kv-row"), "kv-primary").click();
    env.runtime.respondByKind("store", { error: "vault 已锁定——先解锁" });
    const note = findByClass(findBar(env.doc).shadow, "kv-note");
    assert.equal(note.textContent, "存入失败：vault 已锁定——先解锁");
    assert.equal(note.className, "kv-note kv-error");
    assert.equal(env.timers.jobs[0].ms, kv.RESULT_TTL_MS);
  });

  it("store 无响应（lastError）→ 「存入失败：扩展无响应」", () => {
    const env = makeEnv();
    const bar = showBar(env, KEY_TEXT, CANDIDATE);
    findByClass(findByClass(bar.shadow, "kv-row"), "kv-primary").click();
    env.runtime.respondByKind("store", null, "The message port closed");
    assert.equal(findByClass(findBar(env.doc).shadow, "kv-note").textContent,
      "存入失败：扩展无响应");
  });

  it("[忽略] 立即收条且不发 store", () => {
    const env = makeEnv();
    const bar = showBar(env, KEY_TEXT, CANDIDATE);
    findByClass(findByClass(bar.shadow, "kv-row"), "kv-ghost").click();
    assert.equal(findBar(env.doc), null);
    assert.equal(messagesOfKind(env.runtime, "store").length, 0);
    assert.equal(env.timers.jobs.length, 0);
  });

  it("isTrusted=false 的按钮点击无效且不消耗判定（页面代点挡下）", () => {
    const env = makeEnv();
    const bar = showBar(env, KEY_TEXT, CANDIDATE);
    const save = findByClass(findByClass(bar.shadow, "kv-row"), "kv-primary");
    save.click({ isTrusted: false }); // 页面 .click() 代点
    assert.equal(messagesOfKind(env.runtime, "store").length, 0);
    save.click({ isTrusted: true }); // 真用户点击仍可正常存入
    assert.equal(messagesOfKind(env.runtime, "store").length, 1);
  });

  it("copy 后监听器注册在 document 捕获阶段", () => {
    const env = makeEnv();
    assert.equal(env.doc.listeners.get("copy").length, 1);
    assert.equal(env.doc.listeners.get("cut").length, 1);
  });
});

// -------------------------------------------- 迟到回复与会话代次（评审 #1）

describe("迟到回复与会话代次", () => {
  it("在飞的 store 回复迟到时不得覆盖换代后的新候选条", () => {
    const env = makeEnv();
    const barA = showBar(env, KEY_TEXT, CANDIDATE);
    findByClass(findByClass(barA.shadow, "kv-row"), "kv-primary").click(); // A 的 store 在飞
    const second = `GITHUB_TOKEN=ghp_${"b".repeat(36)}`;
    const barB = showBar(env, second, {
      ...CANDIDATE, platform: "gitlab", keyName: "GITHUB_TOKEN", preview: "ghp-***bbbb",
    });
    assert.notEqual(barA, barB, "新候选应替换旧条");
    env.runtime.respondByKind("store", {
      ok: true, outcome: "created", name: "A#1", warning: null,
    }); // A 的迟到回复
    const card = findBar(env.doc).shadow.children[1];
    assert.equal(findByClass(card, "kv-note"), null, "迟到回复不得写入新条");
    assert.equal(findByClass(card, "kv-title").textContent, "检测到凭据");
    assert.equal(findByClass(card, "kv-meta").textContent,
      "gitlab · GITHUB_TOKEN · ghp-***bbbb");
    assert.deepEqual(findByClass(card, "kv-row").children.map((button) => button.textContent),
      ["存入 KeyVault", "忽略"], "新条的操作按钮必须完好");
    assert.equal(env.timers.jobs.length, 0, "迟到回复不得安排自灭定时器");
  });

  it("新条自身的存入流程不受迟到回复影响", () => {
    const env = makeEnv();
    const barA = showBar(env, KEY_TEXT, CANDIDATE);
    findByClass(findByClass(barA.shadow, "kv-row"), "kv-primary").click();
    const second = `GITHUB_TOKEN=ghp_${"b".repeat(36)}`;
    const barB = showBar(env, second, { ...CANDIDATE, platform: "gitlab" });
    env.runtime.respondByKind("store", { ok: true, outcome: "created", name: "A#1" }); // 迟到
    findByClass(findByClass(barB.shadow, "kv-row"), "kv-primary").click(); // B 正常存入
    env.runtime.respondByKind("store", { ok: true, outcome: "created", name: "B#1" });
    assert.equal(findByClass(findBar(env.doc).shadow, "kv-note").textContent, "已存入：B#1");
  });

  it("条已收掉后同代次的结果回复安静落地（防御分支，不建节点不定时器）", () => {
    const doc = fakeDocument("x");
    const timers = fakeTimers();
    const bar = kv.createBar(doc, timers);
    bar.show(CANDIDATE, { onStore: () => {}, onDismiss: () => {} }); // 代次 1
    bar.hide(); // 条先没了（如被清理）
    bar.showResult("已存入：A#1", false, 1); // 迟到回复
    assert.equal(doc.documentElement.children.length, 0, "不得新建任何节点");
    assert.equal(timers.jobs.length, 0, "不得安排自灭定时器");
  });
});

// -------------------------------------------- capture 失败与环回收（评审 #6）

describe("capture 失败与环回收", () => {
  it("通道失败（lastError）→ 回收指纹，同文本重复制重试", () => {
    const env = makeEnv();
    dispatch(env.doc, "copy", copyEvent(KEY_TEXT));
    env.runtime.respondByKind("capture", null, "The message port closed");
    dispatch(env.doc, "copy", copyEvent(KEY_TEXT));
    assert.equal(messagesOfKind(env.runtime, "capture").length, 2, "失败后必须可重试");
  });

  it("SW 内部错误（response.error）→ 回收重试", () => {
    const env = makeEnv();
    dispatch(env.doc, "copy", copyEvent(KEY_TEXT));
    env.runtime.respondByKind("capture", { error: "内部错误" });
    dispatch(env.doc, "copy", copyEvent(KEY_TEXT));
    assert.equal(messagesOfKind(env.runtime, "capture").length, 2);
  });

  it("捕获开关关（disabled）→ 回收重试（重新开启后重复制能捕获）", () => {
    const env = makeEnv();
    dispatch(env.doc, "copy", copyEvent(KEY_TEXT));
    env.runtime.respondByKind("capture", { disabled: true });
    dispatch(env.doc, "copy", copyEvent(KEY_TEXT));
    assert.equal(messagesOfKind(env.runtime, "capture").length, 2);
  });

  it("主机被忽略（ignored）→ 权威落点，重复制不重发", () => {
    const env = makeEnv();
    dispatch(env.doc, "copy", copyEvent(KEY_TEXT));
    env.runtime.respondByKind("capture", { ignored: true });
    dispatch(env.doc, "copy", copyEvent(KEY_TEXT));
    assert.equal(messagesOfKind(env.runtime, "capture").length, 1);
  });

  it("results（0 或 ≥2 条 promptable）→ SW 已裁决，重复制不重发", () => {
    const env = makeEnv();
    dispatch(env.doc, "copy", copyEvent(KEY_TEXT));
    env.runtime.respondByKind("capture", { results: [] });
    dispatch(env.doc, "copy", copyEvent(KEY_TEXT));
    assert.equal(messagesOfKind(env.runtime, "capture").length, 1);
  });

  it("回收只退本次指纹，不影响环内其他文本", () => {
    const env = makeEnv();
    const other = `GITHUB_TOKEN=ghp_${"b".repeat(36)}`;
    dispatch(env.doc, "copy", copyEvent(other)); // 先进环
    env.runtime.respondByKind("capture", { results: [] });
    dispatch(env.doc, "copy", copyEvent(KEY_TEXT)); // 后进环
    env.runtime.respondByKind("capture", null, "port closed"); // KEY_TEXT 回收
    assert.deepEqual(env.controller.ring, [kv.fingerprint(other)]);
  });
});

// -------------------------------------------- 重注入清理与 dispose（评审 #4/#7）

describe("重注入清理与 dispose", () => {
  it("setupCapture 清掉孤儿实例残留的提示条再挂监听", () => {
    const doc = fakeDocument("残迹页");
    const stale = fakeElement("div");
    stale.id = kv.HOST_ID; // 孤儿实例留下的 host
    doc.documentElement.appendChild(stale);
    const keep = fakeElement("div");
    keep.id = "keep-me"; // 页面自己的节点不能误伤
    doc.documentElement.appendChild(keep);
    const runtime = fakeRuntime();
    const controller = kv.setupCapture({
      runtime, document: doc, location: { href: "https://example.com/" },
    });
    assert.equal(stale.parentNode, null, "孤儿残留条应被移除");
    assert.equal(keep.parentNode, doc.documentElement, "页面节点不动");
    assert.equal(doc.listeners.get("copy").length, 1, "新实例照常接管");
    dispatch(doc, "copy", copyEvent(KEY_TEXT)); // 接管后功能可用
    assert.equal(messagesOfKind(runtime, "capture").length, 1);
    controller.dispose();
  });

  it("dispose() 解除 copy/cut 监听", () => {
    const env = makeEnv();
    env.controller.dispose();
    assert.equal(env.doc.listeners.get("copy").length, 0);
    assert.equal(env.doc.listeners.get("cut").length, 0);
    assert.equal(env.runtime.sent.length, 0);
  });
});

// -------------------------------------------------------- 工程检查点

describe("工程检查点（走查硬标准）", () => {
  const source = readFileSync(join(HERE, "..", "content.js"), "utf8");

  it("全文件不出现 innerHTML（review 检查点）", () => {
    assert.ok(!source.includes("innerHTML"), "content.js 里出现了 innerHTML");
  });

  it("不含 import/export——内容脚本必须能被经典 <script> 加载（MV3 无模块内容脚本）", () => {
    const firstCode = source.split("\n")
      .find((line) => line.trim() !== "" && !line.trim().startsWith("//"));
    assert.equal(firstCode, "(() => {", "文件应当是经典 IIFE 形态");
    assert.ok(!/^\s*(?:import|export)\b/m.test(source),
      "content.js 出现了顶层 import/export，浏览器经典脚本加载会直接语法错误");
  });

  it("lastError 纪律：回调里总是先读 chrome.runtime.lastError", () => {
    assert.ok(source.includes("const lastError = chrome.runtime.lastError"),
      "缺少对 chrome.runtime.lastError 的无条件读取——会喷 Unchecked runtime.lastError");
  });

  it("UI 挂 closed Shadow DOM 且 :host all:initial", () => {
    assert.ok(source.includes('attachShadow({ mode: "closed" })'),
      "closed 模式让页面拿不到 shadowRoot，读不到 meta 也摸不到按钮");
    assert.ok(source.includes(":host { all: initial; }"));
  });

  it("node 环境守卫安装：API 经 globalThis 暴露且不触碰真实 DOM/chrome", () => {
    assert.equal(typeof kv, "object");
    for (const name of ["MIN_CHARS", "MAX_CHARS", "RING_SIZE", "RESULT_TTL_MS",
      "extractText", "fingerprint", "platformLabel", "metaLine",
      "createBar", "createCapture", "clearStaleBar", "setupCapture", "installBrowser"]) {
      assert.ok(name in kv, `KeyVaultContent 缺 ${name}`);
    }
    assert.equal(kv.MIN_CHARS, 8);
    assert.equal(kv.MAX_CHARS, 200000);
    assert.equal(kv.RING_SIZE, 20);
  });

  it("extractText 对非文本与异常返回空串", () => {
    assert.equal(kv.extractText(null), "");
    assert.equal(kv.extractText({}), "");
    assert.equal(kv.extractText({ clipboardData: { getData: () => "text" } }), "text");
    assert.equal(kv.extractText({ clipboardData: { getData: () => { throw new Error("x"); } } }), "");
  });
});
