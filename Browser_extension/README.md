# Browser_extension/ —— KeyVault 独立浏览器插件版（Chrome/Edge，MV3）

> 用途：**不依赖桌面软件**的完整浏览器内保管箱。网页复制的 key 自动检测、像保存
> 密码一样确认入库；主密码 + WebCrypto 加密，数据只存本机浏览器配置文件。
> 与 `extension/`（桌面伴侣版，连 Python 宿主）定位不同、互不依赖，二者共存。

## 文件清单

| 文件 | 干什么 |
|---|---|
| `manifest.json` | MV3 清单：clipboardWrite/alarms/offscreen/storage + `<all_urls>` 内容脚本 + popup + options |
| `background.js` | service worker：消息面（status/setup/unlock/lock/capture/store/list/get/reveal/totp/copy 流/rotate/revoke/delete/rename/set-platform/corrections/import-text/stats/audit/备份/设置）+ 检测引擎调度 + 自动锁定 + TTL 擦除 |
| `content.js` | 内容脚本：copy/cut 捕获 → SW 检测 → 高置信候选弹页内提示条（Shadow DOM）；每次复制顺带发「剪贴板已换新」取消在飞擦除 |
| `popup.html/css/js` | 工具栏弹窗三态：设主密码 → 解锁 → 搜索 + 一键复制（30 秒自动擦除） |
| `options.html/css/js` | 全功能管理页：仪表盘、密钥表格、详情抽屉（TOTP 活码/轮换/吊销/重命名/设平台）、审计日志、批量导入、加密备份导出/恢复、设置（自动锁定/捕获开关/忽略站点/清库） |
| `offscreen.html/js` | offscreen 页：TTL 到期覆盖剪贴板（clipboardWrite，无手势） |
| `src/core/*` | 检测大脑（纯逻辑，node 可测）：规则表 24 条、占位符闸门、六解析器、七级判定、掩码、TOTP、纠正规则、同步 sha256 |
| `src/vault/*` | vault 层：PBKDF2+AES-GCM 加密、IndexedDB 后端（db.js）与内存后端（memory-backend.js，同一接口）、SecretRepo（去重/轮换/审计链/备份 KVBE1） |
| `tests/*.test.js` | node:test 单测 61 条（含 RFC 6238 官方向量、sha256 与 node:crypto 对拍） |
| `icons/icon.png` | 图标（与 packaging/icon.png 同源） |

## 子目录

| 子目录 | 负责 |
|---|---|
| `src/core/` | 检测大脑纯逻辑（规则/解析/判定/掩码/TOTP/纠正），node 可直接测 |
| `src/vault/` | 加密、存储后端（IndexedDB + 内存替身）、SecretRepo |
| `tests/` | node:test 单测（`npm test`） |
| `icons/` | 图标位图 |

## 和谁打交道

- **宿主环境**：Chrome/Edge（Chromium MV3）；加载方式见下方「真机走查」。
- **桌面版**：完全独立，无 native messaging、无 Python。备份格式 KVBE1 是
  浏览器版自有格式（与桌面 .kvb 同理念、不互通）。
- **改这里之后要跑**：`npm test`（本目录内，node --test）+ 全部 JS `node --check`；
  Python 侧门禁不受本目录影响，但动了一级目录登记要跑
  `python 文档标准/check_docs.py KeyVault`。

## 真机走查（首次安装约 2 分钟）

1. `chrome://extensions` 开发者模式 → 加载解压缩的扩展 → 选本目录；
   同样方式可装进 Edge（edge://extensions）。
2. 点工具栏 KeyVault 图标 → 设置主密码（≥8 位，创建本机保管箱）。
3. 打开任意网页，复制一段真 API key（或在文本框选中复制）→ 页面右下角浮出
   「检测到 API Key」提示条 → 点「存入 KeyVault」→ 状态栏显示记录名。
   低置信/歧义候选不打扰页面（歧义裸 sk- 会以「厂商未定」提示，可存后改）。
4. popup 搜索 → 点击复制 → 30 秒后剪贴板被自动覆盖；期间再复制别的内容则取消擦除。
5. options 页：详情抽屉看 TOTP 活码（记录值是 base32 种子时自动出现）、
   轮换/吊销/重命名；批量导入粘贴 .env；导出加密备份再恢复验证。

## 别动

- **加密边界**：明文值只在 `reveal` 返回值与内存里存在，落库一律经
  `crypto.js` AES-GCM；不要为了调试把明文写进 IndexedDB 或日志。
- **SW 无状态**：候选不驻留 SW 内存（MV3 随时回收）——[存入] 重发原文重新
  检测是设计不是浪费；换长连接前先想清楚 SW 生命周期。
- **检测语义**：`src/core` 是桌面 `kv/detect`+`kv/parse` 的忠实移植，改动
  任何正则/分级顺序必须同步跑 `npm test`，并考虑与桌面端行为漂移。
- `src/vault/db.js` 与 `memory-backend.js` 是同一接口的两个实现——改接口
  两个都要改（tests 只测内存版，IndexedDB 版靠真机走查）。
- 版本号与 `kv/__init__.py` 同步升（发布时一起改）。
