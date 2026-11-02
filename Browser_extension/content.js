// KeyVault 独立版内容脚本：监听页面 copy/cut 事件 → 本机 SW 检测 →
// 高置信候选弹页内提示条（像浏览器保存密码的气泡）。
//
// 两条职责：
// 1. 捕获：copy 事件是用户主动动作，clipboardData 里就是被复制的内容——
//    不需要 clipboardRead 权限，也读不到用户没复制的东西。
// 2. 剪贴板换新信号：**每一次** copy 都通知 SW（哪怕文本太短不送检），
//    SW 据此取消在飞的 TTL 擦除——不覆盖用户新复制的内容。
//
// UI 全部挂 Shadow DOM、全 textContent、异常就地吞掉——扩展绝不弄坏网页。

(() => {
  "use strict";

  const MIN_CHARS = 8;
  const MAX_CHARS = 200000;          // 与 SW 检测的上限一致
  const RECENT_LIMIT = 20;           // 页面级去重环：同文本短时间只发一次
  const recent = [];
  let barRoot = null;

  // 提示条样式（Shadow DOM 内，与页面样式互不污染；深墨蓝与桌面侧边栏同色系）
  const STYLE = `
<style>
  :host { all: initial; }
  .kv-card {
    position: fixed; right: 20px; bottom: 20px; z-index: 2147483647;
    width: 288px; box-sizing: border-box;
    background: #161a23; color: #f2f4fb;
    border: 1px solid #242b3b; border-radius: 12px;
    padding: 14px 16px;
    font: 13px/1.5 "Microsoft YaHei UI", "Segoe UI", sans-serif;
    box-shadow: 0 8px 28px rgba(0,0,0,.35);
  }
  .kv-title { font-weight: 600; font-size: 14px; margin-bottom: 4px; }
  .kv-meta {
    color: #9aa4b8; font-size: 12px; margin-bottom: 12px;
    overflow-wrap: anywhere; font-family: "Cascadia Mono", Consolas, monospace;
  }
  .kv-row { display: flex; gap: 8px; }
  button {
    flex: 1; padding: 7px 0; border-radius: 8px; border: 1px solid #242b3b;
    font: inherit; font-size: 12.5px; cursor: pointer;
  }
  .kv-primary { background: #3d5fe8; border-color: #2743b8; color: #fff; }
  .kv-primary:hover { background: #2f4fd6; }
  .kv-ghost { background: transparent; color: #9aa4b8; }
  .kv-ghost:hover { color: #d4dcec; }
  .kv-note { font-size: 12.5px; padding: 2px 0; overflow-wrap: anywhere; }
  .kv-note.ok { color: #34d399; }
  .kv-note.bad { color: #f87171; }
</style>
`;

  document.addEventListener("copy", onCopy, true);
  document.addEventListener("cut", onCopy, true);

  function onCopy(event) {
    try {
      const text = event.clipboardData
        ? event.clipboardData.getData("text/plain") : "";
      // 剪贴板换新信号：无条件先发（SW 取消在飞的擦除）
      chrome.runtime.sendMessage({ kind: "clipboard-changed" }, () => {
        void chrome.runtime.lastError; // SW 睡着也无妨，忽略
      });
      if (!text || text.length < MIN_CHARS || text.length > MAX_CHARS) return;
      const fingerprint = hash(text);
      if (recent.includes(fingerprint)) return;
      recent.push(fingerprint);
      if (recent.length > RECENT_LIMIT) recent.shift();

      const payload = {
        kind: "capture",
        text: text,
        source_url: location.href,
        title: document.title || "",
      };
      chrome.runtime.sendMessage(payload, (response) => {
        if (chrome.runtime.lastError || !response || response.error) return;
        if (response.disabled || response.ignored) return;
        maybeShowBar(response, payload);
      });
    } catch (_) {
      /* 页面脚本环境异常绝不外抛 */
    }
  }

  function maybeShowBar(response, payload) {
    if (!response.candidate) return; // 0 条或多条候选：不打扰
    showBar(response.candidate, response.value ?? payload.text, payload);
  }

  function showBar(candidate, value, capturePayload) {
    hideBar();
    barRoot = document.createElement("div");
    barRoot.id = "keyvault-capture-bar";
    const shadow = barRoot.attachShadow({ mode: "open" });
    shadow.innerHTML = STYLE;
    const card = document.createElement("div");
    card.className = "kv-card";
    const title = document.createElement("div");
    title.className = "kv-title";
    title.textContent = candidate.platform === "openai-compatible"
      ? "检测到 API Key（厂商未定）" : "检测到 API Key";
    const meta = document.createElement("div");
    meta.className = "kv-meta";
    meta.textContent = candidate.platform
      + (candidate.keyName ? " · " + candidate.keyName : "")
      + " · " + candidate.preview;
    const buttons = document.createElement("div");
    buttons.className = "kv-row";
    const save = document.createElement("button");
    save.className = "kv-primary";
    save.textContent = "存入 KeyVault";
    const dismiss = document.createElement("button");
    dismiss.className = "kv-ghost";
    dismiss.textContent = "忽略";
    buttons.append(save, dismiss);
    card.append(title, meta, buttons);
    shadow.appendChild(card);
    document.documentElement.appendChild(barRoot);

    save.addEventListener("click", () => {
      chrome.runtime.sendMessage({
        kind: "store", text: value,
        source_url: capturePayload.source_url, title: capturePayload.title,
      }, (response) => {
        const failed = chrome.runtime.lastError || !response || response.error;
        showResult(card,
          failed ? "存入失败：" + (response?.error ?? "扩展通信失败")
                 : response.outcome === "deduped"
                   ? "这把 key 已在库里（去重，合并完成）"
                   : "已存入：" + (response.name || ""),
          failed ? "bad" : "ok");
      });
    });
    dismiss.addEventListener("click", () => hideBar());
  }

  function showResult(card, message, kind) {
    card.textContent = "";
    const note = document.createElement("div");
    note.className = "kv-note " + (kind || "ok");
    note.textContent = message;
    card.appendChild(note);
    setTimeout(hideBar, 3200);
  }

  function hideBar() {
    if (barRoot && barRoot.parentNode) barRoot.parentNode.removeChild(barRoot);
    barRoot = null;
  }

  function hash(text) {
    let h = 0;
    for (let i = 0; i < text.length; i++) {
      h = ((h * 31) + text.charCodeAt(i)) | 0;
    }
    return String(h) + ":" + text.length;
  }
})();
