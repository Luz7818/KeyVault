// KeyVault 管理页（options）：八区全功能管理页——①仪表盘 ②密钥表格
// （含密码生成器卡）③详情抽屉（行点击唤起）④审计日志 ⑤批量导入（两步
// 向导：解析预览 → 勾选确认，逐条 store-direct）⑥备份恢复 ⑦设置 ⑧泄露扫描。
// 经 manifest 的 options_ui.open_in_tab 以整页打开。
//
// 与旧版（git 批4 options.js）的硬缺口修正：
// - 整库清除不再动态 import ./src/vault/db.js 自删（旧版 497-504 零鉴权），
//   改走 C1#30 reset 鉴权消息；UI 双门 = 主密码（鉴权门）+ 输入 RESET
//   （二次确认门），缺一不可。真正的认证在 background 的 verifier 校验。
// - TOTP 从新家 ./src/vault/totp.js 导入（旧 src/core/totp.js 归整合批删除）。
//
// 与桌面版的对位：详情字段与指纹历史（16 位前缀）对齐 kv/gui/detail_window.py:16-37
// （「操作历史」节有意不移植——C1#8 get 契约无 history 字段，完整轨迹在④审计区，
// 同 detail_window.py:36 的指向）；TOTP 活码是**显式入口**（「显示验证码」按钮点击
// 才 reveal，对齐 open_totp_for_secret 的入口纪律「非种子记录不该为看看详情付一次
// reveal」totp_window.py:83-84；活码每秒重绘、剩 5s 转琥珀、复制走剪贴板擦除通道）；
// 扫描的跳过口径（头 8KB 二进制、50MB 上限）对齐 kv/ops/scan.py:19-29；kv config
// 的 mask_char 显示偏好不移植（C9#8，掩码字符固定 *）。kv scan <目录> 的浏览器
// 等价 = 三种输入（粘贴 / 文件 / 目录），目录遍历与 .git 剪枝由用户选择范围天然
// 替代（C6）；分批协议在应用层（planScanBatches + chunkTextByLines），引擎无跨批状态。
//
// 全 textContent：vault 里的名称/备注/来源是用户数据，绝不 innerHTML。
// 模块级只放纯逻辑与常量（含编排层：三态门 planGateState、导入/恢复/扫描结果
// 文案、通道异常翻译——供 node 测试），DOM/chrome 全部收在函数里，boot() 由
// document 探测守卫——node --test 直 import 本文件即可测（tests/app-options.test.js）。

import { PERIOD, parseSeed, generate } from "./src/vault/totp.js";
import {
  GENERATOR_DEFAULTS, generate as generatePassword, resolveOptions,
} from "./src/vault/generator.js";
import { parseBulk, previewText } from "./src/vault/importers.js";
import { MAX_TEXTS_PER_CALL, MAX_CONTENT_CHARS, MAX_TOTAL_CHARS } from "./src/vault/scan.js";
import { suggestCorrectionScope } from "./src/core/corrections.js";
import { hostsIn } from "./src/core/candidate.js";

// ---------------------------------------------------------------- 常量（纯）

const RESET_CONFIRM_PHRASE = "RESET";
// kv/ops/scan.py:20 MAX_FILE_SIZE：50MB 以上跳过（日志/打包产物/数据库量级）
const MAX_FILE_SIZE = 50 * 1024 * 1024;
// kv/ops/scan.py:22 BINARY_CHECK_SIZE：头 8KB 探测 \x00
const BINARY_CHECK_SIZE = 8192;
// 扫描错误清单最多逐条列出 N 项，其余计数带过
const SCAN_ERROR_LIST_CAP = 50;
// 导入文件上限（与 importers.js 的 IMPORT_MAX_CHARS 同量级的 UI 侧闸）
const IMPORT_FILE_MAX = 2_000_000;

export const STATUS_LABEL = { active: "启用", revoked: "已吊销", expired: "已过期" };
export const CONFIDENCE_LABEL = {
  manual: "手动", exact: "精确", high: "高", medium: "中", ambiguous: "厂商未定", none: "无",
};
const CORRECTION_KIND_LABEL = {
  exact: "精确值", keyname: "键名", hostname: "主机名", window: "窗口标题", group: "组默认",
};
const TAB_TITLES = {
  dashboard: "仪表盘", secrets: "密钥", audit: "审计日志", import: "批量导入",
  backup: "备份恢复", scan: "泄露扫描", settings: "设置",
};

// ---------------------------------------------------------------- 纯逻辑（可测）

/** ISO 时间戳 → "YYYY-MM-DD HH:MM:SS"（对齐桌面 detail 的 [:19] 截断）。 */
export function formatStamp(iso) {
  return String(iso ?? "").slice(0, 19).replace("T", " ");
}

/** 标签输入 → 数组：中英文逗号/顿号/分号分隔，去空白。 */
export function parseTagList(text) {
  return String(text ?? "")
    .split(/[,，、;；]+/)
    .map((tag) => tag.trim())
    .filter(Boolean);
}

/** 忽略站点输入 → 主机名数组：按行、小写化、去重（C7 settings 语义）。 */
export function parseHostList(text) {
  const seen = new Set();
  for (const raw of String(text ?? "").split(/\n+/)) {
    const host = raw.trim().toLowerCase();
    if (host) seen.add(host);
  }
  return [...seen];
}

/** 自动锁定分钟数：非负整数，0=禁用，封顶 240（旧版口径）。 */
export function clampAutoLock(value) {
  const parsed = Math.floor(Number(value));
  if (!Number.isFinite(parsed) || parsed < 0) return 0;
  return Math.min(parsed, 240);
}

/** 二进制探测：给定头几个字节，含 \x00 即二进制（scan.py:92-98 同构）。 */
export function isBinaryHead(bytes) {
  return bytes instanceof Uint8Array && bytes.includes(0);
}

/** 整库清除的 UI 双门：主密码（鉴权门）与 RESET（二次确认门）缺一不可。
 * 只做交互门校验；真正的口令认证在 background 的 C1#30 reset。 */
export function checkResetGates(password, phrase) {
  if (!password) return "鉴权门未过：先输入主密码";
  if (phrase !== RESET_CONFIRM_PHRASE) {
    return `二次确认门未过：请在确认框原样输入 ${RESET_CONFIRM_PHRASE}`;
  }
  return null;
}

/** 「设为平台」的可记住纠正范围，对齐 suggest_correction_scope（键名 > 主机名）。
 * 组默认不会被静默建议（corrections.js 的纪律）。返回 {kind,pattern,desc} 或 null。 */
export function suggestScopeFor(record) {
  const suggestion = suggestCorrectionScope(
    { keyName: record?.keyName ?? null, hostnames: hostsIn(record?.sourceUrl ?? "") },
    null,
  );
  if (!suggestion) return null;
  const [kind, pattern, desc] = suggestion;
  return { kind, pattern, desc };
}

/**
 * 把一段文本按**行界**切块，每块 ≤ maxChars（超长单行独立成块不截断，交给
 * 显式报错路径）。行号按块累计：块内行号 + lineOffset = 原文行号。
 * split 与 scan.js 的 /\r\n|\r|\n/ 同式，行数两侧一致。
 */
export function chunkTextByLines(content, maxChars) {
  const lines = String(content ?? "").split(/\r\n|\r|\n/);
  const chunks = [];
  let buffer = [];
  let budget = 0;
  let offset = 0;
  const flush = () => {
    if (!buffer.length) return;
    chunks.push({ content: buffer.join("\n"), lineOffset: offset });
    offset += buffer.length;
    buffer = [];
    budget = 0;
  };
  for (const line of lines) {
    if (buffer.length && budget + line.length > maxChars) flush();
    buffer.push(line);
    budget += line.length + 1; // 预算按 join("\n") 分隔符计，宁多勿少
  }
  flush();
  return chunks;
}

/**
 * C6 分批规划：贪心装批，批 ≤ maxItems 条、总字符 ≤ maxTotalChars。
 * 单条超过 maxContentChars 的块**独占一批**——引擎按批整体校验，隔离它的
 * 失败不拖累其他文本，超限项最终走显式报错而非静默丢。
 */
export function planScanBatches(items, {
  maxItems = MAX_TEXTS_PER_CALL,
  maxTotalChars = MAX_TOTAL_CHARS,
  maxContentChars = MAX_CONTENT_CHARS,
} = {}) {
  const batches = [];
  let current = [];
  let total = 0;
  let currentHasOversized = false;
  const flush = () => {
    if (!current.length) return;
    batches.push(current);
    current = [];
    total = 0;
    currentHasOversized = false;
  };
  for (const item of items) {
    const size = item.chunk.length;
    const oversized = size > maxContentChars;
    if (current.length && (oversized || currentHasOversized || current.length >= maxItems || total + size > maxTotalChars)) {
      flush();
    }
    current.push(item);
    total += size;
    if (oversized) currentHasOversized = true;
  }
  flush();
  return batches;
}

/** 块内行号 → 文件真实行号：按内部块名映射回显示路径并加行偏移。
 * 未知块名原样保留——一次映射失败不吞掉任何命中。 */
export function decorateHits(hits, items) {
  const byName = new Map(items.map((item) => [item.name, item]));
  return hits.map((hit) => {
    const item = byName.get(hit.textName);
    if (!item) return hit;
    return { ...hit, path: item.path, lineNo: hit.lineNo + (item.lineOffset ?? 0) };
  });
}

/** 消息通道异常翻译（MV3：扩展更新/重载后旧页面 context invalidated、SW
 * 启动失败时 sendMessage 会 reject）——send 捕获后翻译成可读错误，调用方的
 * response.error 路径照常工作，页面不再静默无反应。 */
export function translateChannelError(error) {
  const reason = error?.message ?? String(error);
  return `消息通道失败：${reason}（扩展可能已重载——请关闭本页重新打开）`;
}

/** 三态门编排（纯函数，renderGate 只做渲染）：status → 门的状态与文案。
 * error = background 无响应/通道失败（给重试），setup/unlock/open 同名。 */
export function planGateState(status) {
  if (!status || status.error) {
    return {
      mode: "error",
      title: "无法访问保管箱",
      desc: status?.error ?? "background 没有响应，请重试",
      button: "重试",
    };
  }
  if (!status.initialized) {
    return {
      mode: "setup",
      title: "设置主密码，创建本机保管箱",
      desc: "加密数据只存本机浏览器（IndexedDB）；主密码不设恢复——忘了就是忘了。至少 8 位。",
      button: "创建",
    };
  }
  if (!status.unlocked) {
    return { mode: "unlock", title: "输入主密码解锁", desc: "", button: "解锁" };
  }
  return { mode: "open" };
}

/** 批量导入结果文案：新建/去重/跳过(+拒绝) 与 skipped 首条（不含值，C1#20）。 */
export function formatImportResult(summary) {
  if (!summary || summary.error) return null;
  const skipped = summary.skipped ?? [];
  const parts = [
    `新建 ${summary.created ?? 0}`,
    `去重 ${summary.deduped ?? 0}`,
    `跳过 ${skipped.length}`,
  ];
  if (summary.rejected) parts.push(`拒绝 ${summary.rejected}`);
  if (skipped.length) {
    parts.push(`首条：${skipped[0].platform} · ${skipped[0].confidence} · ${skipped[0].preview}`);
  }
  return parts.join(" · ");
}

/** 备份恢复结果文案。 */
export function formatRestoreResult(summary) {
  if (!summary || summary.error) return null;
  const parts = [`新建 ${summary.created ?? 0}`, `去重 ${summary.deduped ?? 0}`];
  if (summary.rejected) parts.push(`拒绝 ${summary.rejected}`);
  return `恢复完成：${parts.join(" · ")}`;
}

/** 扫描结果聚合（纯函数，renderScanResults 只做渲染）：状态行文案、好坏配色、
 * 完整错误清单（读取失败在前，被拒批次在后）。 */
export function summarizeScanRun(collected, { batchCount, hits, failed }) {
  const parts = [
    `扫描完成：${hits.length} 处命中`,
    `${batchCount} 批 / ${collected.items.length} 条文本`,
  ];
  if (collected.skippedBinary) parts.push(`跳过二进制 ${collected.skippedBinary}`);
  if (collected.skippedLarge) parts.push(`跳过超大文件 ${collected.skippedLarge}`);
  return {
    statusText: parts.join(" · "),
    statusCls: hits.length ? "bad" : "ok",
    errors: [...collected.readErrors, ...failed],
  };
}

// ---------------------------------------------------------------- 消息与门

const $ = (id) => document.getElementById(id);

function el(tag, className = null, text = null) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== null) node.textContent = text;
  return node;
}

let toastTimer = null;
function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.add("hidden"), 2600);
}

let hintTimer = null;
/** 抽屉的操作反馈（弹层之下 toast 会被盖住，抽屉内反馈走这里）。 */
function drawerHint(message, cls = "ok") {
  const node = $("drawer-hint");
  node.textContent = message;
  node.className = `hint ${cls}`;
  clearTimeout(hintTimer);
  hintTimer = setTimeout(() => {
    node.textContent = "";
    node.className = "hint";
  }, 4000);
}

function renderStatusPill(status) {
  const dot = $("status-dot");
  dot.className = "dot";
  if (status?.unlocked) {
    dot.classList.add("ok");
    $("status-text").textContent = "已解锁";
  } else if (status?.initialized) {
    dot.classList.add("bad");
    $("status-text").textContent = "已锁定";
  } else {
    dot.classList.add("bad");
    $("status-text").textContent = "未初始化";
  }
  $("lock-btn").classList.toggle("hidden", !status?.unlocked);
}

function showGate({ title, desc, button, action }) {
  $("gate").classList.remove("hidden");
  $("gate-title").textContent = title;
  $("gate-desc").textContent = desc;
  $("gate-error").textContent = "";
  const submitButton = $("gate-submit");
  submitButton.textContent = button;
  submitButton.onclick = async () => {
    submitButton.disabled = true;
    $("gate-error").textContent = "";
    try {
      const response = await action($("gate-password").value);
      if (response?.error) {
        $("gate-error").textContent = response.error;
        return;
      }
      $("gate-password").value = "";
      await renderGate();
    } finally {
      submitButton.disabled = false;
    }
  };
  setTimeout(() => $("gate-password").focus(), 50);
}

// 三态门各模式的提交动作（error 模式的「重试」只是重新拉 status）
const GATE_ACTIONS = {
  error: async () => ({}),
  setup: (password) => send("setup", { password }),
  unlock: (password) => send("unlock", { password }),
};

async function renderGate() {
  const status = await send("status");
  renderStatusPill(status);
  const plan = planGateState(status);
  if (plan.mode === "open") {
    $("side-version").textContent = status.version ? `v${status.version}` : "";
    $("gate").classList.add("hidden");
    await renderTab(currentTab);
    return;
  }
  showGate({
    title: plan.title,
    desc: plan.desc,
    button: plan.button,
    action: GATE_ACTIONS[plan.mode],
  });
}

/** 会话中途被自动锁定：门还开着时不打扰（解锁/创建流程中失败自带 locked
 * 标记，由门内错误行展示），门关着时把整页翻回解锁门。.gate 是普通 div 盖
 * 不到 top-layer 的 modal dialog——锁定时必须主动关抽屉/表单、停活码、清
 * 抽屉体（种子明文闭包随节点一并释放），不允许「已锁定」与可交互弹层并存。 */
function handleLockedResponse(response) {
  if (response?.locked && $("gate").classList.contains("hidden")) {
    stopTotp();
    const drawer = $("drawer");
    if (drawer.open) drawer.close();
    $("drawer-body").textContent = "";
    const formDialog = $("form-dialog");
    if (formDialog.open) formDialog.close();
    renderStatusPill({ unlocked: false, initialized: true });
    showGate({
      title: "保管箱已锁定",
      desc: "无操作超时自动锁定——输入主密码解锁",
      button: "解锁",
      action: (password) => send("unlock", { password }),
    });
  }
}

/** 发消息。永不 reject：通道异常（扩展重载/更新后 context invalidated、SW
 * 启动失败）翻译成 {error}，调用方现成的 response.error 路径即接管——点
 * 刷新/导入/保存/扫描不再静默无反应。响应带 locked:true 且门还关着时统一
 * 翻回解锁门——自动锁定可能在任一条消息途中生效，不能指望每条调用方都记得检查。 */
async function send(kind, extra = {}) {
  let response;
  try {
    response = await chrome.runtime.sendMessage({ kind, ...extra });
  } catch (error) {
    return { error: translateChannelError(error) };
  }
  handleLockedResponse(response);
  return response ?? { error: "background 无响应" };
}

// ---------------------------------------------------------------- 导航

let currentTab = "dashboard";

function selectTab(tab) {
  currentTab = tab;
  for (const button of document.querySelectorAll("#nav button")) {
    button.classList.toggle("active", button.dataset.tab === tab);
  }
  return renderTab(tab);
}

async function renderTab(tab) {
  for (const section of document.querySelectorAll(".tab")) section.classList.add("hidden");
  const section = $(`tab-${tab}`);
  if (section) section.classList.remove("hidden");
  $("page-title").textContent = TAB_TITLES[tab] ?? "KeyVault";
  const renderer = RENDERERS[tab];
  if (renderer) await renderer();
}

async function refresh() {
  await renderTab(currentTab);
}

const RENDERERS = {
  dashboard: renderDashboard,
  secrets: renderSecrets,
  audit: renderAudit,
  import: () => {},      // 表单即界面，状态常驻
  backup: () => {},
  scan: () => {},
  settings: renderSettings,
};

// ---------------------------------------------------------------- ① 仪表盘

async function renderDashboard() {
  const stats = await send("stats");
  if (!stats || stats.error) return;
  $("stat-total").textContent = String(stats.total ?? 0);
  $("stat-active").textContent = String(stats.active ?? 0);
  $("stat-expiring").textContent = String(stats.expiring ?? 0);
  $("stat-flagged").textContent = String(stats.flagged ?? 0);
  renderPlatformList(stats.platforms ?? []);
}

function renderPlatformList(platforms) {
  const list = $("platform-list");
  list.textContent = "";
  if (!platforms.length) {
    list.append(el("li", "muted small", "还没有启用中的记录"));
    return;
  }
  const max = Math.max(...platforms.map((entry) => entry.count), 1);
  for (const { platform, count } of platforms) {
    const row = el("li", "platform-row");
    const bar = el("span", "p-bar");
    bar.style.width = `${Math.max(Math.round((count / max) * 100), 4)}%`;
    row.append(el("span", "p-name", platform), bar, el("span", "p-count", String(count)));
    list.append(row);
  }
}

// ---------------------------------------------------------------- ② 密钥表格

async function renderSecrets() {
  const response = await send("list", {
    query: $("search").value.trim(),
    status: $("status-filter").value,
  });
  const rows = response?.rows;
  if (!rows) return; // 锁定等错误已由门接管
  const tbody = $("rows");
  tbody.textContent = "";
  const empty = $("table-empty");
  if (!rows.length) {
    empty.textContent = $("search").value.trim()
      ? "没有匹配的记录"
      : "库里还没有密钥——去网页上复制一个，或用右上角「添加密钥」手动录入";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  for (const row of rows) {
    tbody.append(secretRow(row));
  }
}

function secretRow(row) {
  const tr = el("tr");
  tr.append(
    el("td", "name", row.name),
    el("td", "muted", row.platform),
    el("td", null, null),
    el("td", "preview", row.preview),
    el("td", "muted", formatStamp(row.updatedAt)),
  );
  const statusCell = tr.children[2];
  statusCell.append(el("span", `st st-${row.status}`, STATUS_LABEL[row.status] ?? row.status));
  tr.addEventListener("click", () => openDetail(row.id));
  return tr;
}

// ---------------------------------------------------------------- ③ 详情抽屉

let openDetailId = null;
// openDetail 竞态守卫：SW 冷启动首条消息可达数百 ms，连点两行会有两个并发
// get——响应回来时序号已过期者整包丢弃（同 popup.js searchSeq 的纪律）
let openDetailSeq = 0;
let totpTimer = null;
const TOTP_RING_SIZE = 46;

function stopTotp() {
  if (totpTimer) {
    clearTimeout(totpTimer);
    totpTimer = null;
  }
}

async function openDetail(id) {
  const seq = ++openDetailSeq;
  const response = await send("get", { id });
  if (seq !== openDetailSeq) return; // 过期响应：另一行已被打开
  if (!response || response.error) {
    toast(`读取详情失败：${response?.error ?? "background 无响应"}`);
    return;
  }
  const detail = response.record;
  openDetailId = detail.id;
  $("drawer-title").textContent = detail.name;
  renderDrawerBadges(detail);
  const body = $("drawer-body");
  body.textContent = ""; // 重建即弃旧节：上一条记录的活码闭包一并释放
  stopTotp();
  appendFieldGrid(body, detail);
  appendFingerprints(body, detail);
  appendActions(body, detail);
  const dialog = $("drawer");
  if (!dialog.open) dialog.showModal();
}

function renderDrawerBadges(detail) {
  const badges = $("drawer-badges");
  badges.textContent = "";
  badges.append(
    el("span", "badge", detail.platform),
    el("span", `badge st st-${detail.status}`, STATUS_LABEL[detail.status] ?? detail.status),
    el("span", "badge", `置信度：${CONFIDENCE_LABEL[detail.confidence] ?? detail.confidence}`),
    el("span", "badge", `种类：${detail.kind}`),
  );
}

/** TOTP 活码的显式入口，对齐桌面 open_totp_for_secret（totp_window.py:78-101）
 * 的入口纪律：非种子记录不该为「看看详情」付一次 reveal——打开详情零 reveal，
 * 点击「显示验证码」才 reveal(event:"totp")；SeedError 给提示、不开节。
 * 活码节每秒重算重绘（totp_window.py:51-58 同款）。 */
async function showTotpSection(detail) {
  const body = $("drawer-body");
  if (body.querySelector(".totp-section")) return;
  let seed;
  try {
    const revealed = await send("reveal", { id: detail.id, event: "totp" });
    if (!revealed || revealed.error) {
      drawerHint(`读取失败：${revealed?.error ?? "background 无响应"}`, "err");
      return;
    }
    seed = parseSeed(revealed.value);
  } catch (_) {
    // totp_window.py:93-95 的提示文案
    drawerHint(`「${detail.name}」的值不是 base32 种子。种子是验证器「显示密钥」里那串。`, "err");
    return;
  }
  // 等待 reveal 期间抽屉可能被重建（另一行）或被自动锁定关闭——不再落节
  if (!$("drawer").open || openDetailId !== detail.id) return;
  body.prepend(buildTotpBlock(seed));
}

function buildTotpBlock(seed) {
  const block = document.createDocumentFragment();
  block.append(el("h3", "section-title", "TOTP 活码"));
  const section = el("div", "totp-section");
  const canvas = document.createElement("canvas");
  canvas.className = "totp-canvas";
  canvas.width = TOTP_RING_SIZE;
  canvas.height = TOTP_RING_SIZE;
  const codeEl = el("div", "totp-code", "· · ·");
  const remainEl = el("div", "totp-remain", "");
  const main = el("div", "totp-main");
  main.append(codeEl, remainEl);
  let currentCode = "";
  section.append(
    canvas,
    main,
    actionButton("复制验证码", "ghost", async () => {
      if (!currentCode) return;
      try {
        await navigator.clipboard.writeText(currentCode);
      } catch (error) {
        drawerHint(`写剪贴板失败：${error.message}`, "err");
        return;
      }
      const copied = await send("copied");
      drawerHint(copied?.ok ? "已复制验证码" : "已复制验证码（本次未安排自动擦除）");
    }),
  );
  block.append(section);
  // 浏览器无 exclude_history 能力（C9#7）——TOTP 复制路径同样带限制声明
  block.append(el("p", "muted small",
    "验证码 30 秒轮换；复制走剪贴板自动擦除通道（浏览器剪贴板历史 Win+V 无法排除——擦除只清当前剪贴板）"));
  const tick = async () => {
    if (!$("drawer").open) return;
    const { code, remaining } = await generate(seed, new Date());
    currentCode = code;
    codeEl.textContent = `${code.slice(0, 3)} ${code.slice(3)}`;
    remainEl.textContent = `${remaining}s 后刷新`;
    drawTotpRing(canvas, remaining);
    totpTimer = setTimeout(tick, 1000);
  };
  stopTotp();
  tick();
  return block;
}

/** 倒计时环：桌面 _draw_ring 同款——底环描边 + 按剩余比例的弧，起点 12 点
 * 方向，剩 5s 内转琥珀色（「快过期了」是状态指示不是装饰）。 */
function drawTotpRing(canvas, remaining) {
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const scaled = Math.round(TOTP_RING_SIZE * dpr);
  if (canvas.width !== scaled) {
    canvas.width = scaled;
    canvas.height = scaled;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, TOTP_RING_SIZE, TOTP_RING_SIZE);
  const center = TOTP_RING_SIZE / 2;
  const radius = center - 4;
  const start = -Math.PI / 2;
  const full = Math.PI * 2;
  ctx.lineWidth = 4;
  ctx.strokeStyle = cssToken("--ring-track", "#e5e8f0");
  ctx.beginPath();
  ctx.arc(center, center, radius, start, start + full);
  ctx.stroke();
  ctx.strokeStyle = remaining <= 5 ? cssToken("--warn", "#d97706") : cssToken("--accent", "#3d5fe8");
  ctx.beginPath();
  ctx.arc(center, center, radius, start, start + full * (remaining / PERIOD));
  ctx.stroke();
  ctx.fillStyle = cssToken("--text-2", "#5f6b81");
  ctx.font = "600 12px 'Cascadia Mono', Consolas, monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(String(remaining), center, center + 0.5);
}

function cssToken(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/** 详情字段网格，16 项对齐桌面 detail_rows（detail_window.py:16-27）。
 * 有意分歧（C9 纪律，review 可指出）：桌面详情窗的「操作历史」节不移植——
 * C1#8 的 get 契约无 history 字段，完整轨迹统一由④审计日志区承担；这与桌面
 * 自己的指向一致（detail_window.py:36 尾注「完整轨迹见 安全 → 审计日志」）。 */
function detailFields(detail) {
  return [
    ["平台", detail.platform],
    ["状态", STATUS_LABEL[detail.status] ?? detail.status],
    ["置信度", CONFIDENCE_LABEL[detail.confidence] ?? detail.confidence],
    ["种类", detail.kind],
    ["键名", detail.keyName ?? "—"],
    ["预览", detail.preview, true],
    ["标签", (detail.tags ?? []).join(", ") || "—"],
    ["别名", (detail.aliases ?? []).join(", ") || "—"],
    ["备注", detail.note || "—"],
    ["来源", `${detail.origin}${detail.sourceUrl ? " · " + detail.sourceUrl : ""}`],
    ["创建", formatStamp(detail.createdAt)],
    ["更新", formatStamp(detail.updatedAt)],
    ["过期", detail.expiresAt ?? "—"],
    ["最后使用", formatStamp(detail.lastUsedAt) || "—"],
    ["值长度", String(detail.valueLen ?? "")],
    ["sha256", detail.sha256, true],
  ];
}

function appendFieldGrid(body, detail) {
  body.append(el("h3", "section-title", "基本信息"));
  const grid = el("div", "field-grid");
  for (const [label, value, copyable] of detailFields(detail)) {
    grid.append(el("div", "k", label));
    const cell = el("div", `v${copyable ? " clickable" : ""}`, String(value));
    if (copyable) {
      cell.title = "点击复制（非敏感值）";
      cell.addEventListener("click", () => copyPlain(String(value)));
    }
    grid.append(cell);
  }
  body.append(grid);
  if (detail.evidence) {
    body.append(el("h3", "section-title", "判定依据"));
    body.append(el("p", "evidence", detail.evidence));
  }
}

/** 指纹历史：16 位前缀 + 状态 + 首见时间（对齐 detail_window.py:31 的行格式）。 */
function appendFingerprints(body, detail) {
  const fingerprints = detail.fingerprints ?? [];
  if (!fingerprints.length) return;
  body.append(el("h3", "section-title", "指纹历史"));
  const list = el("ul", "fp-list");
  for (const fp of fingerprints) {
    list.append(el("li", `fp-row${fp.status === "active" ? "" : " muted"}`,
      `${fp.sha256Prefix}  ${fp.status}  ${formatStamp(fp.firstSeenAt)}`));
  }
  body.append(list);
}

function appendActions(body, detail) {
  body.append(el("h3", "section-title", "操作"));
  const actions = el("div", "dialog-actions");
  actions.append(actionButton("复制值", "primary", () => copySecretValue(detail)));
  actions.append(actionButton("显示验证码", "ghost", () => showTotpSection(detail)));
  if (detail.status === "active") {
    actions.append(actionButton("轮换", "ghost", () => openRotateForm(detail)));
    actions.append(actionButton("吊销", "ghost", () => {
      if (!confirm(`吊销 ${detail.name}？吊销的值不再启用，但仍会参与泄露扫描——它可能已经泄出去了。`)) return;
      return drawerAction(detail, send("revoke", { id: detail.id }), `已吊销 ${detail.name}`);
    }));
  }
  actions.append(actionButton("重命名", "ghost", () => openRenameForm(detail)));
  actions.append(actionButton("设为平台", "ghost", () => openSetPlatformForm(detail)));
  actions.append(actionButton("删除", "danger", () => {
    if (!confirm(`删除 ${detail.name}？审计记录保留（名字快照），但值本身没了。`)) return;
    return drawerAction(detail, send("delete", { id: detail.id }), `已删除 ${detail.name}`, { closeOnSuccess: true });
  }));
  body.append(actions);
  body.append(el("p", "muted small",
    "浏览器剪贴板历史（Win+V）无法排除——30 秒擦除只清当前剪贴板。"));
}

function actionButton(label, kind, run) {
  const button = el("button", `btn ${kind}`, label);
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await run();
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

/** 复制值：reveal(event:"copy") → 写剪贴板 → copied 安排 30s 擦除（C7）。
 * 文案与 popup 复制行同款。 */
async function copySecretValue(detail) {
  const revealed = await send("reveal", { id: detail.id, event: "copy" });
  if (!revealed || revealed.error) {
    drawerHint(`复制失败：${revealed?.error ?? "background 无响应"}`, "err");
    return;
  }
  try {
    await navigator.clipboard.writeText(revealed.value);
  } catch (error) {
    drawerHint(`写剪贴板失败：${error.message}`, "err");
    return;
  }
  const copied = await send("copied");
  drawerHint(copied?.ok
    ? `已复制 ${detail.name}，${copied.ttl} 秒后自动擦除`
    : `已复制 ${detail.name}（本次未安排自动擦除）`);
}

function copyPlain(text) {
  navigator.clipboard.writeText(text).then(
    () => drawerHint("已复制（非敏感值）"),
    (error) => drawerHint(`写剪贴板失败：${error.message}`, "err"),
  );
}

/** 抽屉内动作：成功刷新抽屉（预览/指纹历史/状态即时更新）；delete 可选关门。 */
async function drawerAction(detail, promise, doneMessage, { closeOnSuccess = false } = {}) {
  const response = await promise;
  if (!response || response.error) {
    drawerHint(response?.error ?? "background 无响应", "err");
    return false;
  }
  drawerHint(doneMessage);
  if (closeOnSuccess) {
    $("drawer").close();
  } else {
    await openDetail(detail.id);
  }
  return true;
}

function openRotateForm(detail) {
  showForm({
    title: `轮换 — ${detail.name}`,
    controls: [{ label: "新值", key: "value", password: true }],
    done: `已轮换 ${detail.name}——旧值进指纹历史（revoked），预览已更新`,
    submit: (data) => send("rotate", { id: detail.id, value: data.value }),
  });
}

function openRenameForm(detail) {
  let newName = "";
  showForm({
    title: `重命名 — ${detail.name}`,
    controls: [{ label: "新名称", key: "name", initial: detail.name }],
    done: () => `已重命名为 ${newName}——旧名进别名，可按别名搜索`,
    submit: (data) => {
      newName = data.name;
      return send("rename", { id: detail.id, name: data.name });
    },
  });
}

function openSetPlatformForm(detail) {
  const scope = suggestScopeFor(detail);
  let platformId = detail.platform;
  let rememberedSuffix = "";
  showForm({
    title: `设为平台 — ${detail.name}`,
    controls: [{ label: "平台 ID", key: "platform", initial: detail.platform }],
    checkbox: scope
      ? { label: scope.desc, checked: true }
      : null, // 无键名也无主机名：不给组默认建议（不被静默选中）
    done: () => `已设为 ${platformId}${rememberedSuffix}`,
    submit: async (data, remember) => {
      platformId = data.platform;
      rememberedSuffix = scope && remember ? "，并记住纠正规则" : "";
      return send("set-platform", {
        id: detail.id,
        platform: data.platform,
        remember: scope && remember ? { kind: scope.kind, pattern: scope.pattern } : null,
      });
    },
  });
}

// ---------------------------------------------------------------- 通用小表单

/** 通用小表单：控件数组 + 可选复选框 + 提交回调。成功关门、给反馈并刷新；
 * 失败把 {error} 文案写在表单里。 */
function showForm({ title, controls, checkbox = null, done = "已完成", submit }) {
  const dialog = $("form-dialog");
  const body = $("form-body");
  body.textContent = "";
  body.append(el("h2", null, title));
  const inputs = [];
  for (const control of controls) {
    const row = el("div", "form-row");
    const input = document.createElement("input");
    input.type = control.password ? "password" : "text";
    input.value = control.initial ?? "";
    row.append(el("label", null, control.label), input);
    body.append(row);
    inputs.push([control.key, input]);
  }
  let checkboxEl = null;
  if (checkbox) {
    const row = el("div", "form-row check-row");
    checkboxEl = document.createElement("input");
    checkboxEl.type = "checkbox";
    checkboxEl.checked = checkbox.checked;
    row.append(checkboxEl, el("label", null, checkbox.label));
    body.append(row);
  }
  const errorEl = el("div", "error");
  const actions = el("div", "dialog-actions");
  const cancelButton = el("button", "btn ghost", "取消");
  cancelButton.addEventListener("click", () => dialog.close());
  const okButton = el("button", "btn primary", "确定");
  okButton.addEventListener("click", async () => {
    okButton.disabled = true;
    errorEl.textContent = "";
    try {
      const data = Object.fromEntries(inputs.map(([key, input]) => [key, input.value]));
      const response = await submit(data, checkboxEl?.checked ?? false);
      if (response?.error) {
        errorEl.textContent = response.error;
        return;
      }
      dialog.close();
      const doneText = typeof done === "function" ? done(response) : done;
      if (openDetailId !== null) drawerHint(doneText);
      else toast(doneText);
      if (openDetailId !== null) await openDetail(openDetailId);
      await refresh();
    } finally {
      okButton.disabled = false;
    }
  });
  actions.append(cancelButton, okButton);
  body.append(errorEl, actions);
  // 关门即清：轮换新值/添加密钥值等明文不留残在隐藏弹层的 DOM 里（与
  // backup/restore/reset 路径清空纪律一致）；一次监听覆盖 取消/Esc/成功 三条路径
  dialog.addEventListener("close", () => {
    for (const [, input] of inputs) input.value = "";
    if (checkboxEl) checkboxEl.checked = false;
    errorEl.textContent = "";
  }, { once: true });
  dialog.showModal();
  inputs[0][1].focus();
}

// ---------------------------------------------------------------- ④ 审计日志

async function renderAudit() {
  const response = await send("audit", {});
  if (!response) return;
  const { rows, chainOk, firstBadId } = response;
  if (!rows) return;
  const chainEl = $("audit-chain");
  chainEl.className = `chain-line ${chainOk ? "ok" : "bad"}`;
  chainEl.textContent = chainOk
    ? `审计链完好（${rows.length} 条记录）`
    : `⚠ 审计链在第 ${firstBadId} 行断裂`;
  const tbody = $("audit-rows");
  tbody.textContent = "";
  const empty = $("audit-empty");
  if (!rows.length) {
    empty.textContent = "还没有审计记录";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  for (const row of rows) {
    const tr = el("tr");
    tr.append(
      el("td", "muted", formatStamp(row.ts)),
      el("td", null, row.event),
      el("td", null, row.actor),
      el("td", null, row.nameSnapshot ?? ""),
    );
    tbody.append(tr);
  }
}

// ---------------------------------------------------------------- ⑤ 批量导入（两步向导）

/** 解析格式 → 中文标签（预览区 meta 用）。 */
export const IMPORT_FORMAT_LABELS = {
  "bitwarden-csv": "Bitwarden CSV 导出",
  "bitwarden-json": "Bitwarden JSON 导出",
  "otpauth-list": "otpauth:// 列表",
  "kv-text": ".env / 键值文本",
};

/** 预览行渲染上限：超出部分按解析默认值确认（meta 文案明示），值绝不渲染。 */
export const WIZARD_PREVIEW_CAP = 200;
const WIZARD_SKIP_LIST_CAP = 50;

// 向导在途计划：items 里持有原始值（内存，绝不渲染）；确认/取消即清。
let importPlan = null;

/** 确认步结果汇总文案（node 可测，纯函数）。 */
export function formatWizardSummary(summary) {
  const parts = [];
  if (summary.created) parts.push(`新建 ${summary.created}`);
  if (summary.deduped) parts.push(`合并已有 ${summary.deduped}`);
  if (summary.rejected) parts.push(`空值拒绝 ${summary.rejected}`);
  if (summary.unticked) parts.push(`未勾选跳过 ${summary.unticked}`);
  if (summary.failed) parts.push(`失败 ${summary.failed}`);
  return parts.length ? `导入完成：${parts.join("，")}` : "没有条目被导入";
}

function renderImportPreview() {
  const plan = importPlan;
  const tbody = $("import-preview-rows");
  const meta = $("import-preview-meta");
  const skippedEl = $("import-skipped");
  tbody.textContent = "";
  const shown = plan.items.slice(0, WIZARD_PREVIEW_CAP);
  for (const item of shown) {
    const tr = el("tr");
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = true;
    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.value = item.name;
    nameInput.className = "wizard-name";
    nameInput.placeholder = "留空自动命名";
    const platformInput = document.createElement("input");
    platformInput.type = "text";
    platformInput.value = item.platform;
    platformInput.className = "wizard-platform";
    platformInput.placeholder = item.platform ? "" : "留空走检测";
    const notes = item.warnings.length ? `（${item.warnings.join("；")}）` : "";
    tr.append(
      (() => { const td = el("td"); td.append(check); return td; })(),
      (() => { const td = el("td"); td.append(nameInput); return td; })(),
      (() => { const td = el("td"); td.append(platformInput); return td; })(),
      el("td", "muted", `${previewText(item.value)}${notes}`),
    );
    tbody.append(tr);
  }
  const extra = plan.items.length > shown.length
    ? `；预览只列前 ${shown.length} 条，其余按解析默认值确认` : "";
  meta.textContent = `${IMPORT_FORMAT_LABELS[plan.format] ?? plan.format}：识别 `
    + `${plan.items.length} 条、跳过 ${plan.skipped.length} 条${extra}`
    + (plan.truncated ? "；已达单次导入上限，请分批" : "");
  skippedEl.textContent = "";
  if (plan.skipped.length) {
    skippedEl.classList.remove("hidden");
    skippedEl.append(el("h3", "section-title", `跳过 ${plan.skipped.length} 条（只列预览与原因，不含值）`));
    const list = el("ul", "corr-list");
    for (const entry of plan.skipped.slice(0, WIZARD_SKIP_LIST_CAP)) {
      const li = el("li");
      li.append(
        el("span", null, entry.preview || "(无预览)"),
        el("span", "muted", ` — ${entry.reason}`),
      );
      list.append(li);
    }
    if (plan.skipped.length > WIZARD_SKIP_LIST_CAP) {
      list.append(el("li", "muted", `…其余 ${plan.skipped.length - WIZARD_SKIP_LIST_CAP} 条同上`));
    }
    skippedEl.append(list);
  } else {
    skippedEl.classList.add("hidden");
  }
}

/** 第一步：解析预览（textarea 或文件），不落库。 */
async function parseImport() {
  const resultEl = $("import-result");
  resultEl.textContent = "";
  const fileInput = $("import-file");
  let text = $("import-text").value;
  if (fileInput.files.length) {
    const file = fileInput.files[0];
    if (file.size > IMPORT_FILE_MAX) {
      resultEl.textContent = `文件超过 ${IMPORT_FILE_MAX} 字节上限`;
      return;
    }
    text = await file.text();
  }
  if (!text.trim()) {
    resultEl.textContent = "没有可导入的内容";
    return;
  }
  const button = $("import-parse");
  button.disabled = true;
  try {
    let plan;
    try {
      plan = parseBulk(text); // 纯解析：不落库、不检测（信任闸在第二步勾选 + store-direct）
    } catch (error) {
      resultEl.textContent = `解析失败：${error?.message ?? String(error)}`;
      return;
    }
    if (!plan.items.length && !plan.skipped.length) {
      resultEl.textContent = "没有识别出可导入的条目";
      return;
    }
    importPlan = { ...plan, tags: parseTagList($("import-tags").value) };
    $("import-confirm-result").textContent = "";
    renderImportPreview();
    $("import-preview-card").classList.remove("hidden");
  } finally {
    button.disabled = false;
  }
}

/** 第二步确认：逐条 store-direct（显式平台 = 用户拍板 manual；空平台走 SW
 * 现场检测）。响应二元逐条消化，失败明细只列前 5 条。 */
async function confirmImport() {
  const resultEl = $("import-confirm-result");
  resultEl.textContent = "";
  if (!importPlan) return;
  const plan = importPlan;
  const domRows = [...$("import-preview-rows").querySelectorAll("tr")];
  const button = $("import-confirm");
  button.disabled = true;
  const summary = { total: plan.items.length, created: 0, deduped: 0, rejected: 0, unticked: 0, failed: 0, failures: [] };
  try {
    for (let index = 0; index < plan.items.length; index += 1) {
      const item = plan.items[index];
      let { name, platform } = item;
      if (index < domRows.length) {
        if (!domRows[index].querySelector("input[type=checkbox]").checked) {
          summary.unticked += 1;
          continue;
        }
        name = domRows[index].querySelector(".wizard-name").value.trim();
        platform = domRows[index].querySelector(".wizard-platform").value.trim();
      }
      const payload = { value: item.value };
      if (name) payload.name = name;
      if (item.keyName) payload.keyName = item.keyName;
      if (platform) payload.platform = platform;
      if (item.sourceUrl) payload.sourceUrl = item.sourceUrl;
      if (item.note) payload.note = item.note;
      if (plan.tags.length) payload.tags = plan.tags;
      const response = await send("store-direct", payload);
      if (!response || response.error) {
        summary.failed += 1;
        if (summary.failures.length < 5) {
          summary.failures.push(
            `${name || item.keyName || "(未命名)"}：${response?.error ?? "background 无响应"}`,
          );
        }
        continue;
      }
      if (response.outcome === "created") summary.created += 1;
      else if (response.outcome === "deduped") summary.deduped += 1;
      else summary.rejected += 1;
    }
    resultEl.textContent = formatWizardSummary(summary)
      + (summary.failures.length ? `；失败明细：${summary.failures.join("；")}` : "");
    importPlan = null;
    $("import-preview-card").classList.add("hidden");
    $("import-text").value = "";
    $("import-file").value = "";
    $("import-file-name").textContent = "";
  } finally {
    button.disabled = false;
  }
  await refresh();
}

function cancelImport() {
  importPlan = null; // 原始值只活在计划里，取消即丢弃
  $("import-preview-card").classList.add("hidden");
  $("import-confirm-result").textContent = "";
}

// ---------------------------------------------------------------- 生成器（密码）

let generatedPassword = "";

/** 从生成器卡读选项并归一化；非法输入返回错误文案。 */
function readGeneratorOptions() {
  return resolveOptions({
    length: $("gen-length").value,
    lower: $("gen-lower").checked,
    upper: $("gen-upper").checked,
    digits: $("gen-digits").checked,
    symbols: $("gen-symbols").checked,
    ambiguousOff: $("gen-ambiguous").checked,
  });
}

async function runGenerate() {
  const resultEl = $("gen-result");
  resultEl.textContent = "";
  let options;
  let result;
  try {
    options = readGeneratorOptions();
    result = generatePassword(options);
  } catch (error) {
    resultEl.textContent = `生成失败：${error?.message ?? String(error)}`;
    return;
  }
  generatedPassword = result.password;
  $("gen-output").textContent = result.password;
  $("gen-strength").textContent = `约 ${result.entropyBits} 比特（粗估）`;
  $("gen-copy").disabled = false;
  $("gen-save").disabled = false;
  // 记住上次选择：settings.generator 走 C7（锁定态也可写——settings 在
  // backend meta，不碰 session）。失败不打断生成，只提示。
  const response = await send("set-settings", { patch: { generator: options } });
  if (response?.error) {
    resultEl.textContent = `已生成；默认值保存失败：${response.error}`;
  }
}

/** 复制生成的密码 → 30s TTL 擦除通道（与 popup 复制流同一条 C1#11 链路）。 */
async function copyGenerated() {
  if (!generatedPassword) return;
  try {
    await navigator.clipboard.writeText(generatedPassword);
  } catch (_) {
    $("gen-result").textContent = "复制失败（剪贴板不可用）";
    return;
  }
  const copied = await send("copied");
  $("gen-result").textContent = copied?.ok
    ? `已复制，${copied.ttl} 秒后自动覆盖剪贴板（无法排除系统剪贴板历史）`
    : "已复制，但 30 秒自动擦除安排失败";
}

/** 生成的密码是随机串，检测器不认识——存入必须显式平台（manual 语义）。 */
function saveGenerated() {
  if (!generatedPassword) return;
  showForm({
    title: "把生成的密码存入库",
    controls: [
      { label: "值", key: "value", password: true, initial: generatedPassword },
      { label: "名称（可选，留空自动命名）", key: "name" },
      { label: "键名（可选）", key: "keyName" },
      { label: "平台（必填，如 github.com）", key: "platform" },
    ],
    done: (response) => {
      const base = response.outcome === "created"
        ? `已添加 ${response.name}`
        : `已合并到现有记录 ${response.name}`;
      return response.warning ? `${base}——${response.warning}` : base;
    },
    submit: (data) => {
      const payload = { value: data.value };
      if (data.name.trim()) payload.name = data.name.trim();
      if (data.keyName.trim()) payload.keyName = data.keyName.trim();
      if (data.platform.trim()) payload.platform = data.platform.trim();
      if (!payload.platform) {
        return { error: "生成的密码是随机串，检测器不认识——请填平台（如 github.com）" };
      }
      return send("store-direct", payload);
    },
  });
}

/** 启动时回填上次生成器选择（settings.generator；读不到用默认）。 */
async function loadGeneratorDefaults() {
  const response = await send("get-settings");
  let options = GENERATOR_DEFAULTS;
  try {
    options = resolveOptions(response?.settings?.generator);
  } catch (_) {
    /* settings 缺失/损坏 → 默认 */
  }
  $("gen-length").value = String(options.length);
  $("gen-lower").checked = options.lower;
  $("gen-upper").checked = options.upper;
  $("gen-digits").checked = options.digits;
  $("gen-symbols").checked = options.symbols;
  $("gen-ambiguous").checked = options.ambiguousOff;
}

// ---------------------------------------------------------------- ⑥ 备份恢复

function backupFileName() {
  return `keyvault-backup-${new Date().toISOString().slice(0, 10)}.kvbe1.txt`;
}

function downloadText(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}

async function exportBackup() {
  const resultEl = $("backup-result");
  resultEl.textContent = "";
  const password = $("backup-password").value;
  if (password.length < 8) {
    resultEl.textContent = "备份口令至少 8 位——它是唯一防线";
    return;
  }
  if (password !== $("backup-password2").value) {
    resultEl.textContent = "两次输入的口令不一致";
    return;
  }
  const button = $("backup-export");
  button.disabled = true;
  try {
    const response = await send("export-backup", { password });
    if (!response || response.error) {
      resultEl.textContent = `导出失败：${response?.error ?? "background 无响应"}`;
      return;
    }
    downloadText(backupFileName(), response.blob);
    resultEl.textContent = "已导出到下载。口令丢失无法恢复，请把口令记在别处。";
    $("backup-password").value = "";
    $("backup-password2").value = "";
  } finally {
    button.disabled = false;
  }
}

async function restoreBackup() {
  const resultEl = $("restore-result");
  resultEl.textContent = "";
  const file = $("restore-file").files[0];
  const password = $("restore-password").value;
  if (!file) {
    resultEl.textContent = "先选择备份文件（KVBE1 文本件）";
    return;
  }
  if (!password) {
    resultEl.textContent = "先输入备份口令";
    return;
  }
  const button = $("restore-submit");
  button.disabled = true;
  try {
    const blob = await file.text();
    const summary = await send("restore-backup", { password, blob });
    if (!summary || summary.error) {
      resultEl.textContent = `恢复失败：${summary?.error ?? "background 无响应"}`;
      return;
    }
    resultEl.textContent = formatRestoreResult(summary);
    $("restore-password").value = "";
    $("restore-file").value = "";
    $("restore-file-name").textContent = "";
  } finally {
    button.disabled = false;
  }
  await refresh();
}

// ---------------------------------------------------------------- ⑦ 设置

async function renderSettings() {
  const response = await send("get-settings");
  const settings = response?.settings;
  if (!settings) return;
  $("set-autolock").value = String(settings.autoLockMinutes ?? 15);
  $("set-capture").checked = Boolean(settings.captureEnabled);
  $("set-ignored").value = (settings.ignoredHosts ?? []).join("\n");
  $("settings-saved").textContent = "";
  await renderCorrections();
}

async function renderCorrections() {
  const response = await send("corrections");
  const rows = response?.rows;
  if (!rows) return;
  const list = $("corr-rows");
  list.textContent = "";
  const empty = $("corr-empty");
  if (!rows.length) {
    empty.textContent = "还没有纠正规则";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  for (const row of rows) {
    const li = el("li", "corr-row");
    li.append(
      el("span", "corr-kind", CORRECTION_KIND_LABEL[row.kind] ?? row.kind),
      el("span", "corr-pattern", `${row.pattern} → ${row.platform}`),
      el("span", "corr-note muted", row.note ?? ""),
    );
    const deleteButton = el("button", "btn ghost small", "删除");
    deleteButton.addEventListener("click", async () => {
      deleteButton.disabled = true;
      try {
        const reply = await send("delete-correction", { id: row.id });
        if (reply?.error) {
          toast(reply.error);
          return;
        }
        toast("已删除纠正规则——相关值恢复原判定");
        await renderCorrections();
      } finally {
        deleteButton.disabled = false;
      }
    });
    li.append(deleteButton);
    list.append(li);
  }
}

async function saveSettings() {
  const savedEl = $("settings-saved");
  const response = await send("set-settings", {
    patch: {
      autoLockMinutes: clampAutoLock($("set-autolock").value),
      captureEnabled: $("set-capture").checked,
      ignoredHosts: parseHostList($("set-ignored").value),
    },
  });
  if (response?.error) {
    savedEl.textContent = `保存失败：${response.error}`;
    return;
  }
  savedEl.textContent = "已保存";
  setTimeout(() => { savedEl.textContent = ""; }, 2000);
}

/** 整库清除（C1#30）：双门齐 → reset 鉴权消息。口令错被拒且数据完好；
 * 成功后 background 已删库，整页刷新回到未初始化门。 */
async function wipeVault() {
  const resultEl = $("reset-result");
  resultEl.textContent = "";
  const gateError = checkResetGates($("reset-password").value, $("reset-confirm").value);
  if (gateError) {
    resultEl.textContent = gateError;
    return;
  }
  const button = $("reset-btn");
  button.disabled = true;
  try {
    const response = await send("reset", { password: $("reset-password").value });
    if (!response || response.error) {
      resultEl.textContent = `清除被拒绝：${response?.error ?? "background 无响应"}（数据未动）`;
      return;
    }
    $("reset-password").value = "";
    $("reset-confirm").value = "";
    location.reload();
  } finally {
    button.disabled = false;
  }
}

// ---------------------------------------------------------------- ⑧ 泄露扫描

function setScanStatus(text, cls = "") {
  const node = $("scan-status");
  node.className = `result block ${cls}`;
  node.textContent = text;
}

/** 收集待扫文本：二进制（头 8KB 含 \x00）与超 50MB 的跳过并计数，读取失败
 * 的明确记错——不静默崩。entries = [{file?, handle?, path}]。 */
async function collectEntries(entries) {
  const items = [];
  const readErrors = [];
  let skippedBinary = 0;
  let skippedLarge = 0;
  for (const entry of entries) {
    try {
      const file = entry.file ?? (await entry.handle.getFile());
      if (file.size > MAX_FILE_SIZE) {
        skippedLarge += 1;
        continue;
      }
      const head = new Uint8Array(await file.slice(0, BINARY_CHECK_SIZE).arrayBuffer());
      if (isBinaryHead(head)) {
        skippedBinary += 1;
        continue;
      }
      const text = await file.text();
      for (const chunk of chunkTextByLines(text, MAX_CONTENT_CHARS)) {
        items.push({
          path: entry.path,
          name: `#${items.length}\u0001${entry.path}\u0001L${chunk.lineOffset + 1}`,
          lineOffset: chunk.lineOffset,
          chunk: chunk.content,
        });
      }
    } catch (error) {
      readErrors.push(`${entry.path} —— 读取失败：${error.message}`);
    }
  }
  return { items, skippedBinary, skippedLarge, readErrors };
}

// 扫描流水线重入锁：收集+分批是长流程，连点/换文件不应并发两趟
let scanBusy = false;

async function scanPasted() {
  if (scanBusy) return;
  const text = $("scan-paste").value;
  if (!text.trim()) {
    setScanStatus("粘贴框是空的", "bad");
    return;
  }
  scanBusy = true;
  try {
    const chunks = chunkTextByLines(text, MAX_CONTENT_CHARS);
    const items = chunks.map((chunk, index) => ({
      path: "粘贴的文本",
      name: `#paste-${index}\u0001粘贴的文本\u0001L${chunk.lineOffset + 1}`,
      lineOffset: chunk.lineOffset,
      chunk: chunk.content,
    }));
    await runScan({ items, skippedBinary: 0, skippedLarge: 0, readErrors: [] });
  } finally {
    scanBusy = false;
  }
}

async function scanFromSelection(fileList) {
  if (scanBusy) return;
  scanBusy = true;
  try {
    setScanStatus("读取文件中…");
    const entries = [...fileList].map((file) => ({
      file,
      path: file.webkitRelativePath || file.name,
    }));
    await runScan(await collectEntries(entries));
  } finally {
    scanBusy = false;
  }
}

async function pickDirectoryAndScan() {
  if (typeof showDirectoryPicker === "function") {
    try {
      const dirHandle = await showDirectoryPicker();
      await scanDirectoryHandle(dirHandle);
      return;
    } catch (error) {
      if (error?.name === "AbortError") return; // 用户取消不是错误
      // 其余错误（权限/环境不支持）落到 webkitdirectory 回退
    }
  }
  $("scan-dir-fallback").click();
}

/** 递归枚举目录句柄；相对路径以所选目录名为根（对齐桌面命中行显示路径）。 */
async function walkDirectory(handle, prefix, out) {
  for await (const entry of handle.values()) {
    if (entry.kind === "file") {
      out.push({ handle: entry, path: `${prefix}${entry.name}` });
    } else {
      await walkDirectory(entry, `${prefix}${entry.name}/`, out);
    }
  }
}

async function scanDirectoryHandle(dirHandle) {
  if (scanBusy) return;
  scanBusy = true;
  setScanStatus("收集目录文件中…");
  try {
    const files = [];
    await walkDirectory(dirHandle, `${dirHandle.name}/`, files);
    setScanStatus(`读取文件中…（${files.length} 个文件）`);
    await runScan(await collectEntries(files));
  } catch (error) {
    // 遍历中途失败（句柄权限收回等）：明确报错，不卡在半程也不静默当干净
    setScanStatus(`目录读取失败：${error.message}`, "bad");
  } finally {
    scanBusy = false;
  }
}

/** 分批发送（C6）：每批 ≤100 条 / ≤4M 字符，等响应再发下一批；被拒的整批
 * 逐条记显式错误，其余批次继续——扫得完的扫完，扫不了的明说。 */
async function runScan(collected) {
  if (!collected.items.length) {
    renderScanResults(collected, { batchCount: 0, hits: [], failed: [] });
    return;
  }
  const batches = planScanBatches(collected.items);
  const hits = [];
  const failed = [];
  for (let index = 0; index < batches.length; index++) {
    const batch = batches[index];
    setScanStatus(`扫描中… 第 ${index + 1}/${batches.length} 批（共 ${collected.items.length} 条文本）`);
    const texts = batch.map((item) => ({ name: item.name, content: item.chunk }));
    const response = await send("scan-texts", { texts });
    if (!response || response.error) {
      const reason = response?.error ?? "background 无响应";
      for (const item of batch) failed.push(`${item.path} —— 本批被拒：${reason}`);
      continue;
    }
    hits.push(...decorateHits(response.hits ?? [], batch));
  }
  renderScanResults(collected, { batchCount: batches.length, hits, failed });
}

function renderScanResults(collected, { batchCount, hits, failed }) {
  const summary = summarizeScanRun(collected, { batchCount, hits, failed });
  setScanStatus(summary.statusText, summary.statusCls);
  renderScanErrors(summary.errors);
  const tbody = $("scan-rows");
  tbody.textContent = "";
  for (const hit of hits) {
    const tr = el("tr");
    tr.append(
      el("td", "preview", hit.path ?? hit.textName),
      el("td", "muted", String(hit.lineNo ?? "")),
      el("td", null, hit.name),
      el("td", "muted", hit.platform),
      el("td", null, null),
    );
    tr.children[4].append(el("span", `st st-${hit.status}`, STATUS_LABEL[hit.status] ?? hit.status));
    tbody.append(tr);
  }
  const empty = $("scan-empty");
  if (hits.length) {
    empty.classList.add("hidden");
  } else {
    empty.textContent = "未发现库中指纹的泄露";
    empty.classList.remove("hidden");
  }
}

function renderScanErrors(errors) {
  const box = $("scan-errors");
  box.textContent = "";
  if (!errors.length) {
    box.classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");
  box.append(el("div", "error-head", `以下 ${errors.length} 项无法扫描：`));
  for (const line of errors.slice(0, SCAN_ERROR_LIST_CAP)) {
    box.append(el("div", "error-line", line));
  }
  if (errors.length > SCAN_ERROR_LIST_CAP) {
    box.append(el("div", "error-line", `…另有 ${errors.length - SCAN_ERROR_LIST_CAP} 项未逐条列出`));
  }
}

// ---------------------------------------------------------------- 启动

function wireStatic() {
  for (const button of document.querySelectorAll("#nav button")) {
    button.addEventListener("click", () => selectTab(button.dataset.tab));
  }
  $("lock-btn").addEventListener("click", async () => {
    await send("lock");
    await renderGate();
  });
  $("gate-password").addEventListener("keydown", (event) => {
    if (event.key === "Enter") $("gate-submit").click();
  });

  let searchJob = null;
  $("search").addEventListener("input", () => {
    clearTimeout(searchJob);
    searchJob = setTimeout(() => renderSecrets(), 150);
  });
  $("status-filter").addEventListener("change", () => renderSecrets());
  $("add-btn").addEventListener("click", () => {
    showForm({
      title: "添加密钥",
      controls: [
        { label: "值", key: "value", password: true },
        { label: "名称（可选，留空自动命名）", key: "name" },
        { label: "键名（可选）", key: "keyName" },
        { label: "平台（可选，留空走检测）", key: "platform" },
      ],
      done: (response) => {
        const base = response.outcome === "created"
          ? `已添加 ${response.name}`
          : `已合并到现有记录 ${response.name}`;
        return response.warning ? `${base}——${response.warning}` : base;
      },
      submit: (data) => {
        const payload = { value: data.value };
        if (data.name.trim()) payload.name = data.name.trim();
        if (data.keyName.trim()) payload.keyName = data.keyName.trim();
        if (data.platform.trim()) payload.platform = data.platform.trim();
        return send("store-direct", payload);
      },
    });
  });

  $("drawer-close").addEventListener("click", () => $("drawer").close());
  $("drawer").addEventListener("close", () => {
    openDetailId = null;
    stopTotp();
    renderSecrets();
  });

  $("audit-refresh").addEventListener("click", () => renderAudit());
  $("import-parse").addEventListener("click", parseImport);
  $("import-confirm").addEventListener("click", () => confirmImport());
  $("import-cancel").addEventListener("click", cancelImport);
  $("import-file").addEventListener("change", (event) => {
    $("import-file-name").textContent = event.target.files[0]?.name ?? "";
  });
  $("gen-generate").addEventListener("click", runGenerate);
  $("gen-copy").addEventListener("click", copyGenerated);
  $("gen-save").addEventListener("click", saveGenerated);
  $("backup-export").addEventListener("click", exportBackup);
  $("restore-file").addEventListener("change", (event) => {
    $("restore-file-name").textContent = event.target.files[0]?.name ?? "";
  });
  $("restore-submit").addEventListener("click", restoreBackup);
  $("settings-save").addEventListener("click", saveSettings);
  $("reset-btn").addEventListener("click", wipeVault);

  $("scan-paste-btn").addEventListener("click", scanPasted);
  $("scan-file").addEventListener("change", (event) => {
    if (event.target.files.length) scanFromSelection(event.target.files);
  });
  $("scan-dir-btn").addEventListener("click", pickDirectoryAndScan);
  $("scan-dir-fallback").addEventListener("change", (event) => {
    if (event.target.files.length) scanFromSelection(event.target.files);
  });
}

async function boot() {
  wireStatic();
  void loadGeneratorDefaults(); // 生成器默认值回填（settings 在锁定态也可读）
  await renderGate();
}

// 浏览器入口：node --test 直 import 本文件时无 document，不启动 UI——
// 纯逻辑（分批/切块/双门校验/范围建议等）由此可测。
if (typeof document !== "undefined") {
  boot();
}
