// KeyVault 扩展 service worker（MV3）。
// 职责只有一件：把 content script / popup 的请求原样转发给本机宿主——
// 检测、加密、入库、擦除全部在宿主进程与 vault 里，这里零业务逻辑。

import { callHost } from "./host-client.js";

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || typeof msg.kind !== "string") return false;
  const { kind, ...payload } = msg;
  callHost({ type: kind, ...payload }).then(sendResponse);
  return true; // sendResponse 异步送达
});
