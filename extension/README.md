# extension/ —— 浏览器扩展（Chrome/Edge，MV3）

> 用途：KeyVault 的浏览器伴侣（**桌面伴侣版**）。网页里复制的密钥由它捕获、
> 经本机宿主检测后入库/审批；popup 提供搜索与一键复制。加密、存储、审计全部在
> 本机 vault（Python 宿主），本目录没有任何密钥处理逻辑。
> 独立不依赖桌面的浏览器版在 [`Browser_extension/`](../Browser_extension/)。

## 文件清单

| 文件 | 干什么 |
|---|---|
| `manifest.json` | MV3 清单：nativeMessaging 权限 + `<all_urls>` 内容脚本 + popup |
| `content.js` | 内容脚本：监听 copy/cut 事件 → 文本发宿主检测；高置信候选弹出页内提示条（Shadow DOM） |
| `background.js` | service worker：content/popup 请求原样转发给宿主，零业务逻辑 |
| `host-client.js` | 宿主连接客户端：每次操作一条 connectNative 短连接，失败折成 `{error}` |
| `popup.html` / `popup.css` / `popup.js` | 工具栏弹窗：搜索已存密钥 → 点击复制（30s 自动擦除） |
| `icons/icon.png` | 扩展图标（与 `packaging/icon.png` 同源，整包复用） |

## 子目录

| 子目录 | 负责 |
|---|---|
| `icons/` | 扩展图标位图（manifest 按 16/48/128 引用同一文件，浏览器自缩放） |

## 和谁打交道

- **本机宿主**：`kv_host.py` / `dist/kv-host.exe`——连接名
  `com.keyvault.host`，注册用 `kv host install`（见 `kv/host/`）
- **上游加载**：chrome://extensions 开发者模式「加载解压缩的扩展」选本目录
- **改这里之后要跑**：`python -I -m unittest discover -s tests -t .`
  （宿主协议面在 `tests/test_host_*.py`；本目录 JS 无 Python 门禁，改动后
  在浏览器里重新加载扩展手工验证）

## 别动

- `manifest.json` 的 `permissions` 只保留 `nativeMessaging` 与内容脚本所需——
  加权限要同步说清理由（最小权限是安全工具的底线）。
- `content.js` 对页面的一切输出都挂在 Shadow DOM 里、全部 `textContent`，
  不要换成 innerHTML 或插入页面主 DOM（用户数据转义面）。
- `host-client.js` 的短连接模式是刻意的：MV3 service worker 会被浏览器
  回收，长连接保活不可靠；换长连接前先想清楚 SW 生命周期。
- 版本号与 `kv/__init__.py` 同步升（发布时一起改）。
