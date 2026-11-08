/**
 * KeyVault offscreen 文档脚本：只处理剪贴板擦除。
 *
 * 这是桌面版「copy 后 30 秒擦除剪贴板」（kv/ops/wipe.py）在浏览器端的等价物：
 * TTL 到期时 background 发来 {target:"offscreen", kind:"wipe-clipboard"}，
 * 本页用 clipboardWrite 权限（无用户手势）把剪贴板覆盖为固定提示文案。
 *
 * 协议（CONTRACTS.md C1/C7）：响应 {ok:true}；失败返回 {error:string}——
 * 擦不成就如实上报，宁可不擦也不错擦。其余消息一律不响应：
 * target 不是 "offscreen" 的消息属于别的上下文，kind 不是 wipe-clipboard 的
 * offscreen 消息本页不支持——静默忽略，绝不替别的处理器抢答。
 */
const WIPE_TEXT = "KeyVault: clipboard cleared";

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.target !== "offscreen" || msg?.kind !== "wipe-clipboard") {
    return false; // 非本页消息或未支持的动作：不响应
  }
  navigator.clipboard.writeText(WIPE_TEXT).then(
    () => sendResponse({ ok: true }),
    (error) => sendResponse({
      error: `剪贴板擦除失败：${error instanceof Error ? error.message : String(error)}`,
    }),
  );
  return true; // 响应异步送达，保持消息通道开启
});
