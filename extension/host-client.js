// 本机宿主客户端：每次操作一条原生消息连接（connectNative → 请求 → 响应 → 断开）。
// 短连接而非长连接：MV3 service worker 会被浏览器回收，长连接保活不可靠；
// 每次拉起宿主进程的开销（~百毫秒级）对复制/点击类操作可接受。
// 所有失败都折成 {error} 返回——调用方只管展示，不用管连接细节。

const HOST_NAME = "com.keyvault.host";
const TIMEOUT_MS = 10000;

export function callHost(payload) {
  return new Promise((resolve) => {
    let port;
    try {
      port = chrome.runtime.connectNative(HOST_NAME);
    } catch (err) {
      resolve({ error: "无法启动本机 KeyVault 宿主（先在桌面端跑 kv host install）：" + err.message });
      return;
    }
    let settled = false;
    const timer = setTimeout(() => finish({ error: "宿主响应超时（10s）" }), TIMEOUT_MS);
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { port.disconnect(); } catch (_) { /* 已断开 */ }
      resolve(result);
    };
    port.onMessage.addListener((response) => finish(response || { error: "宿主返回空响应" }));
    port.onDisconnect.addListener(() => {
      if (settled) return;
      const reason = chrome.runtime.lastError ? chrome.runtime.lastError.message : "宿主提前退出";
      finish({ error: "宿主连接断开：" + reason });
    });
    try {
      port.postMessage(payload);
    } catch (err) {
      finish({ error: "发送失败：" + err.message });
    }
  });
}
