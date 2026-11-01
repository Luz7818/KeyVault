// KeyVault popup：搜索已存密钥 → 点击复制（宿主写剪贴板 + 30s 自动擦除）。
// 所有行渲染走 textContent——vault 里的名称是用户数据，绝不 innerHTML。

import { callHost } from "./host-client.js";

const rowsEl = document.getElementById("rows");
const emptyEl = document.getElementById("empty");
const hintEl = document.getElementById("hint");
const dotEl = document.getElementById("status-dot");
const searchEl = document.getElementById("search");

let searchJob = null;

init();

function init() {
  searchEl.addEventListener("input", () => {
    if (searchJob) clearTimeout(searchJob);
    searchJob = setTimeout(() => refresh(searchEl.value.trim()), 150);
  });
  refresh("");
}

async function refresh(query) {
  const status = await callHost({ type: "ping" });
  setStatus(status);
  if (status.error) {
    showEmpty(status.error);
    return;
  }
  const response = await callHost({ type: "list", query: query, limit: 50 });
  if (response.error) {
    showEmpty(response.error);
    return;
  }
  render(response.rows);
}

function setStatus(ping) {
  dotEl.className = "dot";
  if (ping.error) {
    dotEl.classList.add("bad");
    dotEl.title = ping.error;
  } else if (!ping.initialized) {
    dotEl.classList.add("bad");
    dotEl.title = "vault 未初始化——先在桌面端初始化";
  } else {
    dotEl.classList.add("ok");
    dotEl.title = "已连接 KeyVault " + (ping.version || "");
  }
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
  li.addEventListener("click", () => copyRow(li, row.name));
  return li;
}

async function copyRow(li, name) {
  hintEl.textContent = "复制中…";
  const response = await callHost({ type: "copy", name: name });
  if (response.error) {
    hintEl.textContent = "复制失败：" + response.error;
    return;
  }
  hintEl.textContent =
    "已复制 " + name + "，" + Math.round(response.ttl) + " 秒后自动擦除";
}

function showEmpty(message) {
  rowsEl.textContent = "";
  emptyEl.textContent = message;
  emptyEl.classList.remove("hidden");
}
