# Browser_extension/ —— 浏览器扩展（Chrome，MV3，独立浏览器版）

> 用途：KeyVault 的**独立浏览器版**——本地密钥保管箱整个跑在浏览器里，不需要
> 桌面软件、不需要 Python、不需要宿主进程：网页复制的密钥页内检测、一键存入
> 浏览器内加密 vault，管理/TOTP/泄露扫描/备份恢复全功能。零 npm 依赖、零遥测，
> 数据不出本机。
> 连接本机桌面 vault 的**桌面伴侣版**在 [`extension/`](../extension/)——两版
> 定位不同、并存；各自 vault 完全独立。

## 定位

- 给不方便装桌面软件、但需要「网页上撞见密钥就收进保险箱」的场景。
- 检测大脑（规则/解析/判定）是桌面 `kv/detect` + `kv/parse` 的 JS 移植：
  24 条规则表与桌面**逐字节同源**（`tableHash()` 对拍），桌面金标语料 92 条
  由 `tests/golden.test.js` 直读 `tests/corpus/` 正本逐条对拍——语料单源无副本。
- 版本单源 `manifest.json`（`package.json` 仅镜像，`make_release.py` 校验一致）。

## 文件清单

运行时 29 个文件在 [`make_release.py`](make_release.py) 显式列出，缺一即拒绝
打包；清单外全是开发期文件。

| 文件 | 干什么 |
|---|---|
| `manifest.json` | MV3 清单：权限恰为 storage/alarms/clipboardWrite/offscreen（绝不加 clipboardRead），`minimum_chrome_version` 116 |
| `background.js` | MV3 service worker，**只做编排**（无一行检测/加密/存储逻辑） |
| `content.js` | 内容脚本：copy/cut 事件驱动页内捕获（无 clipboardRead） |
| `popup.html` / `popup.css` / `popup.js` | 工具栏弹窗：三态门（设主密码 / 解锁 / 搜索取用） |
| `options.html` / `options.css` / `options.js` | 管理页（整页打开）：八区全功能 |
| `offscreen.html` / `offscreen.js` | offscreen 文档：30 秒 TTL 剪贴板擦除 |
| `icons/icon.png` | 扩展图标（与 `packaging/icon.png` 同源整包复用） |
| `make_release.py` | 零依赖打包脚本（stdlib only，见下「门禁」） |
| `package.json` | `npm test` 入口（`node --test`）；零依赖、版本镜像 |
| `CWS.md` / `PRIVACY-POLICY.md` | 商店提审材料定稿：逐权限理由（含 `<all_urls>`）、Privacy 面板逐项、版本发布说明 / 隐私政策发布原文 |
| `src/` `tests/` | 源码与测试（见下） |
| `dist/` | 打包产物（zip），不入库 |

### src/core —— 检测大脑（8 文件，桌面 kv/detect + kv/parse 的移植）

| 文件 | 负责 |
|---|---|
| `rules.js` | 24 条平台规则表（纯数据）：与桌面逐字节同源，`tableHash()` 对拍；歧义组（裸 sk- 共用形态） |
| `placeholders.js` | 占位符/非机密闸门：18 条占位符模式（与桌面 `kv/parse/placeholders.py` 同源）+ 哈希形态/锁文件白名单，连接串豁免先于 URL 规则 |
| `candidate.js` | 候选构造与主机名/键名提取 |
| `masking.js` | 掩码样式（`[keepHead, keepTail]` + `*` 预览）、canonicalize、kind 枚举（token/json/pem/pair/connstring） |
| `parse.js` | 六解析器（curl / jsonblob / awscsv / dotenv / kvline / bare）+ `genericScan` 兜底（全部落空才跑） |
| `resolve.js` | 八级判定（0 用户纠正 → 7 无信号）：歧义候选拒绝猜测，绝不猜厂商 |
| `corrections.js` | 用户纠正规则五类（存后可纠正平台） |
| `sha256.js` | 同步 sha256（仅检测去重用，与 node:crypto 对拍过） |

### src/vault —— 加密存储层（7 文件）

| 文件 | 负责 |
|---|---|
| `crypto.js` | WebCrypto：PBKDF2-SHA256 600 000 轮派生 AES-GCM-256；Session（锁定 = 删密钥） |
| `db.js` | IndexedDB 后端（单连接、库名 keyvault、DB_VERSION 1） |
| `memory-backend.js` | 同一接口的内存后端（node 测试替身；node 无 indexedDB，真机行为靠浏览器走查） |
| `store.js` | vault 门面：指纹去重、rotate 旧指纹保留、审计哈希链、指纹历史、KVBE1 口令备份恢复、批量导入、过期标记、统计 |
| `scan.js` | 泄露扫描引擎（桌面 `kv/ops/scan.py` 等价）：逐行子串匹配、分批协议无跨批状态 |
| `detect.js` | `detectText()`——检测组合**唯一**入口（capture/store/import 一律经此） |
| `totp.js` | RFC 6238（与桌面 `kv/ops/totp.py` 实跑值对拍） |

记录字段全 camelCase；审计链公式与桌面不同（两侧审计本不互通，自洽即可）。

## 子目录

| 子目录 | 负责 |
|---|---|
| `icons/` | 扩展图标位图（manifest 按 16/48/128 引用同一文件，浏览器自缩放） |
| `src/` | 源码：`core/`（检测大脑）+ `vault/`（加密存储），逐文件见上表 |
| `tests/` | node:test 测试 16 文件（含金标对拍 `golden.test.js`） |

`dist/` 是打包产物目录，不入库。

## 功能表

| 能力 | 在哪 | 说明 |
|---|---|---|
| 页内捕获 | `content.js` → `background.js` | copy/cut 事件（`isTrusted` 防伪造）→ 8–200 000 字符送检 → 恰一条可信候选弹 Shadow DOM 提示条（20 条内容指纹去重环）；存入走重新检测去重 |
| 捕获调度 | `background.js` | 捕获开关、忽略站点；候选绝不驻留 SW 内存（SW 无状态，入库前拿原文重新检测） |
| 搜索取用 | `popup.js` | 150ms 防抖搜索 → 掩码列表 → 点击复制；复制即安排 30 秒擦除；「生成密码…」快捷面板（生成 → 复制走擦除通道 / 存入库走 store-direct） |
| 密码生成器 | `src/vault/generator.js` | `crypto.getRandomValues` + 拒绝采样（无模偏差）；长度 8–128 默认 20、四字符类开关、排除易混淆；每类至少一枚 + 熵粗估；上次选择存 settings.generator（C7 第四字段）；popup 与管理页双入口，存入必须显式平台（随机串检测器不认识，manual 语义） |
| 管理页 | `options.js`（八区） | ①仪表盘（统计与平台分布）②密钥表格（含密码生成器卡）③详情抽屉（TOTP 活码环、指纹历史、轮换/吊销/重命名/设平台/纠正规则）④审计日志（整链复算）⑤批量导入（两步向导：解析预览 → 勾选确认，逐条 store-direct，不可信候选进 skipped 不落盘）⑥KVBE1 备份导出恢复 ⑦设置（自动锁定封顶 240 分钟、捕获开关、忽略站点、生成器默认、整库清除）⑧泄露扫描 |
| 导入向导 | `src/vault/importers.js` | Bitwarden CSV / JSON 导出、otpauth:// 列表、.env 文本四种格式（自动识别）；login_totp 列展开为独立 TOTP 条目（`totp.js` 的 otpauth URI 解析）；CSV/JSON 行平台建议取 login_uri 主机名；skipped 只带掩码预览与原因不含值（C1#20） |
| 泄露扫描 | `options.js` + `scan.js` | 粘贴 / 文件 / 目录三种输入；行界切块 + 贪心分批 + 逐批发送，失败逐条明示；吊销的旧值同样参与匹配 |
| 剪贴板擦除 | `background.js` + `offscreen.js` | 复制后 30 秒 TTL 到点覆盖剪贴板；页面再次复制即取消（clipboard-changed）；擦除失败宁可不擦不错擦，启动 sweep 补擦 |
| 两步验证码 | `src/vault/totp.js` | 详情抽屉「显示验证码」显式 reveal，活码每秒重绘、剩 5s 转琥珀 |
| 自动锁定 | `background.js` | 每分钟 alarm 检查最近一次扩展 UI 交互（KVX-1：页面复制不续命），超时锁定；解锁时补过期标记 sweep |

## 门禁

```bash
cd Browser_extension
npm test          # node --test，零依赖
```

- 2026-11-12 实测（功能批后）：**19 个测试文件 564 条全绿**（tests 564 / pass 564 /
  fail 0 / skipped 0，suites 88）。沿革：11-09 批 16 文件 504 条 → KVX-1 批
  background-idle +10 → 本批 generator +13、importers +19、otpauth 向量 +4、
  app-background/options/popup 补测 +14。
- `node --check` 全部 41 个 JS（顶层 5 + src 17 + tests 19）语法通过。
- 金标：`tests/golden.test.js` 直读 `tests/corpus/` 桌面正本 92 条逐条对拍——
  桌面语料任何改动，浏览器侧测试立刻同响。
- 机械红线有测试钉死：全部 `textContent` 无 `innerHTML`（app-content /
  app-options / app-popup）、禁词表（app-background、app-popup）、
  `package.json` 零依赖（app-package）。
- 打包：`python -I Browser_extension/make_release.py`（仓根执行）——29 文件
  清单缺失即拒绝、版本单源校验（manifest ↔ package.json）、zip 条目回读校验。

主仓 Python 门禁（`python -I -m unittest discover -s tests -t .`）不受本目录影响：
本目录没有 Python，`golden.test.js` 只是读语料正本。

## 真机走查（待办）

node 测试覆盖不到浏览器运行时（IndexedDB、chrome.*、offscreen、Shadow DOM），
加载后手动过一遍——**尚未执行**，步骤：

1. `chrome://extensions` 开发者模式 →「加载已解压的扩展程序」选本目录。
2. 工具栏点开 popup → 设主密码（≥8 位）→ 确认三态门切到搜索态。
3. 打开 options（扩展详情 → 扩展程序选项）：仪表盘统计与平台分布应显示零起点。
4. 在任一网页复制一段含密钥的文本（如 `sk-` 开头假串）→ 页内应弹提示条 →
   「存入」→ popup 搜索可见；再复制同文本不重复弹条（去重环）。
5. 详情抽屉：设平台、看指纹历史、TOTP（值存 base32 种子）出活码、轮换后旧值进指纹历史。
6. 复制一个密钥 → 等 30 秒 → 手动粘贴确认剪贴板已被覆盖；期间在网页再复制
   任意内容 → 到点不擦（clipboard-changed 取消）。
7. 设置区：改自动锁定分钟数 → 等待锁定 → 解锁；忽略站点列表生效（该站复制不弹条）。
8. 备份导出 → 清库（主密码 + RESET 双门）→ 恢复 → 数据与审计链完整。
9. 泄露扫描：粘贴含已存密钥的文本 → 命中报告；拖一个含假密钥的目录扫描。
10. 批量导入一段混合文本 → 确认可信候选入库、低置信进 skipped 明示。

## 加密边界与设计红线

- **加密**：PBKDF2-SHA256 600 000 轮（盐 16B）派生 AES-GCM-256；派生密钥只存
  `chrome.storage.session`（浏览器会话结束即清空，锁定 = 删除）；vault 全库
  IndexedDB，锁定态整库冻结（写操作先 `assertUnlocked`）。
- **备份**：KVBE1 = `KVBE1|` + base64(salt|iv|ct)，口令独立于主密码派生、
  是唯一防线；恢复走正常入库（去重 + 审计链复用），不重新检测。
- **明文边界**：明文只在解密瞬间存在于内存；不进日志、不进 `storage.session`
  （那里只有 raw key 的 b64、活动时间戳与擦除元数据）；候选绝不驻留 SW 内存。
- **消息协议**：SW 消息面恰好 30 个动作，`background.js` 的 `HANDLERS` 表即
  全表（`tests/app-background.test.js` 以 `Object.keys(HANDLERS)` 与
  `ALL_KINDS` 逐项对拍钉死），增删动作必须同步该表；响应二元——成功
  `{ok:true,…}`、失败 `{error:"…"}`，原始异常绝不逃出消息通道（vault 锁定
  统一映射 `{error, locked:true}`）；sender 分级是纵深防御：`capture`/
  `store`/`clipboard-changed` 只接受内容脚本，其余动作只接受扩展页。
- **自动锁续命口径（KVX-1）**：`kv-last-active` 只认扩展 UI 交互——扩展页
  与 SW 消息续命；内容脚本页面事件（`clipboard-changed`/`capture`）一律
  不续命（用户在任意网页按 Ctrl+C 不许把自动锁计时续回去），唯一例外是
  捕获条「存入」（`store`——用户对 vault 的显式拍板）；空闲判定是时钟可
  注入的纯函数 `isIdleExpired`。页面活动 ≠ 库内交互，双向口径由
  `tests/background-idle.test.js` 钉死。
- **剪贴板诚实声明**：浏览器的 `navigator.clipboard` 没有「排除系统剪贴板
  历史」的 API（桌面 `kv copy` 的 exclude_history 无等价物）——popup 文案
  明示「无法排除系统剪贴板历史」，不宣称等同桌面安全级。
- **权限最小化**：不加 `clipboardRead`（copy/cut 事件里拿到的是用户主动复制
  的内容）；`<all_urls>` 是捕获所必需。
- **DOM 纪律**：页面与管理页一律 `textContent`，绝不 `innerHTML`（用户数据
  转义面）；页内提示条挂在 Shadow DOM。
- **检测纪律**：`detectText()` 是唯一组合入口；歧义候选不猜厂商——unknown
  拒绝落盘，ambiguous 显示「厂商未定」、存后可纠正。
- **存储层隔离**：`db.js`（IndexedDB 真身）与 `memory-backend.js`（node 测试
  替身）实现同一接口，`store.js` 之上的代码不感知后端差异（存储后端注入）；
  浏览器侧持久层除全局 `indexedDB` 外不触碰任何 `chrome.*`；store 形状变更
  必须递增 `DB_VERSION` 并在 `onupgradeneeded` 写迁移。
- **版本纪律**：版本单源 `manifest.json`，`package.json` 仅镜像；扩展版本
  独立推进，桌面端版本按其单源 `kv/__init__.py` 在桌面发布时同步（发版指南
  见 [PUBLISHING.md](PUBLISHING.md)）。

## 和谁打交道

- **上游单源（只读）**：`tests/corpus/` 的桌面金标语料正本——`golden.test.js`
  直读对拍，桌面语料改动本侧立刻同响；规则表以桌面 `kv/detect/rules.py` 为
  正本（`tableHash()` 对拍），本侧不许单方面改规则。
- **浏览器运行时**：Chrome 116+ 的 MV3 API（service worker / IndexedDB /
  offscreen / alarms / storage.session）；不连任何宿主进程、不连桌面 vault。
- **改这里之后要跑**：`npm test`（本目录）+ 41 个 JS `node --check` +
  `python -I Browser_extension/make_release.py`（打发布包）；主仓 Python
  门禁不受影响；发布前按上文「真机走查」节在浏览器里手动过一遍。
- **下游发布链**：[`PUBLISHING.md`](PUBLISHING.md)（渠道与步骤）、
  [`CWS.md`](CWS.md)（提审材料定稿，权限面变了先改它）、
  [`PRIVACY-POLICY.md`](PRIVACY-POLICY.md)（隐私政策发布原文）。

## 别动

- `manifest.json` 的 `permissions` 恰为 storage/alarms/clipboardWrite/offscreen
  四项——**绝不加 `clipboardRead`**（copy/cut 事件已足够，只写不读是隐私边界）；
  加任何权限都要先说清理由。
- `src/core/rules.js` 的规则表与桌面逐字节同源：改规则先改桌面正本并重对
  `tableHash()`，不许只动 JS 侧（否则金标对拍立刻红，那是对的）。
- 一切 DOM 输出保持 `textContent`，不要换成 `innerHTML`（用户数据转义面，
  有测试钉死）；页内提示条留在 Shadow DOM。
- `background.js` 不写检测/加密/存储逻辑，检测一律走 `detect.js` 的
  `detectText()` 唯一入口——另起炉灶组装 parse/resolve 是首版翻过的车。
- SW 无状态是刻意的（MV3 随时被回收）：候选不驻留内存、入库走原文重新检测，
  别加「缓存候选」类的优化。
- 擦除失败「宁可不擦不错擦」的语义别改；`COPY_NOTICE` 的「无法排除系统剪贴板
  历史」声明不得删（诚实红线）。
- `package.json` 保持零依赖（有测试钉死）；`make_release.py` 的 29 文件清单
  是发布物语义，src 增删文件必须同步清单再打包。
