// KeyVault 工具栏弹窗（app-popup 独占）。
//
// 三态门：未初始化 → 引导设主密码（≥8 位）；已锁 → 解锁；已解锁 →
// 搜索（150ms 防抖）+ 掩码列表 + 点击复制。对位桌面 kv/gui/pages/secrets.py
// 的「搜索 → 列表 → 复制」最小形态（桌面 120ms 防抖在弹窗链路上放宽为
// 150ms）；tkinter GUI 整体不移植 —— popup + options 即浏览器形态的 UI 全集。
//
// 复制流（CONTRACTS.md C1#9/#11）：reveal{event:"copy"} 取明文 →
// navigator.clipboard.writeText（popup 有用户手势与焦点，无需 offscreen）→
// copied 让 SW 安排 30 秒 TTL 擦除。桌面 kv copy 的 Win+V/云剪切板排除
// （kv/capture/clipboard.py:42-43 exclude_history=True）在浏览器没有等价
// API —— 文案必须如实声明「无法排除系统剪贴板历史」，不得宣称等同桌面
// 安全级（C9#7）；页面侧再复制内容由内容脚本发 clipboard-changed 取消擦除。
//
// DOM 纪律（验收 3）：全部以 textContent / className / hidden / value 赋值
// 渲染，不做任何 HTML 注入式赋值；主密码输入框 type="password"；
// 表单包裹让 Enter 原生提交。
//
// 可测性接缝：createController / boot 的依赖（send、timers、clipboardWrite、
// document）均可注入，浏览器内 boot() 以默认依赖装配；node 测试
// （tests/app-popup.test.js）用极简替身驱动同一份控制器逻辑。

import { generate as generatePassword, resolveOptions } from "./src/vault/generator.js";

export const DEBOUNCE_MS = 150;
export const NOTICE_MS = 3000;

// 文案红线：桌面 kv/capture/clipboard.py:43 的 exclude_history=True
// 在 navigator.clipboard 上没有对应物，此处不得省略括号内的限制声明。
export const COPY_NOTICE = "已复制，约 30 秒后自动覆盖剪贴板（无法排除系统剪贴板历史）";
export const COPY_WIPE_FAILED = "已复制，但 30 秒自动擦除安排失败";
export const COPY_FAILED = "复制失败";
const NO_RESPONSE = "后台服务无响应";
const PASSWORD_TOO_SHORT = "主密码至少 8 位";
const PASSWORD_MISMATCH = "两次输入的主密码不一致";
const PASSWORD_REQUIRED = "请输入主密码";
const MIN_PASSWORD_LEN = 8;
const LOCKED_FALLBACK = "vault 已锁定——先解锁";

// 状态标签对齐桌面 kv/gui/statbar.py:21 的 STATUS_LABELS。
const STATUS_LABELS = { active: "启用", revoked: "已吊销", expired: "已过期" };
const VIEWS = ["setup", "unlock", "list"];

/** 由 status 响应推导弹窗应显示的三态之一。 */
export function deriveView(status) {
  if (!status || !status.initialized) return "setup";
  if (!status.unlocked) return "unlock";
  return "list";
}

/** 创建密钥库前的本地校验：≥8 位 + 两次一致；返回空串表示通过。 */
export function validateNewPassword(password, confirm) {
  if (password.length < MIN_PASSWORD_LEN) return PASSWORD_TOO_SHORT;
  if (password !== confirm) return PASSWORD_MISMATCH;
  return "";
}

export function statusLabel(status) {
  return STATUS_LABELS[status] ?? status;
}

/**
 * 弹窗控制器。ui 的元素只用到这个窄接口：
 * textContent / className / hidden / value / focus() / replaceChildren()。
 *
 * deps: { send, ui, createRowView, clipboardWrite, timers?, noticeMs? }
 */
export function createController(deps) {
  const send = deps.send;
  const ui = deps.ui;
  const createRowView = deps.createRowView;
  const clipboardWrite = deps.clipboardWrite;
  const timers = deps.timers ?? {
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    clearTimeout: (id) => clearTimeout(id),
  };
  const noticeMs = deps.noticeMs ?? NOTICE_MS;

  let searchTimer = null;
  let searchSeq = 0;
  let copyBusy = false;
  let noticeTimer = null;

  // ---- 提示条 ----

  function cancelNotice() {
    if (noticeTimer !== null) {
      timers.clearTimeout(noticeTimer);
      noticeTimer = null;
    }
  }

  function hideNotice() {
    cancelNotice();
    ui.notice.hidden = true;
    ui.notice.textContent = "";
  }

  function showNotice(text, isError = false) {
    cancelNotice();
    ui.notice.textContent = text;
    ui.notice.hidden = false;
    ui.notice.className = isError ? "notice notice-error" : "notice";
    noticeTimer = timers.setTimeout(() => {
      noticeTimer = null;
      hideNotice();
    }, noticeMs);
  }

  function attempt(fn) {
    return (async () => {
      try {
        await fn();
      } catch (err) {
        showNotice(err instanceof Error ? `操作失败：${err.message}` : `操作失败：${String(err)}`, true);
      }
    })();
  }

  // ---- 视图渲染 ----

  function showView(view) {
    for (const name of VIEWS) ui.views[name].hidden = name !== view;
  }

  function setError(el, text) {
    el.hidden = text === "";
    el.textContent = text;
  }

  function cancelSearchTimer() {
    if (searchTimer !== null) {
      timers.clearTimeout(searchTimer);
      searchTimer = null;
    }
  }

  function renderFooter(status) {
    const version = status.version ? `v${status.version}` : "";
    const minutes = status.settings ? status.settings.autoLockMinutes : undefined;
    let lockHint = "";
    if (typeof minutes === "number" && !Number.isNaN(minutes)) {
      lockHint = minutes === 0 ? "自动锁定已禁用" : `闲置 ${minutes} 分钟自动锁定`;
    }
    ui.version.textContent = [version, lockHint].filter(Boolean).join(" · ");
  }

  function enterLocked() {
    showView("unlock");
    ui.statusDot.className = "dot dot-locked";
    ui.statusText.textContent = "已锁定";
    ui.lockButton.hidden = true;
    ui.unlockPassword.focus();
  }

  /** 生成器控件回填：status.settings.generator（C7）→ 控件；缺失/损坏回落默认。
   * 在 init 拉状态时执行——面板首开即可直接生成，而不是先撞一次「全部未勾选」。 */
  function prefillGenerator(settings) {
    let options;
    try {
      options = resolveOptions(settings?.generator);
    } catch (_) {
      return; // settings 形状损坏：保持控件现状（全关时生成会给明确报错）
    }
    ui.genLength.value = String(options.length);
    ui.genLower.checked = options.lower;
    ui.genUpper.checked = options.upper;
    ui.genDigits.checked = options.digits;
    ui.genSymbols.checked = options.symbols;
    ui.genAmbiguous.checked = options.ambiguousOff;
  }

  async function renderStatus(status) {
    hideNotice();
    cancelSearchTimer();
    renderFooter(status);
    prefillGenerator(status.settings);
    const view = deriveView(status);
    showView(view);
    if (view === "setup") {
      ui.statusDot.className = "dot dot-uninit";
      ui.statusText.textContent = "未初始化";
      ui.lockButton.hidden = true;
      ui.setupPassword.focus();
      return;
    }
    if (view === "unlock") {
      enterLocked();
      return;
    }
    ui.statusDot.className = "dot dot-ok";
    ui.statusText.textContent = "已解锁";
    ui.lockButton.hidden = false;
    ui.search.value = "";
    ui.search.focus();
    await refreshList("");
  }

  async function fetchStatus() {
    const res = await send({ kind: "status" });
    if (!res) throw new Error(NO_RESPONSE);
    await renderStatus(res);
  }

  // ---- 列表 ----

  function renderRows(rows) {
    const roots = rows.map((row) => {
      const view = createRowView(() => {
        void copyRow(row.id);
      });
      view.name.textContent = row.name;
      view.preview.textContent = row.preview ?? "";
      view.meta.textContent = row.platform ?? "";
      view.root.title = `点击复制「${row.name}」`;
      if (row.status && row.status !== "active") {
        view.tag.textContent = statusLabel(row.status);
        view.tag.className = `tag tag-${row.status}`;
        view.tag.hidden = false;
      }
      return view.root;
    });
    ui.listRows.replaceChildren(...roots);
  }

  function renderListMeta(rows, query) {
    if (rows.length > 0) {
      ui.listMeta.textContent = `${rows.length} 条记录`;
      ui.listMeta.className = "list-meta";
      return;
    }
    ui.listMeta.textContent = query ? "没有匹配的记录" : "库中还没有记录——在网页上复制密钥时会提示捕获";
    ui.listMeta.className = "list-meta list-empty";
  }

  async function refreshList(query) {
    const seq = ++searchSeq;
    const res = await send({ kind: "list", query });
    if (seq !== searchSeq) return; // 过期响应：已有更新一次的列表在途，丢弃
    if (!res) throw new Error(NO_RESPONSE);
    if (res.error) {
      showNotice(res.error, true);
      return;
    }
    // 不设 locked 分支：真实链路 hList（background.js）→ listSecrets
    // （store.js，无 assertUnlocked）不要求解锁，C1#7 也不声明 list 锁定
    // 错误——锁定态切回由 copyRow 的 reveal locked 路径覆盖（那条真实可达）。
    const rows = res.rows ?? [];
    renderRows(rows);
    renderListMeta(rows, query);
  }

  // ---- 三态门动作 ----

  async function submitSetup() {
    const invalid = validateNewPassword(ui.setupPassword.value, ui.setupConfirm.value);
    if (invalid) {
      setError(ui.setupError, invalid);
      return;
    }
    setError(ui.setupError, "");
    const res = await send({ kind: "setup", password: ui.setupPassword.value });
    if (!res) {
      setError(ui.setupError, NO_RESPONSE);
      return;
    }
    if (res.error) {
      setError(ui.setupError, res.error);
      return;
    }
    ui.setupPassword.value = "";
    ui.setupConfirm.value = "";
    await fetchStatus();
  }

  async function submitUnlock() {
    if (!ui.unlockPassword.value) {
      setError(ui.unlockError, PASSWORD_REQUIRED);
      return;
    }
    setError(ui.unlockError, "");
    const res = await send({ kind: "unlock", password: ui.unlockPassword.value });
    if (!res) {
      setError(ui.unlockError, NO_RESPONSE);
      return;
    }
    if (res.error) {
      setError(ui.unlockError, res.error);
      ui.unlockPassword.value = "";
      ui.unlockPassword.focus();
      return;
    }
    ui.unlockPassword.value = "";
    await fetchStatus();
  }

  async function lock() {
    const res = await send({ kind: "lock" });
    if (!res) {
      showNotice(NO_RESPONSE, true);
      return;
    }
    if (res.error) {
      showNotice(res.error, true);
      return;
    }
    await fetchStatus();
  }

  // ---- 复制流 ----

  async function copyRow(id) {
    if (copyBusy) return;
    copyBusy = true;
    try {
      const res = await send({ kind: "reveal", id, event: "copy" });
      if (!res) throw new Error(NO_RESPONSE);
      if (res.locked) {
        showNotice(res.error ?? LOCKED_FALLBACK, true);
        enterLocked();
        return;
      }
      if (res.error) {
        showNotice(res.error, true);
        return;
      }
      await clipboardWrite(res.value);
      const copied = await send({ kind: "copied" });
      // 无响应（SW 已死/通道断）与 error 同义：擦除都没安排上，绝不显示
      // 「约 30 秒后自动覆盖」的成功承诺——那是虚假安全保障（文案红线）。
      if (!copied || copied.error) showNotice(COPY_WIPE_FAILED, true);
      else showNotice(COPY_NOTICE);
    } catch (err) {
      showNotice(err instanceof Error && err.message ? `${COPY_FAILED}：${err.message}` : COPY_FAILED, true);
    } finally {
      copyBusy = false;
    }
  }

  // ---- 搜索（150ms 防抖，Esc 清空立即回全表） ----

  function onSearchInput() {
    cancelSearchTimer();
    const query = ui.search.value;
    searchTimer = timers.setTimeout(() => {
      searchTimer = null;
      void attempt(() => refreshList(query));
    }, DEBOUNCE_MS);
  }

  function clearSearch() {
    if (ui.search.value === "") return;
    cancelSearchTimer();
    ui.search.value = "";
    void attempt(() => refreshList(""));
  }

  async function init() {
    await attempt(fetchStatus);
  }

  // ---- 生成器（生成 → 复制走 30s 擦除通道 / 存入库走 store-direct） ----

  let generated = "";

  function readGenOptions() {
    return resolveOptions({
      length: ui.genLength.value,
      lower: Boolean(ui.genLower.checked),
      upper: Boolean(ui.genUpper.checked),
      digits: Boolean(ui.genDigits.checked),
      symbols: Boolean(ui.genSymbols.checked),
      ambiguousOff: Boolean(ui.genAmbiguous.checked),
    });
  }

  function toggleGen() {
    ui.genPanel.hidden = !ui.genPanel.hidden;
    if (!ui.genPanel.hidden && !generated) return attempt(runGen);
    return Promise.resolve();
  }

  async function runGen() {
    let result;
    try {
      result = generatePassword(readGenOptions());
    } catch (error) {
      showNotice(`生成失败：${error?.message ?? String(error)}`, true);
      return;
    }
    generated = result.password;
    ui.genOutput.textContent = generated;
    ui.genStrength.textContent = `约 ${result.entropyBits} 比特（粗估）`;
    // 记住上次选择：settings.generator 走 C7（settings 在 backend meta，锁定态也可写）
    await send({ kind: "set-settings", patch: { generator: readGenOptions() } });
  }

  async function copyGen() {
    if (!generated) return;
    try {
      await clipboardWrite(generated);
    } catch (err) {
      showNotice(`${COPY_FAILED}：${err instanceof Error && err.message ? err.message : ""}`, true);
      return;
    }
    const copied = await send({ kind: "copied" });
    // 与 copyRow 同一文案红线：无响应/错误 = 擦除没安排上，不给虚假承诺
    if (!copied || copied.error) showNotice(COPY_WIPE_FAILED, true);
    else showNotice(COPY_NOTICE);
  }

  function toggleGenSave() {
    ui.genSaveForm.hidden = !ui.genSaveForm.hidden;
  }

  async function confirmGenSave() {
    const name = ui.genName.value.trim();
    const platform = ui.genPlatform.value.trim();
    if (!platform) {
      showNotice("请填平台（如 github.com）——生成的密码是随机串，检测器不认识", true);
      return;
    }
    const response = await send({ kind: "store-direct", value: generated, name, platform });
    if (!response || response.error) {
      showNotice(response?.error ?? NO_RESPONSE, true);
      return;
    }
    ui.genName.value = "";
    ui.genPlatform.value = "";
    ui.genSaveForm.hidden = true;
    showNotice(`已存入：${response.name}`);
    await attempt(() => refreshList(""));
  }

  // 三个主提交动作统一经 attempt：send reject（扩展上下文失效/通道断）时
  // 以「操作失败：…」提示条反馈，绝不让异常穿透后 UI 毫无反应。
  return {
    init,
    submitSetup: () => attempt(submitSetup),
    submitUnlock: () => attempt(submitUnlock),
    lock: () => attempt(lock),
    onSearchInput,
    clearSearch,
    toggleGen,
    runGen,
    copyGen,
    toggleGenSave,
    confirmGenSave,
  };
}

// ---- 浏览器装配：boot() 只做 DOM 组装与事件接线 ----

function buildUi(doc) {
  const el = (id) => doc.getElementById(id);
  return {
    views: { setup: el("setup-view"), unlock: el("unlock-view"), list: el("list-view") },
    statusDot: el("status-dot"),
    statusText: el("status-text"),
    lockButton: el("lock-button"),
    setupForm: el("setup-form"),
    setupPassword: el("setup-password"),
    setupConfirm: el("setup-confirm"),
    setupError: el("setup-error"),
    unlockForm: el("unlock-form"),
    unlockPassword: el("unlock-password"),
    unlockError: el("unlock-error"),
    search: el("search"),
    listRows: el("list-rows"),
    listMeta: el("list-meta"),
    notice: el("notice"),
    version: el("version"),
    genOpen: el("gen-open"),
    genPanel: el("gen-panel"),
    genLength: el("gen-length"),
    genLower: el("gen-lower"),
    genUpper: el("gen-upper"),
    genDigits: el("gen-digits"),
    genSymbols: el("gen-symbols"),
    genAmbiguous: el("gen-ambiguous"),
    genOutput: el("gen-output"),
    genStrength: el("gen-strength"),
    genGenerate: el("gen-generate"),
    genCopy: el("gen-copy"),
    genSave: el("gen-save"),
    genSaveForm: el("gen-save-form"),
    genName: el("gen-name"),
    genPlatform: el("gen-platform"),
    genSaveConfirm: el("gen-save-confirm"),
  };
}

function makeRowView(doc, onCopy) {
  const root = doc.createElement("button");
  root.type = "button";
  root.className = "row";
  const name = doc.createElement("span");
  name.className = "row-name";
  const preview = doc.createElement("span");
  preview.className = "row-preview";
  const side = doc.createElement("span");
  side.className = "row-side";
  const meta = doc.createElement("span");
  meta.className = "row-meta";
  const tag = doc.createElement("span");
  tag.className = "tag";
  tag.hidden = true;
  side.append(meta, tag);
  root.append(name, preview, side);
  root.addEventListener("click", () => onCopy());
  return { root, name, preview, meta, tag };
}

export function boot(overrides = {}) {
  const doc = overrides.document ?? document;
  const ui = buildUi(doc);
  const controller = createController({
    send: (msg) => chrome.runtime.sendMessage(msg),
    createRowView: (onCopy) => makeRowView(doc, onCopy),
    clipboardWrite: (text) => navigator.clipboard.writeText(text),
    ui,
    ...overrides,
  });
  ui.setupForm.addEventListener("submit", (event) => {
    event.preventDefault();
    void controller.submitSetup();
  });
  ui.unlockForm.addEventListener("submit", (event) => {
    event.preventDefault();
    void controller.submitUnlock();
  });
  ui.lockButton.addEventListener("click", () => {
    void controller.lock();
  });
  ui.search.addEventListener("input", () => {
    controller.onSearchInput();
  });
  ui.search.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      controller.clearSearch();
    }
  });
  ui.genOpen.addEventListener("click", () => {
    void controller.toggleGen();
  });
  ui.genGenerate.addEventListener("click", () => {
    void controller.runGen();
  });
  ui.genCopy.addEventListener("click", () => {
    void controller.copyGen();
  });
  ui.genSave.addEventListener("click", () => {
    controller.toggleGenSave();
  });
  ui.genSaveConfirm.addEventListener("click", () => {
    void controller.confirmGenSave();
  });
  void controller.init();
}

if (typeof document !== "undefined") {
  boot();
}
