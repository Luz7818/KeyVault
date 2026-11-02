// KeyVault 独立版 popup：三态（未初始化 → 引导设主密码 / 已锁 → 解锁 /
// 已解锁 → 搜索复制）。复制 = reveal 明文 → 页面写剪贴板（有焦点手势）→
// 通知 SW 安排 TTL 擦除。全 textContent——vault 里的名称是用户数据。

function send(kind, extra = {}) {
  return chrome.runtime.sendMessage({ kind, ...extra });
}

const dotEl = document.getElementById("status-dot");
const lockBtn = document.getElementById("lock-btn");
const gateEl = document.getElementById("gate");
const gateTitle = document.getElementById("gate-title");
const gatePassword = document.getElementById("gate-password");
const gateError = document.getElementById("gate-error");
const gateSubmit = document.getElementById("gate-submit");
const mainEl = document.getElementById("main");
const rowsEl = document.getElementById("rows");
const emptyEl = document.getElementById("empty");
const hintEl = document.getElementById("hint");
const searchEl = document.getElementById("search");

let searchJob = null;

init();

function init() {
  gateSubmit.addEventListener("click", submitGate);
  gatePassword.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitGate();
  });
  lockBtn.addEventListener("click", async () => {
    await send("lock");
    renderStatus();
  });
  searchEl.addEventListener("input", () => {
    if (searchJob) clearTimeout(searchJob);
    searchJob = setTimeout(() => refresh(searchEl.value.trim()), 150);
  });
  renderStatus();
}

async function renderStatus() {
  const status = await send("status");
  const initialized = Boolean(status.initialized);
  const unlocked = Boolean(status.unlocked);
  dotEl.className = "dot";
  if (status.error) {
    dotEl.classList.add("bad");
    dotEl.title = status.error;
  } else if (!initialized) {
    dotEl.classList.add("bad");
    dotEl.title = "尚未设置主密码";
  } else if (!unlocked) {
    dotEl.classList.add("bad");
    dotEl.title = "已锁定";
  } else {
    dotEl.classList.add("ok");
    dotEl.title = "KeyVault " + (status.version || "") + " · 已解锁";
  }

  if (!initialized) {
    showGate("设置主密码，创建本机保管箱", "创建");
    return;
  }
  if (!unlocked) {
    showGate("输入主密码解锁", "解锁");
    return;
  }
  gateEl.classList.add("hidden");
  mainEl.classList.remove("hidden");
  lockBtn.classList.remove("hidden");
  refresh("");
}

function showGate(title, buttonLabel) {
  mainEl.classList.add("hidden");
  lockBtn.classList.add("hidden");
  gateEl.classList.remove("hidden");
  gateTitle.textContent = title;
  gateSubmit.textContent = buttonLabel;
  gateError.textContent = "";
  gatePassword.value = "";
  setTimeout(() => gatePassword.focus(), 50);
}

async function submitGate() {
  const status = await send("status");
  const password = gatePassword.value;
  if (password.length < 8) {
    gateError.textContent = "主密码至少 8 位";
    return;
  }
  const response = status.initialized
    ? await send("unlock", { password })
    : await send("setup", { password });
  if (response.error) {
    gateError.textContent = response.error;
    return;
  }
  renderStatus();
}

async function refresh(query) {
  const response = await send("list", { query });
  if (response.error) {
    showEmpty(response.error);
    return;
  }
  render(response.rows);
}

function render(rows) {
  rowsEl.textContent = "";
  if (!rows.length) {
    showEmpty("没有匹配的记录");
    return;
  }
  emptyEl.classList.add("hidden");
  for (const row of rows) rowsEl.appendChild(rowItem(row));
}

function rowItem(row) {
  const li = document.createElement("li");
  const name = document.createElement("span");
  name.className = "name";
  name.textContent = row.name;
  const platform = document.createElement("span");
  platform.className = "platform";
  platform.textContent = row.platform;
  const preview = document.createElement("span");
  preview.className = "preview";
  preview.textContent = row.preview;
  li.append(name, platform, preview);
  li.title = "复制 " + row.name;
  li.addEventListener("click", () => copyRow(li, row.id, row.name));
  return li;
}

async function copyRow(li, id, name) {
  hintEl.textContent = "复制中…";
  const revealed = await send("reveal", { id, event: "copy" });
  if (revealed.error) {
    hintEl.textContent = "复制失败：" + revealed.error;
    return;
  }
  try {
    await navigator.clipboard.writeText(revealed.value);
  } catch (error) {
    hintEl.textContent = "写剪贴板失败：" + error.message;
    return;
  }
  const copied = await send("copied");
  hintEl.textContent = copied.ok
    ? `已复制 ${name}，${copied.ttl} 秒后自动擦除`
    : `已复制 ${name}（本次未安排自动擦除）`;
}

function showEmpty(message) {
  rowsEl.textContent = "";
  emptyEl.textContent = message;
  emptyEl.classList.remove("hidden");
}
