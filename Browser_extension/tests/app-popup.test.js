// app-popup 单测：popup.js 三态门 / 150ms 防抖 / 复制流与文案红线。
//
// 手段：node:test + 本文件内自带的极简 DOM/后台替身（零 npm 依赖），驱动
// popup.js 真实的 createController 与 boot 装配——浏览器真机走查（SW 装载后
// 的三态切换、clipboard-changed 联动擦除取消）归集成阶段，本文件覆盖 node
// 侧可执行的全部行为面。
//
// 对拍基准：
// - 三态门与复制流 —— CONTRACTS.md C1#1/#2/#3/#7/#9/#11；
// - 文案红线 —— 桌面 kv/capture/clipboard.py:42-43（exclude_history=True）
//   在浏览器无等价 API，COPY_NOTICE 必须含「无法排除系统剪贴板历史」；
// - 状态标签 —— 桌面 kv/gui/statbar.py:21 STATUS_LABELS；
// - 搜索空态双形态 —— 桌面 kv/gui/pages/secrets.py refresh/_show_no_match。

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";

import {
  DEBOUNCE_MS,
  COPY_NOTICE,
  COPY_WIPE_FAILED,
  COPY_FAILED,
  deriveView,
  validateNewPassword,
  statusLabel,
  createController,
  boot,
} from "../popup.js";

// ---- 极简替身：DOM 元素 / 文档 / 可控时钟 / 后台 SW / 剪贴板 ----

class FakeEl {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.handlers = new Map();
    this.hidden = false;
    this.textContent = "";
    this.value = "";
    this.className = "";
    this.disabled = false;
    this.title = "";
    this.focused = false;
  }

  addEventListener(type, fn) {
    if (!this.handlers.has(type)) this.handlers.set(type, []);
    this.handlers.get(type).push(fn);
  }

  dispatch(type, event = { preventDefault() {} }) {
    for (const fn of this.handlers.get(type) ?? []) fn(event);
  }

  append(...nodes) {
    this.children.push(...nodes);
  }

  replaceChildren(...nodes) {
    this.children = [...nodes];
  }

  focus() {
    this.focused = true;
  }
}

// popup.js buildUi 引用的全部元素 id（缺一个 boot 就会挂）。
const UI_IDS = [
  "setup-view", "unlock-view", "list-view", "status-dot", "status-text",
  "lock-button", "setup-form", "setup-password", "setup-confirm", "setup-error",
  "unlock-form", "unlock-password", "unlock-error", "search", "list-rows",
  "list-meta", "notice", "version",
];

function makeDocument() {
  const registry = new Map(UI_IDS.map((id) => [id, new FakeEl()]));
  return {
    registry,
    getElementById: (id) => registry.get(id) ?? null,
    createElement: (tag) => new FakeEl(tag),
  };
}

function makeUi() {
  const el = () => new FakeEl();
  return {
    views: { setup: el(), unlock: el(), list: el() },
    statusDot: el(),
    statusText: el(),
    lockButton: el(),
    setupForm: el(),
    setupPassword: el(),
    setupConfirm: el(),
    setupError: el(),
    unlockForm: el(),
    unlockPassword: el(),
    unlockError: el(),
    search: el(),
    listRows: el(),
    listMeta: el(),
    notice: el(),
    version: el(),
  };
}

function makeTimers() {
  let now = 0;
  let seq = 0;
  const jobs = new Map();
  return {
    setTimeout(fn, ms) {
      seq += 1;
      jobs.set(seq, { fn, at: now + ms });
      return seq;
    },
    clearTimeout(id) {
      jobs.delete(id);
    },
    advance(ms) {
      now += ms;
      for (const [id, job] of [...jobs]) {
        if (job.at <= now) {
          jobs.delete(id);
          job.fn();
        }
      }
    },
    get pending() {
      return jobs.size;
    },
  };
}

function makeClipboard({ reject = false } = {}) {
  const writes = [];
  return {
    writes,
    writeText: async (text) => {
      if (reject) throw new Error("Document is not focused.");
      writes.push(text);
    },
  };
}

// 与 CONTRACTS.md C1 动作表同形的后台替身。锁定语义只对齐真实实现：
// reveal 有 assertUnlocked（store.js:485）→ {error,locked:true}；
// list 不要求解锁（hList→listSecrets，store.js:437 无锁定检查，C1#7 无
// 锁定声明）→ 锁定态照样返回 {rows}。绝不自造替身特有行为。
function makeSW(options = {}) {
  const state = {
    initialized: options.initialized ?? false,
    unlocked: false,
    version: "1.0.0",
  };
  const rows = options.rows ?? [
    { id: 1, name: "GitHub PAT", platform: "github", status: "active", preview: "ghp_****abcd" },
    { id: 2, name: "云服务器 SSH", platform: "server", status: "active", preview: "sk-****ef12" },
    { id: 3, name: "旧令牌", platform: "legacy", status: "revoked", preview: "tok-****90ab" },
  ];
  const values = { 1: "ghp_realvalue111", 2: "sk-realvalue222", 3: "tok-realvalue333" };
  const calls = [];
  let gate = null;

  function handle(msg) {
    calls.push({ ...msg });
    switch (msg.kind) {
      case "status":
        return {
          initialized: state.initialized,
          unlocked: state.unlocked,
          settings: { autoLockMinutes: options.autoLockMinutes ?? 15, captureEnabled: true, ignoredHosts: [] },
          version: state.version,
        };
      case "setup":
        if (options.setupError) return { error: options.setupError };
        if (typeof msg.password !== "string" || msg.password.length < 8) return { error: "主密码至少 8 位" };
        state.initialized = true;
        state.unlocked = true;
        return { ok: true };
      case "unlock":
        if (msg.password !== (options.masterPassword ?? "correct horse")) {
          return { error: "主密码不正确", locked: true };
        }
        state.unlocked = true;
        return { ok: true };
      case "lock":
        if (options.lockError) return { error: options.lockError };
        state.unlocked = false;
        return { ok: true };
      case "list": {
        const q = (msg.query ?? "").toLowerCase();
        return { rows: rows.filter((r) => !q || r.name.toLowerCase().includes(q)) };
      }
      case "reveal": {
        if (!state.unlocked) return { error: "vault 已锁定——先解锁", locked: true };
        const row = rows.find((r) => r.id === msg.id);
        return { value: values[msg.id] ?? "", name: row ? row.name : "" };
      }
      case "copied":
        // "copiedResponse" in options：可显式给 undefined 模拟「SW 无响应」
        return "copiedResponse" in options ? options.copiedResponse : { ok: true, ttl: 30 };
      default:
        return { error: `未知动作 ${msg.kind}` };
    }
  }

  return {
    calls,
    state,
    rows,
    send: (msg) => {
      if (gate) {
        const g = gate;
        gate = null;
        const result = handle(msg);
        return g.then(() => result);
      }
      return Promise.resolve(handle(msg));
    },
    // 挂起下一次 send（同步记账、延迟放行），返回 release()。
    holdNext() {
      let release;
      gate = new Promise((resolve) => {
        release = resolve;
      });
      return release;
    },
  };
}

function makeRowFactory() {
  const made = [];
  const factory = (onCopy) => {
    const root = new FakeEl("button");
    root.className = "row";
    const name = new FakeEl("span");
    const preview = new FakeEl("span");
    const meta = new FakeEl("span");
    const tag = new FakeEl("span");
    tag.hidden = true;
    root.append(name, preview, meta, tag);
    root.addEventListener("click", () => onCopy());
    const view = { root, name, preview, meta, tag };
    made.push(view);
    return view;
  };
  factory.made = made;
  return factory;
}

function makeController({ sw, clipboard, ui, noticeMs } = {}) {
  const timers = makeTimers();
  const uiArg = ui ?? makeUi();
  const rowFactory = makeRowFactory();
  const controller = createController({
    send: sw.send,
    ui: uiArg,
    createRowView: rowFactory,
    clipboardWrite: (clipboard ?? makeClipboard()).writeText,
    timers,
    ...(noticeMs !== undefined ? { noticeMs } : {}),
  });
  return { controller, ui: uiArg, timers, rowFactory };
}

// 常规前置：未初始化 → init 进 setup → 提交合法主密码 → 进入列表态。
async function setupList({ sw, clipboard, noticeMs } = {}) {
  const swArg = sw ?? makeSW();
  const clipboardArg = clipboard ?? makeClipboard();
  const { controller, ui, timers } = makeController({ sw: swArg, clipboard: clipboardArg, noticeMs });
  await controller.init();
  ui.setupPassword.value = "long-enough-1";
  ui.setupConfirm.value = "long-enough-1";
  await controller.submitSetup();
  return { controller, ui, sw: swArg, timers, clipboard: clipboardArg };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));
const listCalls = (sw) => sw.calls.filter((c) => c.kind === "list");

// ---- 一、源码红线 ----

describe("源码红线（验收 1/3）", () => {
  const jsPath = new URL("../popup.js", import.meta.url);
  const htmlPath = new URL("../popup.html", import.meta.url);
  const cssPath = new URL("../popup.css", import.meta.url);

  it("三个独占文件齐备且非空", () => {
    for (const path of [htmlPath, cssPath, jsPath]) {
      assert.ok(existsSync(path), `${path.pathname} 应存在`);
      assert.ok(readFileSync(path, "utf8").length > 0);
    }
  });

  it("popup.js 不出现 innerHTML / outerHTML / insertAdjacentHTML / document.write", () => {
    const source = readFileSync(jsPath, "utf8");
    for (const banned of ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"]) {
      assert.ok(!source.includes(banned), `popup.js 不得出现 ${banned}`);
    }
    assert.ok(source.includes("textContent"), "行内容一律走 textContent");
  });

  it("popup.html 三个密码输入框（setup 主密码+确认、unlock 主密码）均为 type=password，且由表单包裹（Enter 原生提交）", () => {
    const html = readFileSync(htmlPath, "utf8");
    const inputs = [...html.matchAll(/<input ([^>]*?)>/g)].map((m) => m[1]);
    const passwordFields = inputs.filter((attrs) => attrs.includes('type="password"'));
    assert.equal(passwordFields.length, 3, "setup 两个 + unlock 一个");
    for (const id of ["setup-password", "setup-confirm", "unlock-password"]) {
      const attrs = inputs.find((a) => a.includes(`id="${id}"`));
      assert.ok(attrs, `popup.html 缺少 #${id}`);
      assert.ok(attrs.includes('type="password"'), `#${id} 必须是 type=password`);
    }
    assert.ok(html.includes('id="setup-form"') && html.includes('id="unlock-form"'), "表单包裹是 Enter 提交的载体");
  });

  it("popup.html 覆盖 popup.js 引用的全部元素 id", () => {
    const html = readFileSync(htmlPath, "utf8");
    for (const id of UI_IDS) {
      assert.ok(html.includes(`id="${id}"`), `popup.html 缺少 id="${id}"`);
    }
    assert.ok(html.includes('<script type="module" src="popup.js">'), "以 module 脚本装载 popup.js");
  });

  it("popup.css 双主题齐备（prefers-color-scheme + 明暗变量）", () => {
    const css = readFileSync(cssPath, "utf8");
    assert.ok(css.includes("prefers-color-scheme"), "应包含暗色媒体查询");
    assert.ok(css.includes("--fg") && css.includes("--bg") && css.includes("--elev"), "变量单源");
    assert.ok(css.includes("dot-uninit") && css.includes("dot-locked") && css.includes("dot-ok"), "三态状态灯样式齐备");
  });

  it("manifest.json 的 default_popup 指向 popup.html（app-package 落地前跳过）", (t) => {
    const manifestPath = new URL("../manifest.json", import.meta.url);
    if (!existsSync(manifestPath)) {
      t.skip("manifest.json 尚未创建（归 app-package），default_popup 接线留待集成批");
      return;
    }
    const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
    const target = manifest.action?.default_popup ?? manifest.default_popup;
    assert.equal(target, "popup.html");
  });
});

// ---- 二、纯函数 ----

describe("三态推导与本地校验", () => {
  it("deriveView 三态门：未初始化→setup，已锁→unlock，已解锁→list", () => {
    assert.equal(deriveView({ initialized: false, unlocked: false }), "setup");
    assert.equal(deriveView({ initialized: true, unlocked: false }), "unlock");
    assert.equal(deriveView({ initialized: true, unlocked: true }), "list");
  });

  it("validateNewPassword：<8 位、两次不一致、通过 三分支", () => {
    assert.equal(validateNewPassword("short1", "short1"), "主密码至少 8 位");
    assert.equal(validateNewPassword("12345678", "123456789"), "两次输入的主密码不一致");
    assert.equal(validateNewPassword("12345678", "12345678"), "");
  });

  it("statusLabel 对齐桌面 STATUS_LABELS（statbar.py:21）", () => {
    assert.equal(statusLabel("active"), "启用");
    assert.equal(statusLabel("revoked"), "已吊销");
    assert.equal(statusLabel("expired"), "已过期");
    assert.equal(statusLabel("mystery"), "mystery");
  });

  it("防抖常量 150ms；文案红线常量逐字校验", () => {
    assert.equal(DEBOUNCE_MS, 150);
    assert.equal(COPY_NOTICE, "已复制，约 30 秒后自动覆盖剪贴板（无法排除系统剪贴板历史）");
    assert.ok(COPY_NOTICE.includes("无法排除系统剪贴板历史"));
    assert.equal(COPY_WIPE_FAILED, "已复制，但 30 秒自动擦除安排失败");
    assert.equal(COPY_FAILED, "复制失败");
  });
});

// ---- 三、控制器行为（createController + 替身） ----

describe("未初始化态：引导设主密码", () => {
  it("init 后显示 setup 视图，状态灯为未初始化，焦点落主密码框", async () => {
    const { controller, ui } = makeController({ sw: makeSW() });
    await controller.init();
    assert.equal(ui.views.setup.hidden, false);
    assert.equal(ui.views.unlock.hidden, true);
    assert.equal(ui.views.list.hidden, true);
    assert.equal(ui.statusText.textContent, "未初始化");
    assert.equal(ui.statusDot.className, "dot dot-uninit");
    assert.equal(ui.lockButton.hidden, true);
    assert.equal(ui.setupPassword.focused, true);
  });

  it("少于 8 位：本地拦截，不发 setup 消息", async () => {
    const sw = makeSW();
    const { controller, ui } = makeController({ sw });
    await controller.init();
    ui.setupPassword.value = "short1";
    ui.setupConfirm.value = "short1";
    await controller.submitSetup();
    assert.equal(ui.setupError.hidden, false);
    assert.equal(ui.setupError.textContent, "主密码至少 8 位");
    assert.ok(!sw.calls.some((c) => c.kind === "setup"));
  });

  it("两次输入不一致：本地拦截，不发 setup 消息", async () => {
    const sw = makeSW();
    const { controller, ui } = makeController({ sw });
    await controller.init();
    ui.setupPassword.value = "12345678";
    ui.setupConfirm.value = "123456789";
    await controller.submitSetup();
    assert.equal(ui.setupError.textContent, "两次输入的主密码不一致");
    assert.ok(!sw.calls.some((c) => c.kind === "setup"));
  });

  it("合法提交：发 setup → 重拉 status → 直接进主界面（列表 + 状态灯已解锁 + 页脚版本）", async () => {
    const sw = makeSW();
    const { controller, ui } = makeController({ sw });
    await controller.init();
    ui.setupPassword.value = "long-enough-1";
    ui.setupConfirm.value = "long-enough-1";
    await controller.submitSetup();
    const setupCall = sw.calls.find((c) => c.kind === "setup");
    assert.deepEqual(setupCall, { kind: "setup", password: "long-enough-1" });
    assert.equal(ui.views.list.hidden, false);
    assert.equal(ui.views.setup.hidden, true);
    assert.equal(ui.statusText.textContent, "已解锁");
    assert.equal(ui.statusDot.className, "dot dot-ok");
    assert.equal(ui.lockButton.hidden, false);
    assert.equal(ui.version.textContent, "v1.0.0 · 闲置 15 分钟自动锁定");
    assert.equal(ui.listRows.children.length, 3);
    assert.equal(ui.setupPassword.value, "");
    assert.equal(ui.setupConfirm.value, "");
  });

  it("SW 侧 setup 返回 error：内联展示错误、留在 setup 视图、不清空已输口令", async () => {
    const sw = makeSW({ setupError: "vault 已初始化" });
    const { controller, ui } = makeController({ sw });
    await controller.init();
    ui.setupPassword.value = "long-enough-1";
    ui.setupConfirm.value = "long-enough-1";
    await controller.submitSetup();
    assert.ok(sw.calls.some((c) => c.kind === "setup"), "本地校验已过，请求应发出");
    assert.equal(ui.setupError.hidden, false);
    assert.equal(ui.setupError.textContent, "vault 已初始化");
    assert.equal(ui.views.setup.hidden, false);
    assert.equal(ui.views.list.hidden, true);
    assert.equal(ui.setupPassword.value, "long-enough-1", "SW 拒绝时保留已输口令供重试");
  });

  it("renderFooter：autoLockMinutes=0 显示「自动锁定已禁用」", async () => {
    const sw = makeSW({ autoLockMinutes: 0 });
    const { controller, ui } = makeController({ sw });
    await controller.init();
    ui.setupPassword.value = "long-enough-1";
    ui.setupConfirm.value = "long-enough-1";
    await controller.submitSetup();
    assert.equal(ui.version.textContent, "v1.0.0 · 自动锁定已禁用");
  });
});

describe("掩码列表", () => {
  it("行渲染：名称/掩码预览/平台 textContent，吊销行带状态标签，点击句柄绑定到行", async () => {
    const sw = makeSW({ initialized: true });
    sw.state.unlocked = true;
    const { controller, ui, rowFactory } = makeController({ sw });
    await controller.init();
    assert.equal(ui.listRows.children.length, 3);
    // 断言走视图对象引用而非替身 DOM 结构——语义对任何工厂形状都成立
    const [first, , revoked] = rowFactory.made;
    assert.equal(first.root.className, "row");
    assert.equal(first.root.title, "点击复制「GitHub PAT」");
    assert.equal(first.name.textContent, "GitHub PAT");
    assert.equal(first.preview.textContent, "ghp_****abcd");
    assert.equal(first.meta.textContent, "github");
    assert.equal(revoked.root.title, "点击复制「旧令牌」");
    assert.equal(revoked.tag.hidden, false);
    assert.equal(revoked.tag.textContent, "已吊销");
    assert.equal(revoked.tag.className, "tag tag-revoked");
    assert.equal(first.tag.hidden, true, "active 行不带状态标签");
    assert.equal(ui.listMeta.textContent, "3 条记录");
  });

  it("空库与无匹配两种空态文案（对齐桌面 secrets.py 双态）", async () => {
    const sw = makeSW({ rows: [] });
    const { controller, ui, timers } = makeController({ sw });
    await controller.init();
    ui.setupPassword.value = "long-enough-1";
    ui.setupConfirm.value = "long-enough-1";
    await controller.submitSetup();
    assert.equal(ui.listMeta.textContent, "库中还没有记录——在网页上复制密钥时会提示捕获");
    assert.ok(ui.listMeta.className.includes("list-empty"));
    ui.search.value = "zzz";
    controller.onSearchInput();
    timers.advance(DEBOUNCE_MS);
    await settle();
    assert.equal(ui.listMeta.textContent, "没有匹配的记录");
  });
});

describe("搜索防抖（150ms）", () => {
  it("每次击键重开 150ms 窗口（对齐桌面 secrets.py:117-125 语义）；窗口期满只发最后一次", async () => {
    const { controller, ui, sw, timers } = await setupList();
    const before = listCalls(sw).length;
    ui.search.value = "git";
    controller.onSearchInput();
    timers.advance(100);
    assert.equal(listCalls(sw).length, before, "150ms 未满不应发查询");
    ui.search.value = "github";
    controller.onSearchInput();
    timers.advance(50);
    assert.equal(listCalls(sw).length, before, "击键重开窗口：距上次击键仍不足 150ms");
    timers.advance(100);
    await settle();
    const added = listCalls(sw).slice(before);
    assert.equal(added.length, 1, "两次击键折叠为一次查询");
    assert.equal(added[0].query, "github");
    assert.equal(ui.listRows.children.length, 1, "命中 GitHub PAT 一行");
    assert.equal(ui.listMeta.textContent, "1 条记录");
  });

  it("窗口内连击只保留最后一次输入", async () => {
    const { controller, ui, sw, timers } = await setupList();
    const before = listCalls(sw).length;
    ui.search.value = "gi";
    controller.onSearchInput();
    ui.search.value = "git";
    controller.onSearchInput();
    ui.search.value = "github";
    controller.onSearchInput();
    timers.advance(DEBOUNCE_MS);
    await settle();
    const added = listCalls(sw).slice(before);
    assert.equal(added.length, 1);
    assert.equal(added[0].query, "github");
  });

  it("Esc 清空：取消在途防抖、立即回全表查询", async () => {
    const { controller, ui, sw, timers } = await setupList();
    const before = listCalls(sw).length;
    ui.search.value = "git";
    controller.onSearchInput();
    controller.clearSearch();
    await settle();
    assert.equal(ui.search.value, "");
    const added = listCalls(sw).slice(before);
    assert.equal(added.length, 1);
    assert.equal(added[0].query, "");
    assert.equal(ui.listRows.children.length, 3);
    timers.advance(DEBOUNCE_MS);
    assert.equal(listCalls(sw).length, before + 1, "清空后在途防抖不应再发查询");
  });

  it("过期响应丢弃：慢查询后到，不得覆盖新列表", async () => {
    const sw = makeSW({
      rows: [
        { id: 1, name: "Delta One", platform: "d", status: "active", preview: "d-****01" },
        { id: 2, name: "Delta Two", platform: "d", status: "active", preview: "d-****02" },
        { id: 3, name: "Omega Key", platform: "o", status: "active", preview: "o-****03" },
      ],
    });
    const { controller, ui, timers } = await setupList({ sw });
    const releaseA = sw.holdNext(); // 挂起 query="delta" 的 list（两行）
    ui.search.value = "delta";
    controller.onSearchInput();
    timers.advance(DEBOUNCE_MS);
    const releaseB = sw.holdNext(); // 挂起 query="omega" 的 list（一行）
    ui.search.value = "omega";
    controller.onSearchInput();
    timers.advance(DEBOUNCE_MS);
    releaseA(); // 旧查询后到（SW 替身回放自身计算结果：Delta 两行），应被丢弃
    await settle();
    releaseB(); // 新查询后完成，应胜出
    await settle();
    assert.equal(ui.listRows.children.length, 1, "最终列表应是最后一次查询的结果");
    assert.equal(ui.listRows.children[0].children[0].textContent, "Omega Key");
  });
});

describe("复制流", () => {
  it("点击行：reveal(event:copy) → 写剪贴板 → copied → 提示含文案红线", async () => {
    const { ui, sw, clipboard, timers } = await setupList();
    ui.listRows.children[0].dispatch("click");
    await settle();
    const reveal = sw.calls.find((c) => c.kind === "reveal");
    assert.deepEqual(reveal, { kind: "reveal", id: 1, event: "copy" });
    assert.deepEqual(clipboard.writes, ["ghp_realvalue111"]);
    assert.ok(sw.calls.some((c) => c.kind === "copied"), "应让 SW 安排擦除");
    assert.equal(ui.notice.hidden, false);
    assert.equal(ui.notice.textContent, COPY_NOTICE);
    assert.ok(ui.notice.textContent.includes("无法排除系统剪贴板历史"));
    assert.equal(ui.notice.className, "notice");
    timers.advance(3000);
    assert.equal(ui.notice.hidden, true);
    assert.equal(ui.notice.textContent, "");
  });

  it("剪贴板写入失败：不发 copied，提示复制失败", async () => {
    const { ui, sw, clipboard } = await setupList({ clipboard: makeClipboard({ reject: true }) });
    ui.listRows.children[0].dispatch("click");
    await settle();
    assert.equal(clipboard.writes.length, 0);
    assert.ok(!sw.calls.some((c) => c.kind === "copied"));
    assert.ok(ui.notice.textContent.startsWith(COPY_FAILED), `实际文案：${ui.notice.textContent}`);
    assert.equal(ui.notice.className, "notice notice-error");
  });

  it("copied 响应带 error：复制成功但明示擦除安排失败", async () => {
    const { ui } = await setupList({ sw: makeSW({ copiedResponse: { error: "alarm 建立失败" } }) });
    ui.listRows.children[0].dispatch("click");
    await settle();
    assert.equal(ui.notice.textContent, COPY_WIPE_FAILED);
  });

  it("copied 无响应（SW 已死/通道断）：绝不显示「约 30 秒后自动覆盖」的虚假承诺", async () => {
    // chrome.runtime.sendMessage 在无监听方响应时 resolve undefined
    const { ui } = await setupList({ sw: makeSW({ copiedResponse: undefined }) });
    ui.listRows.children[0].dispatch("click");
    await settle();
    assert.equal(ui.notice.textContent, COPY_WIPE_FAILED);
    assert.ok(!ui.notice.textContent.includes("无法排除系统剪贴板历史"), "失败文案不承诺擦除时限");
    assert.ok(ui.notice.textContent.includes("擦除"), "但必须如实说明擦除未安排");
    assert.equal(ui.notice.className, "notice notice-error");
  });

  it("reveal 期间已锁：不写剪贴板，切回解锁态并提示锁定错误", async () => {
    const { ui, sw, clipboard } = await setupList();
    sw.state.unlocked = false; // SW 自动锁定（C7 超时）；reveal 有 assertUnlocked（store.js:485）
    ui.listRows.children[0].dispatch("click");
    await settle();
    assert.equal(clipboard.writes.length, 0);
    assert.ok(!sw.calls.some((c) => c.kind === "copied"));
    assert.equal(ui.views.unlock.hidden, false);
    assert.equal(ui.views.list.hidden, true);
    assert.equal(ui.statusText.textContent, "已锁定");
    assert.equal(ui.notice.textContent, "vault 已锁定——先解锁");
  });

  it("复制进行中忽略再次点击（单飞防抖）", async () => {
    const { ui, sw, clipboard } = await setupList();
    const release = sw.holdNext(); // 挂起第一次 reveal
    ui.listRows.children[0].dispatch("click");
    await settle();
    ui.listRows.children[1].dispatch("click"); // copyBusy，应被忽略
    await settle();
    assert.equal(sw.calls.filter((c) => c.kind === "reveal").length, 1);
    release();
    await settle();
    assert.deepEqual(clipboard.writes, ["ghp_realvalue111"]);
    assert.equal(sw.calls.filter((c) => c.kind === "copied").length, 1);
  });
});

describe("锁定与解锁", () => {
  it("锁定按钮：发 lock → 重拉 status → 回解锁态，状态灯变红、锁定钮隐藏", async () => {
    const { controller, ui, sw } = await setupList();
    await controller.lock();
    assert.ok(sw.calls.some((c) => c.kind === "lock"));
    assert.equal(ui.views.unlock.hidden, false);
    assert.equal(ui.views.list.hidden, true);
    assert.equal(ui.statusText.textContent, "已锁定");
    assert.equal(ui.statusDot.className, "dot dot-locked");
    assert.equal(ui.lockButton.hidden, true);
    assert.equal(ui.unlockPassword.focused, true);
  });

  it("锁定响应带 error：留在当前态并提示", async () => {
    const { controller, ui } = await setupList({ sw: makeSW({ lockError: "锁定失败" }) });
    await controller.lock();
    assert.equal(ui.views.list.hidden, false, "锁定失败应留在列表态");
    assert.equal(ui.notice.textContent, "锁定失败");
    assert.equal(ui.notice.className, "notice notice-error");
  });

  it("已锁态初始化进 unlock 视图；错口令提示并清空输入；对口令解锁进列表", async () => {
    const sw = makeSW({ initialized: true, masterPassword: "correct horse" });
    const { controller, ui } = makeController({ sw });
    await controller.init();
    assert.equal(ui.views.unlock.hidden, false);
    assert.equal(ui.statusText.textContent, "已锁定");
    assert.equal(ui.unlockPassword.focused, true);
    ui.unlockPassword.value = "wrong-pass";
    await controller.submitUnlock();
    assert.equal(ui.unlockError.hidden, false);
    assert.equal(ui.unlockError.textContent, "主密码不正确");
    assert.equal(ui.unlockPassword.value, "", "错口令后应清空输入");
    assert.equal(ui.views.unlock.hidden, false);
    ui.unlockPassword.value = "correct horse";
    await controller.submitUnlock();
    assert.equal(ui.views.list.hidden, false);
    assert.equal(ui.statusText.textContent, "已解锁");
    assert.equal(ui.unlockError.hidden, true);
    assert.equal(ui.listRows.children.length, 3);
  });

  it("空口令本地拦截「请输入主密码」，不发 unlock", async () => {
    const sw = makeSW({ initialized: true });
    const { controller, ui } = makeController({ sw });
    await controller.init();
    await controller.submitUnlock();
    assert.equal(ui.unlockError.textContent, "请输入主密码");
    assert.ok(!sw.calls.some((c) => c.kind === "unlock"));
  });
});

// ---- 三·五、主提交异常兜底（attempt 包装 send reject） ----

describe("主提交异常兜底（attempt）", () => {
  // 评审实证场景：扩展上下文失效时 send reject，此前异常穿透、UI 毫无反应。
  it("unlock 的 send reject：提示「操作失败：…」，留在解锁视图，不抛出", async () => {
    const inner = makeSW({ initialized: true });
    const sw = {
      calls: inner.calls,
      send: (msg) => (msg.kind === "unlock"
        ? Promise.reject(new Error("Extension context invalidated."))
        : inner.send(msg)),
    };
    const { controller, ui } = makeController({ sw });
    await controller.init();
    ui.unlockPassword.value = "whatever";
    await controller.submitUnlock();
    assert.equal(ui.notice.hidden, false, "UI 必须有反馈");
    assert.equal(ui.notice.textContent, "操作失败：Extension context invalidated.");
    assert.equal(ui.notice.className, "notice notice-error");
    assert.equal(ui.views.unlock.hidden, false, "失败留在解锁视图");
  });

  it("setup 的 send reject：同样以提示条反馈", async () => {
    const inner = makeSW();
    const sw = {
      calls: inner.calls,
      send: (msg) => (msg.kind === "setup" ? Promise.reject(new Error("消息通道断开")) : inner.send(msg)),
    };
    const { controller, ui } = makeController({ sw });
    await controller.init();
    ui.setupPassword.value = "long-enough-1";
    ui.setupConfirm.value = "long-enough-1";
    await controller.submitSetup();
    assert.equal(ui.notice.textContent, "操作失败：消息通道断开");
    assert.equal(ui.views.setup.hidden, false);
  });

  it("lock 的 send reject：以提示条反馈，留在列表态", async () => {
    const inner = makeSW();
    const sw = {
      calls: inner.calls,
      send: (msg) => (msg.kind === "lock" ? Promise.reject(new Error("锁定通道失效")) : inner.send(msg)),
    };
    const { controller, ui } = makeController({ sw });
    await controller.init();
    ui.setupPassword.value = "long-enough-1";
    ui.setupConfirm.value = "long-enough-1";
    await controller.submitSetup();
    await controller.lock();
    assert.equal(ui.notice.textContent, "操作失败：锁定通道失效");
    assert.equal(ui.views.list.hidden, false, "锁定失败留在列表态");
    assert.equal(ui.statusText.textContent, "已解锁", "状态灯不误报");
  });
});

// ---- 四、boot 装配（事件接线走真实 boot） ----

describe("boot 装配", () => {
  it("init 拉状态进三态门；表单 submit / 搜索 input+Esc / 锁定点击全部接上", async () => {
    const doc = makeDocument();
    const sw = makeSW();
    const timers = makeTimers();
    boot({ document: doc, send: sw.send, timers, clipboardWrite: async () => {} });
    await settle();
    const reg = doc.registry;
    assert.equal(reg.get("setup-view").hidden, false, "未初始化应显示 setup 视图");
    assert.equal(reg.get("status-text").textContent, "未初始化");

    // 表单 submit（浏览器里由 Enter 触发）→ setup 流
    reg.get("setup-password").value = "long-enough-1";
    reg.get("setup-confirm").value = "long-enough-1";
    reg.get("setup-form").dispatch("submit");
    await settle();
    assert.equal(reg.get("list-view").hidden, false);
    assert.equal(reg.get("list-rows").children.length, 3);
    assert.ok(sw.calls.some((c) => c.kind === "setup"));
    // 真实 makeRowView 结构：root > [name, preview, side > [meta, tag]]，
    // 吊销行的 tag 在 side 内——装配层结构断言，防止只对替身形状成立
    const revokedRow = reg.get("list-rows").children[2];
    assert.equal(revokedRow.children[2].className, "row-side");
    const realTag = revokedRow.children[2].children[1];
    assert.equal(realTag.hidden, false);
    assert.equal(realTag.textContent, "已吊销");
    assert.equal(realTag.className, "tag tag-revoked");

    // input 事件 → 防抖查询
    const before = listCalls(sw).length;
    const search = reg.get("search");
    search.value = "git";
    search.dispatch("input");
    timers.advance(DEBOUNCE_MS);
    await settle();
    assert.equal(listCalls(sw).slice(before).length, 1);
    assert.equal(listCalls(sw).at(-1).query, "git");

    // Esc keydown → 立即清空回全表
    search.dispatch("keydown", { key: "Escape", preventDefault() {} });
    await settle();
    assert.equal(search.value, "");
    assert.equal(listCalls(sw).at(-1).query, "");

    // 锁定按钮 click → 回解锁态
    reg.get("lock-button").dispatch("click");
    await settle();
    assert.equal(reg.get("unlock-view").hidden, false);
    assert.equal(reg.get("status-text").textContent, "已锁定");
  });

  it("boot 装配后行点击走完整复制流", async () => {
    const doc = makeDocument();
    const sw = makeSW();
    const timers = makeTimers();
    const clipboard = makeClipboard();
    boot({ document: doc, send: sw.send, timers, clipboardWrite: clipboard.writeText });
    await settle();
    const reg = doc.registry;
    reg.get("setup-password").value = "long-enough-1";
    reg.get("setup-confirm").value = "long-enough-1";
    reg.get("setup-form").dispatch("submit");
    await settle();
    reg.get("list-rows").children[0].dispatch("click");
    await settle();
    assert.deepEqual(clipboard.writes, ["ghp_realvalue111"]);
    assert.equal(reg.get("notice").textContent, COPY_NOTICE);
    timers.advance(3000);
    assert.equal(reg.get("notice").hidden, true);
  });

  it("boot 默认依赖缺 document 时（node 环境）不装配、可安全 import", async () => {
    // 本文件顶部 import popup.js 时 document 未定义——能走到这里即证明无崩溃。
    assert.equal(typeof boot, "function");
  });
});
