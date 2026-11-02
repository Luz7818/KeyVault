// KeyVault 独立版 options 管理页：三态门（未初始化/锁定/解锁）+ 五个分区
// （仪表盘 / 密钥管理 / 审计 / 备份导入 / 设置）。全 textContent——vault 里的
// 名称与备注是用户数据，绝不 innerHTML。

import { parseSeed, generate } from "./src/core/totp.js";

function send(kind, extra = {}) {
  return chrome.runtime.sendMessage({ kind, ...extra });
}

const $ = (id) => document.getElementById(id);
const STATUS_LABEL = { active: "启用", revoked: "已吊销", expired: "已过期" };

// ---------------------------------------------------------------- 门（三态）

async function renderGate() {
  const status = await send("status");
  updateFoot(status);
  if (status.error) {
    showGate("无法访问保管箱", status.error, "重试", async () => renderGate());
    return;
  }
  if (!status.initialized) {
    showGate("设置主密码，创建本机保管箱",
      "加密数据只存本机浏览器；主密码不设恢复——忘了就是忘了。", "创建",
      async () => {
        const response = await send("setup", { password: $("gate-password").value });
        if (response.error) return response.error;
        return null;
      });
    return;
  }
  if (!status.unlocked) {
    showGate("输入主密码解锁", "", "解锁", async () => {
      const response = await send("unlock", { password: $("gate-password").value });
      return response.error ?? null;
    });
    return;
  }
  $("gate").classList.add("hidden");
  $("lock-btn").classList.remove("hidden");
  await renderTab();
}

function showGate(title, desc, buttonLabel, submitter) {
  $("gate").classList.remove("hidden");
  $("lock-btn").classList.add("hidden");
  for (const section of document.querySelectorAll(".tab")) section.classList.add("hidden");
  $("gate-title").textContent = title;
  $("gate-desc").textContent = desc;
  $("gate-error").textContent = "";
  $("gate-submit").textContent = buttonLabel;
  $("gate-submit").onclick = async () => {
    const error = await submitter();
    if (error) { $("gate-error").textContent = error; return; }
    await renderGate();
  };
  $("gate-password").onkeydown = (e) => { if (e.key === "Enter") $("gate-submit").click(); };
  setTimeout(() => $("gate-password").focus(), 50);
}

function updateFoot(status) {
  const dot = $("foot-dot");
  dot.className = "dot";
  if (status.unlocked) { dot.classList.add("ok"); $("foot-text").textContent = "已解锁"; }
  else if (status.initialized) { dot.classList.add("bad"); $("foot-text").textContent = "已锁定"; }
  else { dot.classList.add("bad"); $("foot-text").textContent = "未初始化"; }
  $("lock-btn").classList.toggle("hidden", !status.unlocked);
}

$("lock-btn").addEventListener("click", async () => {
  await send("lock");
  await renderGate();
});

// ---------------------------------------------------------------- 分区导航

for (const button of document.querySelectorAll("#nav button")) {
  button.addEventListener("click", () => {
    for (const b of document.querySelectorAll("#nav button")) b.classList.remove("active");
    button.classList.add("active");
    renderTab(button.dataset.tab);
  });
}

async function renderTab(tab = document.querySelector("#nav button.active")?.dataset.tab
  ?? "dashboard") {
  for (const section of document.querySelectorAll(".tab")) section.classList.add("hidden");
  $(`tab-${tab}`).classList.remove("hidden");
  if (tab === "dashboard") await renderDashboard();
  if (tab === "secrets") await renderSecrets();
  if (tab === "audit") await renderAudit();
  if (tab === "tools") renderToolsReset();
  if (tab === "settings") await renderSettings();
}

// ---------------------------------------------------------------- 仪表盘

async function renderDashboard() {
  const stats = await send("stats");
  const cards = [
    { label: "全部", num: stats.total, cls: "" },
    { label: "启用", num: stats.active, cls: "" },
    { label: "30 天内到期", num: stats.expiring, cls: "warn" },
    { label: "吊销 / 过期", num: stats.flagged, cls: "bad" },
  ];
  const row = $("stat-cards");
  row.textContent = "";
  for (const card of cards) {
    const div = document.createElement("div");
    div.className = `stat-card ${card.cls}`;
    const num = document.createElement("div");
    num.className = "num";
    num.textContent = String(card.num);
    const label = document.createElement("div");
    label.className = "label";
    label.textContent = card.label;
    div.append(num, label);
    row.appendChild(div);
  }
  const list = $("platform-list");
  list.textContent = "";
  if (!stats.platforms.length) {
    const li = document.createElement("li");
    li.textContent = "还没有启用中的记录";
    list.appendChild(li);
    return;
  }
  for (const { platform, count } of stats.platforms) {
    const li = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = "p-dot";
    const name = document.createElement("span");
    name.className = "p-name";
    name.textContent = platform;
    const num = document.createElement("span");
    num.className = "p-count";
    num.textContent = String(count);
    li.append(dot, name, num);
    list.appendChild(li);
  }
}

// ---------------------------------------------------------------- 密钥管理

let seedCache = new Map(); // id -> base32 种子（页面内存，关页即失）

async function renderSecrets() {
  const rows = await send("list", {
    query: $("search").value.trim(),
    status: $("status-filter").value,
  });
  const tbody = $("rows");
  tbody.textContent = "";
  const empty = $("table-empty");
  if (!rows.length) {
    empty.textContent = $("search").value.trim() ? "没有匹配的记录" : "库里还没有密钥——去网页上复制一个试试";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.className = `st-${row.status}`;
    const name = document.createElement("td");
    name.textContent = row.name;
    const platform = document.createElement("td");
    platform.textContent = row.platform;
    const status = document.createElement("td");
    status.className = `status-dot-${row.status}`;
    status.textContent = `● ${STATUS_LABEL[row.status] ?? row.status}`;
    const preview = document.createElement("td");
    preview.className = "preview";
    preview.textContent = row.preview;
    const updated = document.createElement("td");
    updated.textContent = (row.updatedAt ?? "").slice(0, 19).replace("T", " ");
    tr.append(name, platform, status, preview, updated);
    tr.addEventListener("click", () => openDetail(row));
    tbody.appendChild(tr);
  }
}

$("search").addEventListener("input", () => renderSecrets());
$("status-filter").addEventListener("change", () => renderSecrets());

// ---------------------------------------------------------------- 详情抽屉

async function openDetail(row) {
  const detail = await send("get", { id: row.id });
  const dialog = $("detail");
  dialog.textContent = "";
  const body = document.createElement("div");
  body.className = "dialog-body";

  const title = document.createElement("h2");
  title.textContent = detail.name;
  const badges = document.createElement("div");
  badges.className = "badges";
  const platformBadge = document.createElement("span");
  platformBadge.className = "badge";
  platformBadge.textContent = `${detail.platform} · ${detail.status}`;
  badges.appendChild(platformBadge);

  body.append(title, badges);

  // TOTP 活码：记录值是 base32 种子时出现（试算一次，失败即无此节）
  if (detail.status === "active") {
    try {
      const revealed = await send("reveal", { id: detail.id, event: "totp" });
      const seed = parseSeed(revealed.value);
      seedCache.set(detail.id, b64OfSeed(seed));
      const box = document.createElement("div");
      box.className = "totp-box";
      const code = document.createElement("div");
      code.className = "totp-code";
      const remain = document.createElement("div");
      remain.className = "muted";
      box.append(code, remain);
      body.appendChild(box);
      const tick = async () => {
        if (!dialog.open) return;
        const [value, remaining] = await generate(seed, new Date());
        code.textContent = `${value.slice(0, 3)} ${value.slice(3)}`;
        remain.textContent = `${remaining}s 后刷新`;
        setTimeout(tick, 1000);
      };
      tick();
    } catch (_) { /* 值不是 TOTP 种子：无活码节 */ }
  }

  const grid = document.createElement("div");
  grid.className = "field-grid";
  const fields = [
    ["平台", detail.platform], ["状态", STATUS_LABEL[detail.status] ?? detail.status],
    ["键名", detail.keyName ?? "—"], ["预览", detail.preview, true],
    ["标签", (detail.tags ?? []).join(", ") || "—"],
    ["备注", detail.note || "—"],
    ["来源", `${detail.origin}${detail.sourceUrl ? " · " + detail.sourceUrl : ""}`],
    ["创建", (detail.createdAt ?? "").slice(0, 19).replace("T", " ")],
    ["更新", (detail.updatedAt ?? "").slice(0, 19).replace("T", " ")],
    ["过期", detail.expiresAt ?? "—"],
    ["指纹历史", (detail.fingerprints ?? []).map((f) =>
      `${f.sha256Prefix} ${f.status}`).join(" / ") || "—"],
  ];
  for (const [k, v, clickable] of fields) {
    const kEl = document.createElement("div");
    kEl.className = "k";
    kEl.textContent = k;
    const vEl = document.createElement("div");
    vEl.className = "v" + (clickable ? " clickable" : "");
    vEl.textContent = String(v);
    if (clickable) {
      vEl.title = "点击复制（非敏感值）";
      vEl.addEventListener("click", () => navigator.clipboard.writeText(String(v)));
    }
    grid.append(kEl, vEl);
  }
  body.appendChild(grid);

  const actions = document.createElement("div");
  actions.className = "dialog-actions";
  if (detail.status === "active") {
    actions.appendChild(actionButton("复制值", async () => {
      const revealed = await send("reveal", { id: detail.id, event: "copy" });
      await navigator.clipboard.writeText(revealed.value);
      await send("copied");
      return `已复制，30 秒后自动擦除`;
    }));
    actions.appendChild(actionButton("轮换", () => showForm({
      title: `轮换 — ${detail.name}`, fields: [["新值", "value", true]],
      submit: async (data) => send("rotate", { id: detail.id, value: data.value }),
    })));
    actions.appendChild(actionButton("吊销", () => confirmAnd(
      `吊销 ${detail.name}？`, () => send("revoke", { id: detail.id }))));
  }
  if (detail.status === "revoked" || detail.status === "expired") {
    actions.appendChild(actionButton("重新启用", async () => {
      await send("set-platform", { id: detail.id, platform: detail.platform });
      return null;
    }));
  }
  actions.appendChild(actionButton("重命名", () => showForm({
    title: `重命名 — ${detail.name}`, fields: [["新名称", "name"]],
    initial: { name: detail.name },
    submit: async (data) => send("rename", { id: detail.id, name: data.name }),
  })));
  actions.appendChild(actionButton("设为平台", () => showForm({
    title: `设为平台 — ${detail.name}`, fields: [["平台 id", "platform"]],
    initial: { platform: detail.platform },
    submit: async (data) => send("set-platform", {
      id: detail.id, platform: data.platform,
      remember: detail.keyName ? { kind: "keyname", pattern: `^${detail.keyName}$` } : null,
    }),
  })));
  actions.appendChild(actionButton("删除", () => confirmAnd(
    `删除 ${detail.name}？审计记录保留。`, () => send("delete", { id: detail.id }),
    true)));
  actions.appendChild(closeButton(dialog));
  body.appendChild(actions);
  dialog.appendChild(body);
  dialog.showModal();
  dialog.addEventListener("close", () => {
    seedCache.delete(detail.id);
    renderSecrets();
  }, { once: true });
}

function b64OfSeed(bytes) {
  return btoa(String.fromCharCode(...bytes));
}

function actionButton(label, action) {
  const button = document.createElement("button");
  button.textContent = label;
  button.className = "primary";
  button.addEventListener("click", async () => {
    const error = await action();
    if (error) alert(error);
  });
  return button;
}

function closeButton(dialog) {
  const button = document.createElement("button");
  button.textContent = "关闭";
  button.addEventListener("click", () => dialog.close());
  return button;
}

async function confirmAnd(message, action, danger = false) {
  if (!confirm(message)) return null;
  const response = await action();
  return response?.error ?? null;
}

// 通用小表单（轮换/重命名/设平台）
function showForm({ title, fields, initial = {}, submit }) {
  const dialog = $("form-dialog");
  dialog.textContent = "";
  const body = document.createElement("div");
  body.className = "dialog-body";
  const heading = document.createElement("h2");
  heading.textContent = title;
  body.appendChild(heading);
  const inputs = [];
  for (const [label, key, isPassword] of fields) {
    const row = document.createElement("div");
    row.className = "form-row";
    const labelEl = document.createElement("label");
    labelEl.textContent = label;
    const input = document.createElement("input");
    input.type = isPassword ? "password" : "text";
    if (initial[key]) input.value = initial[key];
    row.append(labelEl, input);
    body.appendChild(row);
    inputs.push([key, input]);
  }
  const error = document.createElement("div");
  error.className = "error";
  const actions = document.createElement("div");
  actions.className = "dialog-actions";
  const cancel = document.createElement("button");
  cancel.textContent = "取消";
  cancel.addEventListener("click", () => dialog.close());
  const submitButton = document.createElement("button");
  submitButton.className = "primary";
  submitButton.textContent = "确定";
  submitButton.addEventListener("click", async () => {
    const data = Object.fromEntries(inputs.map(([key, input]) => [key, input.value]));
    const response = await submit(data);
    if (response?.error) { error.textContent = response.error; return; }
    dialog.close();
    await renderSecrets();
  });
  actions.append(cancel, submitButton);
  body.append(error, actions);
  dialog.appendChild(body);
  dialog.showModal();
  inputs[0][1].focus();
}

// ---------------------------------------------------------------- 审计

$("audit-refresh").addEventListener("click", () => renderAudit());

async function renderAudit() {
  const { rows, chainOk, firstBadId } = await send("audit");
  $("chain-state").textContent = chainOk
    ? "审计链完好" : `⚠ 审计链在第 ${firstBadId} 行断裂`;
  const tbody = $("audit-rows");
  tbody.textContent = "";
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 4;
    td.textContent = "（空）";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }
  for (const row of rows) {
    const tr = document.createElement("tr");
    const ts = document.createElement("td");
    ts.textContent = (row.ts ?? "").slice(0, 19).replace("T", " ");
    const event = document.createElement("td");
    event.textContent = row.event;
    const actor = document.createElement("td");
    actor.textContent = row.actor;
    const name = document.createElement("td");
    name.textContent = row.nameSnapshot ?? "";
    tr.append(ts, event, actor, name);
    tbody.appendChild(tr);
  }
}

// ---------------------------------------------------------------- 备份 / 导入

function renderToolsReset() {
  $("import-result").textContent = "";
  $("backup-result").textContent = "";
}

$("import-submit").addEventListener("click", async () => {
  const text = $("import-text").value;
  if (!text.trim()) return;
  $("import-submit").disabled = true;
  const tags = $("import-tags").value.split(",").map((t) => t.trim()).filter(Boolean);
  const summary = await send("import-text", { text, tags });
  $("import-submit").disabled = false;
  const skipped = summary.skipped?.length ?? 0;
  $("import-result").textContent =
    `新建 ${summary.created ?? 0} · 去重 ${summary.deduped ?? 0} · 跳过 ${skipped}` +
    (skipped ? `（如 ${summary.skipped[0].platform}）` : "");
  $("import-text").value = "";
});

$("export-btn").addEventListener("click", async () => {
  const password = $("backup-password").value;
  if (password.length < 8) {
    $("backup-result").textContent = "备份口令至少 8 位";
    return;
  }
  const response = await send("export-backup", { password });
  if (response.error) {
    $("backup-result").textContent = "导出失败：" + response.error;
    return;
  }
  const blob = new Blob([response.blob], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `keyvault-backup-${new Date().toISOString().slice(0, 10)}.kvb.txt`;
  anchor.click();
  URL.revokeObjectURL(url);
  $("backup-result").textContent = "已导出（口令不设恢复——忘了就解不开）";
});

$("restore-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const password = $("backup-password").value;
  if (password.length < 8) {
    $("backup-result").textContent = "先在上方输入备份口令，再选择文件";
    return;
  }
  const blob = await file.text();
  const summary = await send("restore-backup", { password, blob });
  if (summary.error) {
    $("backup-result").textContent = "恢复失败：" + summary.error;
    return;
  }
  $("backup-result").textContent =
    `恢复完成：新建 ${summary.created} · 去重 ${summary.deduped}`;
});

// ---------------------------------------------------------------- 设置

async function renderSettings() {
  const { settings } = await send("get-settings");
  $("set-autolock").value = String(settings.autoLockMinutes);
  $("set-capture").checked = Boolean(settings.captureEnabled);
  $("set-ignored").value = (settings.ignoredHosts ?? []).join("\n");
  $("settings-saved").textContent = "";
}

$("settings-save").addEventListener("click", async () => {
  const patch = {
    autoLockMinutes: Math.max(0, Math.min(240, Number($("set-autolock").value) || 0)),
    captureEnabled: $("set-capture").checked,
    ignoredHosts: $("set-ignored").value.split(/\n+/)
      .map((s) => s.trim().toLowerCase()).filter(Boolean),
  };
  await send("set-settings", { patch });
  $("settings-saved").textContent = "已保存";
  setTimeout(() => { $("settings-saved").textContent = ""; }, 2000);
});

$("wipe-btn").addEventListener("click", async () => {
  if (!confirm("删除浏览器里的全部 KeyVault 数据？此操作不可恢复（先导出备份）！")) return;
  const { deleteDatabase } = await import("./src/vault/db.js");
  await deleteDatabase();
  await chrome.storage.session.clear();
  location.reload();
});

// ---------------------------------------------------------------- 启动

$("add-btn").addEventListener("click", () => showForm({
  title: "添加密钥", fields: [["值", "value", true], ["名称（可选）", "name"],
    ["键名（可选）", "keyName"], ["平台（可选）", "platform"]],
  submit: async (data) => {
    // 直接 add：平台未给时按检测（无 keyName/主机名多为 unknown，诚实落 unknown）
    return send("store", {
      text: data.value, source_url: "", title: "",
    });
  },
}));

$("import-btn").addEventListener("click", () => {
  document.querySelector('#nav [data-tab="tools"]').click();
});

renderGate();
