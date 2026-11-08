// KeyVault 内容脚本 —— 页内捕获与提示条。
//
// 设计依据（Browser_extension/CONTRACTS.md C1#5/#6/#12、C7、C9#1/#7）：
// - copy 事件是用户主动动作，clipboardData 里就是被复制的内容——不需要
//   clipboardRead 权限，也读不到用户没复制的东西。这是桌面 kv/capture/watch.py
//   轮询剪切板在浏览器里的合法等价物；watch 的常驻轮询形态被有意排除，
//   由内容脚本的事件驱动替代。
// - 每一次 copy/cut 先无条件发 kind:"clipboard-changed"：SW 据此取消在飞的
//   30s 擦除（C7），恢复桌面「绝不覆盖用户新复制内容」的核心安全性质
//   （wipe.py:121-146 靠指纹比对实现，浏览器无 clipboardRead 没法按指纹比对，
//   「用户每复制一次就通知一次」是最接近的等价）。因此 isTrusted=false 的
//   合成事件直接忽略——页面脚本伪造的 copy 并没有真的动剪切板。
// - 8–200000 字符的文本送 kind:"capture"，SW 检测后恰返回一条 promptable
//   候选（C9#1：isTrusted 全集且平台非 unknown）才弹 Shadow DOM 提示条；
//   0 条或 ≥2 条都不打扰。20 条内容指纹环防止同文本短时间内重复弹条
//   （语义对齐 watch.py:139-142 的 seen ring：进环即视为「已检过一次」）。
// - [存入] 把原文随 kind:"store" 重发（C1#6 重新检测+去重）——配合 SW 的
//   无状态设计：内容脚本除当前提示条外不持有任何候选。
// - UI 全部挂在 closed Shadow DOM、全部 textContent、异常就地吞掉——扩展绝不
//   弄坏网页。:host all:initial 隔离的是样式**继承**（页面样式不渗入组件、
//   组件样式不外渗）；页面直接命中 host 元素自身的规则仍会生效（host 是
//   light DOM 里的普通 div，外层规则级联优先于 :host），这是固有边界，
//   走查时勿误判为完全隔离。
//
// 形态说明：本文件**不含任何 import/export**。MV3 内容脚本不支持 ES modules
// （manifest 的 content_scripts 无 "type":"module"），必须能被经典 <script>
// 直接加载；node 测试侧经 globalThis.KeyVaultContent 取到同一套实现。

(() => {
  "use strict";

  // ------------------------------------------------------------ 常量

  const MIN_CHARS = 8;        // 送检文本下限（过短的不打扰 SW）
  const MAX_CHARS = 200000;   // 送检文本上限，超长在发送前截停
  // 去重环容量 20 是本模块任务指定的页内数值，与桌面 watch.py:31 的
  // SEEN_RING_SIZE = 200 是**有意分歧**（按 C9 纪律声明理由）：桌面环陪一个
  // 会话级监听进程，页面内容脚本随导航重生、生命周期短，20 条足够防同文本
  // 短时间重复弹条。进环即视为「已检过一次」的语义对齐 watch.py:139-142。
  const RING_SIZE = 20;
  const RESULT_TTL_MS = 3200; // 存入结果展示 3.2s 后自灭
  const HOST_ID = "keyvault-capture-bar";

  const MSG_CAPTURE = "capture";                     // C1#5
  const MSG_STORE = "store";                         // C1#6
  const MSG_CLIPBOARD_CHANGED = "clipboard-changed"; // C1#12

  // ------------------------------------------------------ 纯函数（无 DOM）

  /**
   * 从 copy/cut 事件里取纯文本。非文本复制（图片、文件）取到空串，
   * 走长度闸门自然丢弃；页面环境再怪也只回空串，绝不抛。
   */
  function extractText(event) {
    try {
      if (!event || !event.clipboardData) return "";
      return String(event.clipboardData.getData("text/plain") || "");
    } catch (_) {
      return "";
    }
  }

  // 去重指纹：桌面 watch.py:139 用 sha256(utf-8)，这里用单遍 32 位 FNV-1a +
  // 长度——crypto.subtle 异步且仅在安全上下文可用（http 页面没有）。32 位
  // 对 20 槽环足够：碰撞的后果只是同文本少弹一次条，不丢任何数据；页面即便
  // 刻意构造 FNV 碰撞也藏不住真凭据（要先让用户复制碰撞串、后复制真凭据，
  // 而页面无法预知用户会复制什么）。
  const FNV_OFFSET = 0x811c9dc5;
  const FNV_PRIME = 0x01000193;

  function fnv1a(text) {
    let hash = FNV_OFFSET;
    for (let i = 0; i < text.length; i += 1) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, FNV_PRIME) >>> 0;
    }
    return hash >>> 0;
  }

  /** 文本 → 去重指纹（32 位 FNV-1a + 长度），环内比较用。 */
  function fingerprint(text) {
    return `${fnv1a(text).toString(16)}:${text.length}`;
  }

  /**
   * 候选的平台展示名。歧义（裸 sk- → openai-compatible）诚实显示「厂商未定」，
   * 绝不替用户猜厂商——对齐桌面 rules.py:251-256 的落点与 C9#1 的 UI 口径。
   */
  function platformLabel(candidate) {
    if (candidate && candidate.confidence === "ambiguous") return "厂商未定";
    const platform = candidate ? candidate.platform : "";
    return platform ? String(platform) : "unknown";
  }

  /** meta 行：平台 · 键名 · 掩码预览。键名缺失时跳过，不留双分隔符。 */
  function metaLine(candidate) {
    const parts = [platformLabel(candidate)];
    if (candidate && candidate.keyName) parts.push(String(candidate.keyName));
    if (candidate && candidate.preview) parts.push(String(candidate.preview));
    return parts.join(" · ");
  }

  // ------------------------------------------------------------ 提示条

  // 样式全部挂在 Shadow DOM 内：<style> 经 textContent 注入，全文件不拼任何
  // HTML 字符串（走查用 includes 检查该写法为零出现）。:host all:initial
  // 隔离的是样式继承（页面样式不渗入组件、组件样式不外渗）；页面直接命中
  // host 元素的规则仍会生效，见文件头的边界说明。
  const CSS_TEXT = `
:host { all: initial; }
.kv-card {
  position: fixed; right: 20px; bottom: 20px; z-index: 2147483647;
  width: 300px; box-sizing: border-box; margin: 0; padding: 14px 16px;
  background: #161a23; color: #f2f4fb;
  border: 1px solid #242b3b; border-radius: 12px;
  font: 13px/1.5 "Microsoft YaHei UI", "PingFang SC", "Segoe UI", sans-serif;
  text-align: left;
  box-shadow: 0 8px 28px rgba(0, 0, 0, .35);
}
.kv-title { margin: 0 0 4px; font-weight: 600; font-size: 14px; color: #f2f4fb; }
.kv-meta {
  margin: 0 0 12px; color: #9aa4b8; font-size: 12px; line-height: 1.5;
  overflow-wrap: anywhere; font-family: Consolas, "Cascadia Mono", monospace;
}
.kv-row { display: flex; gap: 8px; }
.kv-card button {
  flex: 1; margin: 0; padding: 7px 0; border-radius: 8px;
  border: 1px solid #242b3b; font: inherit; font-size: 12.5px;
  cursor: pointer;
}
.kv-primary { background: #3d5fe8; border-color: #2743b8; color: #ffffff; }
.kv-primary:hover { background: #2f4fd6; }
.kv-ghost { background: transparent; color: #9aa4b8; }
.kv-ghost:hover { color: #d4dcec; }
.kv-note { margin: 2px 0; color: #34d399; font-size: 12.5px; overflow-wrap: anywhere; }
.kv-note.kv-error { color: #f87171; }
`;

  /**
   * 提示条控件。doc 只用到 createElement/documentElement/title 一类小表面，
   * node 测试用桩替换；timers 注入让 3.2s 自灭可测。
   *
   * @returns {{show, showResult, hide}}
   */
  function createBar(doc, timers) {
    let host = null;
    let card = null;
    let decided = false;
    let hideTimer = null;
    let epoch = 0; // 弹条代次：store 回复凭它认条，迟到的回复不得覆盖新条

    function removeHost() {
      if (hideTimer !== null) {
        timers.clearTimeout(hideTimer);
        hideTimer = null;
      }
      if (host && host.parentNode) host.parentNode.removeChild(host);
      host = null;
      card = null;
    }

    function scheduleHide(delayMs) {
      if (hideTimer !== null) timers.clearTimeout(hideTimer);
      hideTimer = timers.setTimeout(removeHost, delayMs);
    }

    /** 弹出候选提示条；已有提示条先收掉，同一时刻至多一条。 */
    function show(candidate, actions) {
      removeHost();
      decided = false;
      epoch += 1;
      const session = epoch; // 本次弹条的会话代号，[存入] 的回复凭它认条
      host = doc.createElement("div");
      host.id = HOST_ID;
      // closed：页面拿不到 shadowRoot 引用——读不到 meta 行，也摸不到按钮。
      const shadow = host.attachShadow({ mode: "closed" });

      const style = doc.createElement("style");
      style.textContent = CSS_TEXT;
      shadow.appendChild(style);

      card = doc.createElement("div");
      card.className = "kv-card";

      const title = doc.createElement("div");
      title.className = "kv-title";
      title.textContent = "检测到凭据";

      const meta = doc.createElement("div");
      meta.className = "kv-meta";
      meta.textContent = metaLine(candidate);

      const row = doc.createElement("div");
      row.className = "kv-row";
      const save = doc.createElement("button");
      save.className = "kv-primary";
      save.textContent = "存入 KeyVault";
      const dismiss = doc.createElement("button");
      dismiss.className = "kv-ghost";
      dismiss.textContent = "忽略";
      row.appendChild(save);
      row.appendChild(dismiss);

      card.appendChild(title);
      card.appendChild(meta);
      card.appendChild(row);
      shadow.appendChild(card);
      doc.documentElement.appendChild(host);

      // 与 copy 路径同一信任纪律：isTrusted=false 的合成点击（页面 .click()
      // 代点）无效且不消耗判定；一次性判定防双击双重存入。
      save.addEventListener("click", (event) => {
        if (event && event.isTrusted === false) return;
        if (decided) return;
        decided = true;
        actions.onStore(session);
      });
      dismiss.addEventListener("click", (event) => {
        if (event && event.isTrusted === false) return;
        if (decided) return;
        decided = true;
        actions.onDismiss();
      });
    }

    /**
     * 用结果文案替换卡片内容，3.2s 后自灭。sessionEpoch 与当前弹条不符即
     * 迟到回复（用户已复制了新候选、条已换代）：静默丢弃，绝不覆盖新条。
     * isError 时文案转红。
     */
    function showResult(message, isError, sessionEpoch) {
      if (!card || sessionEpoch !== epoch) return;
      while (card.firstChild) card.removeChild(card.firstChild);
      const note = doc.createElement("div");
      note.className = isError ? "kv-note kv-error" : "kv-note";
      note.textContent = String(message);
      card.appendChild(note);
      scheduleHide(RESULT_TTL_MS);
    }

    return { show, showResult, hide: removeHost };
  }

  // ---------------------------------------------------------- 捕获控制器

  /**
   * 事件驱动的捕获控制器——本模块的核心。env 注入全部外部世界：
   * - runtime.send(message, onReply)：chrome.runtime.sendMessage 的接缝，
   *   onReply 收 {response, error}（error 非 null 即 lastError/通道失败）；
   * - document / location：页面环境；
   * - timers：可选，缺省用全局 setTimeout/clearTimeout。
   */
  function createCapture(env) {
    const doc = env.document;
    const timers = env.timers || {
      setTimeout: (fn, ms) => setTimeout(fn, ms),
      clearTimeout: (id) => clearTimeout(id),
    };
    const bar = createBar(doc, timers);
    const ring = []; // 内容指纹环：只存指纹，绝不存原文（watch.py:13-15 同一纪律）

    function pageUrl() {
      try {
        return String((env.location && env.location.href) || "");
      } catch (_) {
        return "";
      }
    }

    function pageTitle() {
      try {
        return String(doc.title || "");
      } catch (_) {
        return "";
      }
    }

    /**
     * 把指纹退出环。capture 未能真正检测（通道失败/SW 内部错误/捕获开关关）
     * 时必须回收：桌面 watch「进环即已检」的前提是检测本地同步必成，浏览器
     * 侧 sendMessage 可能失败，不回收会让同文本重复制永不重试。ignored 是
     * 权威落点（用户明确忽略该主机），照旧留在环里。
     */
    function releaseRing(digest) {
      const index = ring.indexOf(digest);
      if (index >= 0) ring.splice(index, 1);
    }

    /** copy/cut 统一入口：先无条件报 clipboard-changed，再走送检闸门。 */
    function handleCopy(event) {
      try {
        if (event && event.isTrusted === false) return; // 页面伪造的合成事件
        // ① 无条件通知：即令文本太短/重复/非文本，用户确确实动过剪切板，
        //    在飞的 30s 擦除必须让位（wipe.py 的「不覆盖新内容」性质）。
        env.runtime.send({ kind: MSG_CLIPBOARD_CHANGED }, () => {});

        // ② 长度闸门
        const text = extractText(event);
        if (text.length < MIN_CHARS || text.length > MAX_CHARS) return;

        // ③ 去重环：进环即视为已检过，同文本短时间内不再送检（watch.py:139-142）
        const digest = fingerprint(text);
        if (ring.includes(digest)) return;
        ring.push(digest);
        if (ring.length > RING_SIZE) ring.shift();

        // ④ 送检。恰一条 promptable 与否由 SW 裁决（C1#5），这里只认 candidate。
        env.runtime.send(
          {
            kind: MSG_CAPTURE,
            text,
            source_url: pageUrl(),
            title: pageTitle(),
          },
          (reply) => handleCaptureReply(reply, digest),
        );
      } catch (_) {
        /* 就地吞掉：扩展绝不能弄坏网页 */
      }
    }

    function handleCaptureReply(reply, digest) {
      if (!reply || reply.error || !reply.response) {
        releaseRing(digest); // 通道失败/lastError：回收指纹，重复制可重试
        return;
      }
      const response = reply.response;
      if (response.error || response.disabled) {
        releaseRing(digest); // SW 没能检测（内部错误/开关关）：回收，允许重试
        return;
      }
      if (response.ignored) return; // 主机被明确忽略：权威落点，不重发
      if (!response.candidate) return; // 0 条或 ≥2 条 promptable：SW 已裁决，安静
      const text = response.value; // C1#5 原文随行，[存入] 原样重发
      bar.show(response.candidate, {
        onStore: (session) => requestStore(text, session),
        onDismiss: () => bar.hide(),
      });
    }

    function requestStore(text, session) {
      env.runtime.send(
        {
          kind: MSG_STORE,
          text,
          source_url: pageUrl(),
          title: pageTitle(),
        },
        (reply) => handleStoreReply(reply, session),
      );
    }

    /** C1#6：{ok, outcome:"created"|"deduped", name, warning} | {error} */
    function handleStoreReply(reply, session) {
      if (!reply || reply.error || !reply.response) {
        bar.showResult("存入失败：扩展无响应", true, session);
        return;
      }
      const response = reply.response;
      if (response.error) {
        bar.showResult(`存入失败：${response.error}`, true, session);
        return;
      }
      const name = String(response.name || "");
      const suffix = response.warning ? `（${response.warning}）` : "";
      if (response.outcome === "created") {
        bar.showResult(`已存入：${name}${suffix}`, false, session);
      } else if (response.outcome === "deduped") {
        bar.showResult(`已在库里（去重）：${name}${suffix}`, false, session);
      } else {
        bar.showResult("存入失败：扩展返回了未知结果", true, session);
      }
    }

    doc.addEventListener("copy", handleCopy, true);
    doc.addEventListener("cut", handleCopy, true);

    return {
      ring,
      /** 解除本实例对文档的监听（测试与清理用）。 */
      dispose() {
        doc.removeEventListener("copy", handleCopy, true);
        doc.removeEventListener("cut", handleCopy, true);
      },
    };
  }

  // ------------------------------------------------------------ 安装

  /**
   * DOM 层残迹清理：扩展 reload/更新会向已开标签页重新注入本脚本，旧孤儿
   * 实例的提示条（light DOM host）不被 Chrome 清理，会与 newcomer 叠影。
   * 新实例一律接管功能——孤儿的 chrome.runtime 已失效，sendMessage 只会
   * 抛「context invalidated」并被吞，靠它守界面只会让捕获失灵——接管前
   * 先清掉孤儿残留。孤儿实例的 copy 监听无法从外部摘除，但其消息发送
   * 全部就地吞掉，属惰性空转，无正确性影响。
   */
  function clearStaleBar(doc) {
    const root = doc.documentElement;
    if (!root) return;
    for (const child of [...root.children]) {
      if (child.id === HOST_ID && child.parentNode) child.parentNode.removeChild(child);
    }
  }

  /** 装配（installBrowser 的可测主体）：清孤儿残留 → 挂监听。 */
  function setupCapture(env) {
    clearStaleBar(env.document);
    return createCapture(env);
  }

  /**
   * 真实浏览器环境装配。lastError 的纪律在这里兑现：回调里**总是**先取
   * chrome.runtime.lastError（无论用不用），否则 Chrome 会往页面控制台喷
   * 「Unchecked runtime.lastError」。
   */
  function installBrowser() {
    const runtime = {
      send(message, onReply) {
        try {
          chrome.runtime.sendMessage(message, (response) => {
            const lastError = chrome.runtime.lastError; // 必须先读：防噪音
            if (lastError) {
              onReply({ response: null, error: String(lastError.message || "extension error") });
              return;
            }
            onReply({ response: response || null, error: null });
          });
        } catch (_) {
          /* 扩展重载/上下文失效：就地吞掉 */
        }
      },
    };
    setupCapture({
      runtime,
      document,
      location: typeof location !== "undefined" ? location : { href: "" },
    });
  }

  // 环境判定：真内容脚本（isolated world 有 chrome.runtime.id）→ 自动安装；
  // node 测试环境 → 把 API 挂到 globalThis 供 tests/app-content.test.js 取用。
  const IS_CONTENT_SCRIPT =
    typeof chrome !== "undefined" && !!chrome && !!chrome.runtime &&
    !!chrome.runtime.id && typeof document !== "undefined" && !!document;

  const KeyVaultContent = {
    MIN_CHARS,
    MAX_CHARS,
    RING_SIZE,
    RESULT_TTL_MS,
    HOST_ID,
    extractText,
    fingerprint,
    platformLabel,
    metaLine,
    createBar,
    createCapture,
    clearStaleBar,
    setupCapture,
    installBrowser,
  };

  let installed = false;
  if (IS_CONTENT_SCRIPT) {
    try {
      installBrowser();
      installed = true;
    } catch (_) {
      /* 安装失败也不外抛 */
    }
  }
  if (!installed) {
    const root = typeof globalThis !== "undefined" ? globalThis : this;
    root.KeyVaultContent = KeyVaultContent;
  }
})();
