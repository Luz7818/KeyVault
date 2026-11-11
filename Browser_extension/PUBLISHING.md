# PUBLISHING —— 发布指南（Chrome Web Store / Edge 加载项 / GitHub Release）

> 用途：让别人能在网上下载安装 KeyVault 浏览器扩展。三条渠道的材料与步骤都在
> 这份文件，每次发版照着走。打包命令：`python -I Browser_extension/make_release.py`
> （仓根执行，`-I` 隔离模式）→ 产物 `dist/keyvault-browser-extension-v<版本>.zip`。
> 2026-11-12 提审材料定稿：权限理由、隐私政策移入单源
> [`CWS.md`](CWS.md) / [`PRIVACY-POLICY.md`](PRIVACY-POLICY.md)，商店描述与
> 功能实况对齐；渠道与步骤沿用 2026-11-09 v2 批的口径。

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
2. 准备一个**隐私政策页面**（商店强制要求）：把定稿原文
   [`PRIVACY-POLICY.md`](PRIVACY-POLICY.md) 整页发布到你的 GitHub Pages
   （`Luz7818.github.io` 仓库加一页 `keyvault-privacy.html`），得到公开 URL。

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
   - 权限理由（逐条）：**定稿见 [`CWS.md`](CWS.md)「逐权限理由」**——每条
     标注代码位置（storage=仅 `chrome.storage.session` 三键；alarms=自动锁定
     +30 秒擦除两个定时器；clipboardWrite=用户复制与无手势的擦除覆盖；
     offscreen=只做擦除的屏外文档；`<all_urls>`=任意网站复制密钥的捕获场景，
     不读页面其他内容、不发起任何网络请求）。CWS.md 是单源，改权限先改那里。
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
   - 说明：粘贴 [`CWS.md`](CWS.md)「版本发布说明」对应版本小节，并注明
     真机走查状态（十步见 `Browser_extension/README.md`「真机走查」节）。
3. 没装商店的人：下载 zip 解压 → 开发者模式加载解压缩的扩展。

## 发版检查单（每次）

- [ ] `npm test`（Browser_extension 内，`node --test` 零依赖）全绿
- [ ] 全部 41 个 JS `node --check` 通过
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
• 密码生成器：拒绝采样的随机密码（长度 8–128、字符类开关、可排除易混淆
  字符），复制走同一条 30 秒擦除通道，一键存入保管箱
• 批量导入向导：Bitwarden CSV / JSON、otpauth:// 验证器导出、.env 文本，
  格式自动识别；两步流程（解析预览 → 勾选确认），不可信条目不落盘
• 泄露扫描：粘贴文本或选文件/目录，用已存指纹在本机逐行比对，命中逐条报告
• 轮换与审计：轮换保留历史指纹；append-only 审计链可查可验
• 加密备份：口令加密的 KVBE1 备份文件导出/恢复

与桌面版（github.com/Luz7818/KeyVault）功能同源、数据各自独立。零外部依赖、
无遥测、无任何网络请求。

## 隐私政策（定稿）

提审用的隐私政策原文定稿在 [`PRIVACY-POLICY.md`](PRIVACY-POLICY.md)——发布
到公开 URL 后把地址填进商店，商店表单逐项填写见 [`CWS.md`](CWS.md)
「Privacy 面板逐项」。本文件不再保留草稿副本（单源，防两份漂移）。
