// offscreen 页：TTL 到期后的剪贴板覆盖（桌面版 30 秒自动擦除的浏览器等价）。
// clipboardWrite 权限允许无手势写入。页面按需创建、擦完即关（由 SW 负责）。

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.target !== "offscreen" || msg.kind !== "wipe-clipboard") return false;
  navigator.clipboard.writeText("KeyVault: clipboard cleared")
    .then(() => sendResponse({ ok: true }))
    .catch((error) => sendResponse({ error: String(error) }));
  return true;
});
