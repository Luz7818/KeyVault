// KeyVault 内容脚本：监听本页的复制事件，把被复制的文本交给本机宿主检测。
//
// 设计约束：
// - copy 事件是用户主动动作，clipboardData 里就是被复制的内容——不需要
//   clipboardRead 权限，也读不到用户没复制的东西。
// - 只传文本与页面元数据（URL/标题，非敏感、宿主侧已有把关）；明文检测在
//   宿主进程里完成，本脚本不留存任何副本。
// - 页面里出现的一切 UI 都挂在 Shadow DOM 里，与页面样式互不污染；
//   任何异常都就地吞掉——扩展绝不能弄坏网页。

(() => {
  "use strict";

  const MIN_CHARS = 8;
  const MAX_CHARS = 200000;          // 与宿主 service.MAX_TEXT_CHARS 一致
  const RECENT_LIMIT = 20;           // 页面级去重环：同文本短时间只发一次
  const recent = [];
  let barRoot = null;

  document.addEventListener("copy", onCopy, true);
  document.addEventListener("cut", onCopy, true);

  // 提示条样式（Shadow DOM 内，与页面样式互不污染；深墨蓝与桌面侧边栏同色系）
  const STYLE = `
<style>
  :host { all: initial; }
  .kv-card {
    position: fixed; right: 20px; bottom: 20px; z-index: 2147483647;
    width: 280px; box-sizing: border-box;
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
  .kv-note { color: #34d399; font-size: 12.5px; padding: 2px 0; overflow-wrap: anywhere; }
</style>
`;

  function onCopy(event) {
    try {
      const text = event.clipboardData
        ? event.clipboardData.getData("text/plain") : "";
      if (!text || text.length < MIN_CHARS || text.length > MAX_CHARS) return;
      const fingerprint = hash(text);
      if (recent.includes(fingerprint)) return;
      recent.push(fingerprint);
      if (recent.length > RECENT_LIMIT) recent.shift();
      chrome.runtime.sendMessage(
        {
          kind: "capture",
          text: text,
          source_url: location.href,
          title: document.title || "",
        },
        (response) => {
          if (chrome.runtime.lastError || !response || response.error) return;
          maybeShowBar(response);
        },
      );
    } catch (_) {
      /* 页面脚本环境异常绝不外抛 */
    }
  }

  function maybeShowBar(response) {
    const results = Array.isArray(response.results) ? response.results : [];
    const promptable = results.filter(
      (r) => r.promptable && r.status !== "existing",
    );
    // 0 条：普通复制，安静离开；≥2 条：多个候选一起进桌面审批，不逐条打扰
    if (promptable.length !== 1) return;
    showBar(promptable[0]);
  }

  // ---------------------------------------------------------------- 提示条

  function showBar(row) {
    hideBar();
    barRoot = document.createElement("div");
    barRoot.id = "keyvault-capture-bar";
    const shadow = barRoot.attachShadow({ mode: "open" });
    shadow.innerHTML = STYLE;
    const card = document.createElement("div");
    card.className = "kv-card";
    const title = document.createElement("div");
    title.className = "kv-title";
    title.textContent = "检测到 API Key";
    const meta = document.createElement("div");
    meta.className = "kv-meta";
    meta.textContent =
      row.platform + (row.key_name ? " · " + row.key_name : "") + " · " + row.preview;
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

    save.addEventListener("click", () => decide(row, "approve", card));
    dismiss.addEventListener("click", () => decide(row, "reject", card));
  }

  function decide(row, action, card) {
    chrome.runtime.sendMessage(
      { kind: action, inbox_id: row.inbox_id },
      (response) => {
        const failed = chrome.runtime.lastError || !response || response.error;
        showResult(card, failed ? "操作失败：" + (response && response.error
          ? response.error : chrome.runtime.lastError.message)
          : action === "approve" ? "已存入：" + (response.name || "") : "已忽略");
      },
    );
  }

  function showResult(card, message) {
    card.textContent = "";
    const note = document.createElement("div");
    note.className = "kv-note";
    note.textContent = message;
    card.appendChild(note);
    setTimeout(hideBar, 2600);
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
