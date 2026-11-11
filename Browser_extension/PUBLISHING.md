# PUBLISHING —— 发布指南（Chrome Web Store / Edge 加载项 / GitHub Release）

> 用途：让别人能在网上下载安装 KeyVault 浏览器扩展。三条渠道的材料与步骤都在
> 这份文件，每次发版照着走。打包命令：`python -I Browser_extension/make_release.py`
> （仓根执行，`-I` 隔离模式）→ 产物 `dist/keyvault-browser-extension-v<版本>.zip`。
> 2026-11-09 随 v2 重写更新：版本纪律、发版检查单、真机走查条数有变，渠道与
> 商店材料基本沿旧。

## 版本纪律（v2 起与桌面端解耦）

- 扩展版本**单源 `manifest.json`**（`package.json` 仅镜像，`make_release.py`
  校验两处一致，不一致拒绝打包）。
- 扩展版本与桌面端（`kv/__init__.py` 单源）**各自独立推进**；桌面端版本在
  桌面发布时按其旧约定统一升档——浏览器扩展发版**不需要**动 `kv/`。
- 29 个运行时文件清单冻结在 `make_release.py`：src 增删文件必须同步清单，
  否则发布物缺文件且检测静默失效——这是打包器硬拦的两种失败之一（另一种
  是版本不一致）。

## 渠道一：Chrome Web Store（主渠道，Chrome + Edge 用户都能装）

### 前置（一次性，需要你本人操作）

1. 注册 [Chrome Web Store 开发者账号](https://chrome.google.com/webstore/devconsole)：
   Google 账号 + **一次性 5 美元注册费**（信用卡）。
2. 准备一个**隐私政策页面**（商店强制要求）：把本文末尾的草稿发布到你的
   GitHub Pages（`Luz7818.github.io` 仓库加一页 `keyvault-privacy.html`），
   得到公开 URL。

### 上传步骤（每次发版）

1. `python -I Browser_extension/make_release.py` 产出 zip。
2. 开发者控制台 → **新增项目** → 上传 zip。
3. 商店信息页：
   - 名称：`KeyVault - 本地密钥保管箱`
   - 简介一句话：`网页复制的 API key 自动检测、像保存密码一样确认入库；主密码加密，数据只存本机浏览器。`
   - 详细描述（复制本文末尾草稿）。
   - 类别：**工具**；语言：中文（简体）+ 英语。
4. **隐私权标签页**（审不过大多栽在这里，逐项照抄）：
   - 单一用途说明：`KeyVault 是一个本地密钥保管箱：检测网页上复制的 API 密钥，
     在用户确认后加密存入浏览器本机数据库（IndexedDB），供用户随后搜索与复制。
     所有数据只保存在用户本机，不向任何服务器发送。`
   - 权限理由（逐条，v2 权限面不变）：
     - `storage`：保存解锁状态与扩展设置（不含密钥）。
     - `alarms`：定时器——无操作自动锁定与剪贴板到期自动擦除。
     - `clipboardWrite`：用户点复制后写入剪贴板；到期后覆盖擦除。
     - `offscreen`：承载剪贴板擦除的页面（MV3 要求）。
     - `host_permissions (<all_urls>)` + 内容脚本：只在用户复制动作发生时读取
       该次复制文本以检测密钥；不读取页面其他内容，不注入任何页面行为。
   - 数据使用声明：勾选「处理用户数据」→ 具体为「用户提供或网站内容产生的信息
     （复制的文本）」与「本地存储的用户活动」；**全部本地处理，不出本机，无远程
     服务器，无遥测**。隐私政策 URL 填你发布的页面。
5. 分发标签页：选公开（或先选「不公开/隐藏」自用测试）。
6. 提交审核。宽权限 + 用户数据类扩展走**深度人工审核**，通常数天到两周；
   被拒按控制台意见补材料重提即可（常见要求：隐私政策可达、权限理由更具体）。

## 渠道二：Microsoft Edge 加载项（免费，建议同批上）

1. [Partner Center](https://partner.microsoft.com/dashboard/microsoftedge) 注册
   （Microsoft 账号，免费）。
2. 创建新扩展 → 上传**同一个 zip**（MV3 通用）→ 隐私政策 URL、商店文案同上。
3. Edge 审核一般比 Chrome 快（数天）。

## 渠道三：GitHub Release（零门槛，源码+zip 都能发）

1. 推送仓库。
2. GitHub 仓库页 → Releases → Draft a new release：
   - tag：`v<扩展版本>`（当前 1.0.0 → `v1.0.0`）；标题写明独立浏览器插件版。
   - 附件：`Browser_extension/dist/keyvault-browser-extension-v<版本>.zip`
     （桌面用户另附 `dist/kv.exe` / `dist/kv-cli.exe` / `dist/kv-host.exe`，
     版本随桌面端发布节奏，可能与扩展版本号不同）。
   - 说明：指向 `Browser_extension/README.md` 的「真机走查」节（十步）。
3. 没装商店的人：下载 zip 解压 → 开发者模式加载解压缩的扩展。

## 发版检查单（每次）

- [ ] `npm test`（Browser_extension 内，`node --test` 零依赖）全绿
- [ ] 全部 36 个 JS `node --check` 通过
- [ ] 主仓门禁不受影响：`python -I -m unittest discover -s tests -t .` +
      `python kv.py selftest --golden`
- [ ] `python -I Browser_extension/make_release.py` 产出新 zip
      （29 文件清单校验 + 版本单源校验 + zip 条目回读校验，三关全过）
- [ ] 真机走查十步过一遍（`Browser_extension/README.md`「真机走查」节）
- [ ] 商店上传新版本号 zip（更新已上架项目，不用新建）
- [ ] 扩展版本改的只是 `manifest.json`（`package.json` 镜像同步）；**不动 `kv/`**
      ——桌面端版本在桌面发布时按旧约定一起升

## 商店详细描述（草稿，直接粘贴）

KeyVault 是一个**本地优先**的密钥保管箱：在网页上复制 API 密钥时自动检测，
像浏览器保存密码一样弹出确认，一键加密存入——之后随时搜索、一键复制（30 秒
自动擦除剪贴板）。

功能：
• 自动捕获：复制 API key 即检测（支持 OpenAI/DeepSeek/GitHub/AWS/Google/
  GitLab/Slack/Vercel/npm/Stripe/Telegram 等 24 类平台形态），可信候选
  弹出确认条，绝不悄悄入库
• 加密保管：主密码（PBKDF2 60 万次派生）+ AES-GCM-256，数据只存本机浏览器，
  不向任何服务器发送；无操作自动锁定（默认 15 分钟，可自定义）
• 快速使用：popup 搜索 → 一键复制 → 30 秒后剪贴板自动擦除（期间复制新内容
  则取消擦除，绝不覆盖你的新东西）
• 两步验证：值是 base32 种子的记录自动出现 TOTP 活码（RFC 6238，倒计时环）
• 批量导入：粘贴 .env / JSON / curl / AWS csv 一次入库
• 泄露扫描：粘贴文本或选文件/目录，用已存指纹在本机逐行比对，命中逐条报告
• 轮换与审计：轮换保留历史指纹；append-only 审计链可查可验
• 加密备份：口令加密的 KVBE1 备份文件导出/恢复

与桌面版（github.com/Luz7818/KeyVault）功能同源、数据各自独立。零外部依赖、
无遥测、无任何网络请求。

## 隐私政策草稿（发布到 GitHub Pages 后把 URL 填进商店）

KeyVault 浏览器扩展隐私政策

KeyVault 不收集、不传输、不出售任何数据。扩展处理的全部信息——你在网页上
复制的文本、检测到的密钥、你输入的主密码派生密钥——只保存在你自己的浏览器
本机存储（IndexedDB 与会话内存）中，不会发送到任何服务器。扩展没有遥测、
没有统计、没有任何网络请求。

- 复制的文本：仅在检测密钥的瞬间被读取，检测完成后不保留副本；你在确认条上
  点「存入」后，该值经主密码派生密钥（AES-GCM-256）加密存入本机 IndexedDB。
- 主密码：不存储明文，仅存储 PBKDF2（60 万次迭代）派生的密钥校验器。
- 剪贴板：写入由你主动点击触发；到期自动擦除在你本机完成。
- 泄露扫描：指纹比对完全在你本机进行，任何文本不离开你的浏览器。
- 卸载扩展或使用扩展内「删除全部数据」即彻底删除本机全部数据。

联系：仓库 github.com/Luz7818/KeyVault 的 Issues。
