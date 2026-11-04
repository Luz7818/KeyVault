# HISTORY —— 版本更新记录

> 用途：记录每次版本与规范变更的内容和缘由。只追加，禁止删除或改写既有条目；写错了就追加一条
> 更正。新条目写在文件末尾。当前版本以 `kv/__init__.py` 单源为准（本文件不再复制版本号——
> 0.2.0/0.3.0 两轮升版均漏改此处，2026-11-05 起改为指向单源根治漂移）。

## 2026-10-05 · v0.1.0 版本控制建档与规范体系落位

- `git init` 建档（`main` 分支）：此前 13,837 行代码零版本控制，是核查报告挂账的 P1 风险。
  首个提交包含全部源码、测试与九件文档。
- 文档从"五件套"迁到九件体系：新增 `docs/ARCHITECTURE.md`、`docs/CODE-STYLE.md`、
  `docs/TESTING.md`、`docs/GIT.md`、`HISTORY.md`、`TODO.md`；`docs/getting-started.md`
  更名 `docs/GET-START.md`；`AGENTS.md` 重写为规范入口（原「仓库地图」移入 ARCHITECTURE，
  原「改动后的验证」移入 TESTING，关键约定按性质拆入各细则文件）。
- 补 `.gitignore`（build/dist/__pycache__/.pytest_cache/.kv）并把 `build/`、`dist/` 登记
  进 `.docsignore`——此前目录说明声称".gitignore 已排除"但文件并不存在，属失实陈述，已纠正。
- 变更缘由：落位《项目整体规范.md》九件必建。本仓更早开发历史未建档，可由代码与
  `tests/` 推断，不可追溯提交。

## 2026-10-05 · GUI 初始化报错掩盖真实根因（事务边界修复）

- 现象：GUI「初始化 Vault」弹「cannot rollback - no transaction is active」。
  连接是 autocommit（`isolation_level=None`），真实失败发生在 `apply_schema`
  阶段（`BEGIN` 之前，疑似新建 .db 被杀软瞬时锁定），此时无事务可回滚，
  except 分支的 `ROLLBACK` 自身抛错，把根因吞掉了。
- 修复：`db.session()` 与 `db.initialize()` 的 `ROLLBACK` 移进 `BEGIN` 成功后的
  内层 except——`BEGIN` 之前失败根因原样冒出，`BEGIN` 之后失败照常回滚。
- 测试：新增 `tests/test_vault_store.py::TestInitTransactionBoundary` 两条
  （BEGIN 前失败冒根因 / BEGIN 后失败回滚 meta），全量 543 → 545，
  修复后连续两轮 `OK (skipped=2)` 实测。
- 顺带修复：`test_list_on_absent_vault_reports_binding_exit_code` 隐式依赖
  「本机从未 init 过 vault」——本机 vault 一旦初始化该用例必假失败。用例内
  显式把 `KV_VAULT_DIR` 指向不存在的临时目录，与真实环境解耦。
- 同步事实口径：源文件 76 → 88 个 `.py`、13 837 → 15 593 行（10-04 统计后
  新模块入库所致，非本条改动产生），README / AGENTS.md / TESTING.md 已同步。

## 2026-10-05 · GUI 剪贴板监控检测到候选却不落库（watch 接口对齐）

- 现象：GUI「捕获 → 剪贴板监控」日志显示「检测到：…」，但审批队列和 secret
  表都是空的——候选从未落盘。
- 根因：GUI 向 `watch()` 传 `stop_event`/`on_event`，而签名里没有这两个参数，
  调用必抛 `TypeError`；GUI 的 `except TypeError` 分支静默退化到
  `_watch_fallback`——那个简化轮询只打日志，`spool` 被忽略，不写 inbox。
- 修复：`watch()` 正式支持 `stop_event`（置位即优雅退出，轮询 sleep 换成
  `Event.wait`）与 `on_event`（掩码摘要回调，不传照旧打印控制台）；
  `_read_key()` 包异常保护（GUI 线程/pythonw 无控制台不炸）；
  删除 `_watch_fallback` 与 `except TypeError` 分支。
- 测试：新增 `TestGuiIntegration` 三条（on_event 回调且明文不外泄 /
  stop_event 置位立即退出 / GUI 实参形态端到端 spool 进 inbox），
  全量 545 → 548，`OK (skipped=2)` 实测。
- 端到端实测：真 Win32Clipboard + PowerShell 写剪贴板 → watch 捕获 →
  inbox 1 条待审 → 接受后 `created`，secret 表有记录；测试数据已清除。

## 2026-10-05 · GUI 视觉改版（clam 主题 + 统一设计令牌）

- 动机：原界面大量继承 Windows 默认 ttk（vista 基底）观感，与「本地密钥
  保管箱」的工具定位不符；改版只动 `kv/gui/`（叶子模块），骨架与交互不变。
- 关键一手：ttk 基底切到 **clam**——vista 主题不吃颜色配置，表格/标签页/
  按钮全都没法定制；切 clam 后按设计令牌注册全量组件样式。
- 设计令牌集中在 `theme.py`：深墨蓝侧边栏 `#161a23`、白纸内容区、靛蓝主色
  `#4a6cf7`、三级文字灰阶、语义色；`setup_style()` 按组件组拆成 6 个子函数
  （受 `test_arch` 单函数 60 行预算约束）。
- 组件：按钮三档（Accent 实心主色 / 白底描边 / Danger 红字）、Treeview 行高
  32 无边框扁平表头、Notebook 扁平标签页选中主色、Entry 聚焦变主色、
  原生 Text/Listbox 统一 `theme.flat()` 1px 描边。
- 外壳：侧边栏品牌区（◆ KeyVault + 副标题 + 分隔线）+ 导航主色指示条；
  六页统一 `PageHeader`（标题 + 副标题）；主操作按钮一律 Accent。
- 微调：密钥管理页创建时间列精简到分钟（原精确到秒在常见窗口宽度下被截断）。
- 曾试 DWM 深色标题栏，被 `test_arch` 的 ctypes 隔离测试拦下（GUI 层禁
  ctypes）——门禁正确，撤回；标题栏保持系统默认。
- 验证：全量 548 条 `OK (skipped=2)`；六页构建切换无异常；逐页截图目检
  （DPI aware 截屏，演示数据驱动）。

## 2026-10-06 · GUI 质感升级：DPI 感知、圆角自绘控件、弹簧动效

- 用户反馈：文字不清晰、UI 偏土、无动效。三项的根因与对策：
  - **文字发虚**的根因是 tkinter 进程不感知 DPI——系统缩放下整个窗口被
    位图拉伸，换字体无效。`kv_gui.py` 入口开 per-monitor DPI 感知
    （FFI 按隔离规则放根 shim，不进 `kv/gui/`），`theme.init(scale)` 按显示
    器缩放系数重建像素字号与全部几何常量。
  - **直角与灰线**是"工具感"主要来源：新增 `kv/gui/controls.py`——
    Canvas 自绘 `RoundButton`（三档配色、hover 8 帧颜色渐变、按下即时
    变暗、释放才提交）与 `Card`（1px 圆角描边容器），全部按钮/表格/
    日志/信息面板换装。
  - **动效**：新增 `kv/gui/motion.py`——临界阻尼弹簧（可中断、重定向
    速度延续）。导航指示条在项间弹簧滑动、页面切换 18px 轻滑入、
    按钮/导航 hover 渐变；动画只碰 place/颜色，60fps 稳。
- 顺带修复：状态栏自第一版起就被 expand 的内容区挤出窗口（pack 顺序
  问题），此前从未显示过；改为先 pack 再排内容区。
- 等宽字体优先 Cascadia Mono（运行时检测，缺失退回 Consolas）；
  ttk clam 样式保留为兜底，主路径控件已自绘。
- 验证：全量 548 条 `OK (skipped=2)`；六页截图目检；`pyinstaller kv.spec`
  重打包通过（入口含 DPI 逻辑，`dist/kv.exe` 需要用新包才有效果）。

## 2026-10-06 · 字号整体上调（用户反馈文字过小）

- 像素字号基线：正文 13→15、辅助 12→13、标题 21→24；表格行高 32→38、
  控件高 34→38 随动。改动集中在 `theme.py` 的基线常量，DPI 缩放路径不变。
- 排查记录：截图曾显示导航指示条停在错误行——实测是截图脚本 `sleep`
  冻结了 Tk 事件循环、after 动画帧不走所致；主循环正常时弹簧收敛正确，
  应用无 bug。

## 2026-10-06 · 文档核查修复（GUI 批之后数字对齐 + corpus 下钻合规）

- **数字对齐实测**：README/AGENTS 的源文件 88→90 个 `.py`、15 593→16 396 行
  （10-06 GUI 批新增 controls.py/motion.py 后未回写）；README 目录表与 tests/README
  标题、GET-START 通过标准的 543→548 对齐 AGENTS「Ran 548」口径。
- **corpus 下钻合规**：`tests/corpus/README.md` 按规范§1.2「二级不下钻另开 README」
  并入 tests/README 的「corpus/ —— 金标语料（下钻说明）」节（内容全量保留），原文件删除。
- **杂项**：GET-START 示例输出 `<detected>` 改真实示例值并加注；kv/README 删模板残留段；
  ARCHITECTURE 树 `<九件文档>` 占位改普通标注；目录说明树删除与 ARCHITECTURE
  根文件表重复的职责短语；TODO「正在做」随迁移完成更新。

## 2026-10-06 · GUI 质感对标成熟案例：自绘图标/标签页/滚动条/输入框

- 用户反馈仍不美观——根因是 ttk 原生控件（Notebook 方角 tab、系统滚动
  条、方角 Entry）无法深度美化。本轮把「关键表面」全部自绘：
  - `kv/gui/icons.py`：6 枚 Canvas 线条图标（钥匙/放大镜/闪电/层叠/
    盾牌/滑杆），颜色随选中态重绘，不依赖图标字体的跨版本渲染；
  - `controls.TabBar`：替代 ttk.Notebook——pill 导航条（选中底色弹簧
    滑动、文字色过渡）+ place 堆叠内容区；capture/ingest/security 三页
    全部换装；
  - `controls.SlimScrollbar`：8px 圆角细滚动条（hover 加深、拖动/滚轮/
    点击轨道），DataTable 内置，系统滚动条全部退役；
  - `controls.RoundEntry`：圆角描边输入框（聚焦变主色），搜索/路径/
    表单/配置项全部换装。
- 侧边栏导航分组（密钥/工具/系统）+ 图标；表格状态列着色（active 绿 /
  revoked 红 / expired 橙）。
- 修复中发现的组件 bug：TabBar 初始堆叠未 raise 当前页、`<Configure>`
  重排清掉选中 pill 不重画、RoundEntry 全高 Entry 盖住描边上下边、
  SlimScrollbar 误用 Canvas 不存在的 command 选项。
- 验证：全量 548 条 `OK (skipped=2)`；六页截图目检（含 TabBar 切换、
  表单对齐）；`pyinstaller kv.spec` 重打包通过。
- 数字口径：源文件 90→91 个 `.py`、16 396→16 746 行（新增 icons.py 等，
  README/AGENTS 已同步）。

## 2026-10-06 · 字号二轮上调 + 界面缩放设置（用户反馈文字仍小）

- 像素字号基线：正文 15→17、辅助 13→14、标题 24→28；行高 38→44、控件高
  38→42、侧边栏 236、间距全档随动；默认窗口 1080×720 → 1180×780。
- 新增「界面缩放」设置项（设置页，1.0/1.1/1.25/1.5，重启生效）：存设置表
  `ui_scale`，入口 `kv_gui.py` 启动时读取并与 DPI 缩放相乘——以后调大小
  不用改代码。vault 未初始化/值越界时按 100% 不挡启动。
- secrets 列宽随字号放大（155/120/80/205/165）。
- 验证：全量 548 条 `OK (skipped=2)`；截图目检（17px 正文 + 1180 宽窗口
  比例协调）。dist/kv.exe 重打包时文件被占用（应用运行中），关掉后重跑
  `build.bat` 即可。

## 2026-10-07 · 根目录最小化：测试与打包链路归位

- 按《文档标准》§2.3 最小根原则收拢根文件：`runtests.py` → `tests/runtests.py`（测试入口
  与测试套件同目录，`ROOT` 推导改两层）；`build.bat` + `kv.spec` → `packaging/`（构建脚本、
  打包配置与说明同目录），spec 入口改 `../kv_gui.py`，build.bat 先回仓库根再打包并写死
  `--distpath dist --workpath build`——产物路径 `dist/kv.exe` 不变。
- 根 shim `kv.py`/`kv_gui.py` 按 §2.3 第 4 条（入口脚本留根）保留。
- 验证：`python -I tests/runtests.py` 全量通过；`check_docs.py KeyVault` 阻断无、提醒无。

## 2026-10-07 · 字体真自适应：Ctrl+滚轮即时缩放（字体+控件+行高全随动）

- 用户反馈设置页未见到界面缩放（实为旧 exe）且要求「确实自适应」。本轮：
  - 主题字体改为 tkinter **命名字体**（named font）——一处 configure(size)
    全应用立即跟随，这是即时缩放的机制核心；
  - `theme.set_zoom()`：字体 + 控件高/圆角 + Treeview 行高同步更新，
    自绘组件经 `theme.LISTENERS` 注册回调（按钮重测宽度防溢出、输入框
    重设高度、TabBar 重排 pill），destroy 时注销；
  - 交互：**Ctrl+滚轮 / Ctrl+= / Ctrl+-** 即时缩放（0.85–1.60），状态栏
    提示当前档位，自动写回设置表 `ui_scale`，下次启动保持；
  - 窗口默认尺寸 clamp 到屏幕 88%/95%——1.25 档在 900p 屏上不再超界。
- 设置页「界面缩放」下拉与 Ctrl+滚轮写同一配置，两路并存。
- 验证：1.0/1.25 双档截图（批量操作页对比，按钮/输入框/文字等比放大
  无破版）；全量 548 条 `OK (skipped=2)`；新 exe 打包通过。

## 2026-10-07 · 按钮内边距修复（用户反馈文字贴边）

- 根因：RoundButton/TabBar pill 宽度用的是 `PAD_LG - PAD` 的差值（仅 10px），
  文字到边框的间距过小。
- 修复：新增独立令牌 `BTN_PAD_X=16 / BTN_PAD_Y=12`（zoom 感知，set_zoom
  随动）；按钮宽度 = 文字 + 2×BTN_PAD_X，高度 = max(CONTROL_HEIGHT,
  文字行高 + 2×BTN_PAD_Y)——字号缩放后文字不再贴边不溢出；TabBar pill
  与 RoundEntry 内边距同步。
- 顺带：侧边栏激活行的图标画布底色跟随染色（此前高亮块缺图标一段）。
- 验证：设置页截图目检按钮留白；全量 548 条 `OK (skipped=2)`；新 exe 打包通过。

## 2026-10-07 · 图标语言升级（用户要求更有设计感）

- 重写 `kv/gui/icons.py`：圆头线条 + 双层线宽（主线 1.9/细节 1.4）+
  实心点缀——斜钥匙加环心点、放大镜加玻璃高光弧、批量操作改三层菱形
  堆叠（Stack 图标）、盾牌加内部对勾、设置改双滑杆错位旋钮。
- 新增品牌标志 `draw_logo`：圆角盾 + 中心钥匙孔（孔色独立），替代品牌区
  的 "◆" 字符，侧边栏排布改为「logo + KeyVault」横排。
- `_build_layout` 按预算拆出 `_build_brand` / `_build_nav`（test_arch
  60 行预算拦截后重构）。
- 验证：侧边栏截图目检（含放大裁片）；全量 548 条 `OK (skipped=2)`；
  新 exe 打包通过。

## 2026-10-07 · 应用图标：品牌盾徽替代默认羽毛

- 用户反馈窗口/任务栏还是 tkinter 默认羽毛图标、exe 文件图标为空白。
- 新增 `packaging/make_icon.py`：把矢量盾徽（与 GUI 内 logo 同源形状）
  光栅化为 256px PNG——纯 stdlib 手写 PNG 编码与 PNG-in-ICO 容器
  （超采样抗锯齿、纵向渐变、白色钥匙孔），产物 `packaging/icon.png`
  与 `icon.ico` 入库随仓走。
- `kv.spec`：EXE 嵌入 `icon.ico`（资源管理器里的 exe 图标），datas 带上
  `icon.png`；新增 `kv/gui/icon.py` 用 `iconphoto` 设置标题栏/任务栏图标
  （开发态找仓库、打包态找 _MEIPASS，缺失静默跳过）。
- 验证：标题栏截图确认盾徽生效；程序断言 photo 绑定；全量 548 条
  `OK (skipped=2)`；新 exe 打包通过。

## 2026-10-07 · 修复 build.bat 双击无效（编码不匹配）

- 用户双击 packaging\build.bat 后不产生新 exe。根因：bat 是 UTF-8 编码，
  中文 Windows 的 cmd 默认按 GBK 解析批处理——中文注释被读成乱码"命令"，
  脚本从注释行开始整体错位，python 命令根本没执行。
- 修复：bat 首行加 `chcp 65001 >nul`（与文件 UTF-8 编码一致），顺带把
  PyInstaller 安装步骤改为「已装则跳过」，打包失败提示里点明最常见原因
  （dist\kv.exe 正在运行）。
- 验证：cmd 模拟双击完整跑通（中文输出正常、打包成功）；用
  ExtractAssociatedIcon 提取 dist\kv.exe 关联图标确认盾徽已嵌入 exe 资源。

## 2026-10-07 · 批1 功能三件套：备份/恢复 + 健康提醒 + 空态引导

- **备份/恢复（全新功能，CLI/GUI 一起补）**：侦察发现 `kv export` 只是 init
  的引导文案，从未实现。新增 `kv/ops/backup.py`——口令加密的全量明文导出
  （.kvb）：格式 `KVBK1|salt16|nonce16|ct|tag32`，scrypt(n=2^15,maxmem 64MB)
  派生 64B（前 32 流加密 SHA256-CTR / 后 32 HMAC 认证，encrypt-then-MAC）。
  存明文口令加密而非 DPAPI 密文：blob 换机即废，备份密文等于备份废铁；
  口令是唯一防线（≥8 位强制，GUI 双确认）。恢复走 saveops.commit 正常入库
  （去重/审计链复用），错口令/篡改 → BackupAuthError。CLI：kv export/restore
  待批 2 补充命令挂载（本次 GUI 优先）。**ARCHITECTURE 待补格式决策**。
- **健康报告**：`kv/ops/health.py`——30 天内到期（行高亮橙）/已过期计数/
  180 天未轮换；security 页「健康报告」按钮 + secrets 表行着色（st-expiring）。
- **空态引导**：vault 未初始化 → secrets 页大标题引导卡（一键初始化，公共
  `app.initialize_vault()`，settings 页改调它）；空库 → 「添加密钥/设置/捕获」
  三按钮引导。
- 测试 +9（backup 往返/错口令/篡改/弱口令/二次恢复去重、health 三分支），
  脱敏表补 BackupError/BackupAuthError 两行（test_arch 门禁要求）。
- 验证：全量 557 条 `OK (skipped=2)`；截图目检备份 tab/未初始化空态/
  空库空态；`Ran 557` 口径同步 README/AGENTS。

## 2026-10-07 · 批2：Ctrl+K 命令面板

- 新增 `kv/gui/palette.py`：无边框命令面板——输入即过滤（前缀 > 包含 >
  子序列三档打分，短名优先），↑↓ 选择、Enter 执行、Esc/失焦关闭。
- 动作：回车把选中密钥复制到剪贴板（30s 自动擦除），复制逻辑收敛到
  `app.copy_secret_by_name`（密钥页右键菜单同款，消除重复实现）。
- 全局 `Ctrl+K` 唤起；vault 未初始化时面板内给出提示。
- 验证：演示 vault 全链路（过滤 → Enter → 剪贴板明文比对一致）；全量
  557 条 `OK (skipped=2)`。

## 2026-10-07 · 批3：审批队列批量接受 + 审计筛选

- 捕获页审批队列加「接受全部（高置信）」：复用 CLI a 键的
  `reviewops.review(auto=True)`，一行状态栏汇报 存入/重复/跳过。
- 审计日志加筛选：事件类型下拉（从已加载行动态取值，不硬编码枚举）+
  记录名包含过滤（KeyRelease 即时），客户端过滤已加载行，状态栏显示
  「N 条（共 M 条）」。
- 验证：脚本断言筛选结果（event=save 2 条、name~deepseek 1 条）；全量
  557 条 `OK (skipped=2)`。

## 2026-10-07 · 批4：详情弹窗卡片化 + 操作历史

- 密钥详情从「mono 纯文本」重做为卡片布局：大标题 + 平台/状态徽章 +
  分区字段网格（基本信息/判定依据/指纹历史/操作历史），内容超高时
  SlimScrollbar 滚动。
- 操作历史直接在弹窗里看：secret_id 维度最近 15 条审计（时间/事件/操作者），
  尾注指向安全页审计日志。
- `_fill_detail`（mono 文本版）移除，`_on_show` 只做取数。
- 验证：弹窗截图目检（徽章/网格/滚动）；全量 557 条 `OK (skipped=2)`。

## 2026-10-07 · 批4 补丁：详情弹窗拆独立模块（文件预算拦截）

- secrets.py 472 行超出单文件 460 预算（test_arch 拦截）。详情弹窗只依赖
  数据（row/fps/history），拆到 `kv/gui/detail_window.py`
  （detail_rows / build_detail_window），secrets.py 回落 389 行。
- 门禁 557 条 `OK (skipped=2)`，弹窗冒烟通过，新 exe 打包。

## 2026-10-07 · 修复 TabBar 点击失灵与残留 pill（用户反馈标签无法切换）

- 两个叠加的根因：
  1. `_place_pill` 不删除旧 pill——每次切换叠加一个新色块，残留的旧 pill
     既显示为「另一个选中态」又（不透明填充）拦截其覆盖区域的点击；
  2. hitbox 用 `fill=""`——Tk Canvas 透明填充不参与命中判定，点击只能
     落在 1px 描边线上，几乎必然落空。
- 修复：pill 先删旧再画；hitbox 改纸面色（可命中、无视觉差）；点击绑定
  挂到 hit/pill/文字共享的 `tab-<key>` tag；层叠固定为 hit → pill → 文字。
- 验证：`find_closest` 命中项带正确 tag、点击后 current 切换、画布上
  pill 恰好 1 个、RoundEntry 输入与 var 同步；全量 557 条 `OK (skipped=2)`；
  新 exe 打包通过。

## 2026-10-12 · 全量实测修复批（用户反馈「实际使用有很多问题和 bug」）

实测口径：真 CLI 端到端（独立沙箱 vault，不碰用户数据）+ 源码 GUI 自动化交互 +
双 exe（kv.exe / kv-cli.exe）冻结态验证；先复现后修复，每条都留了回归测试。

- **文档面（README / GET-START / ARCHITECTURE）**：`kv add ds sk-abc` 位置参数根本不
  存在（实际 `--name/--value`），「30 秒跑通」首步必失败；vault 位置写成「当前目录建
  `.kv/`」实际是 `%LOCALAPPDATA%\KeyVault\`；`use --target` 实际是 `--to`；
  「548 个用例」过期。全部按实际行为改写，附 `--vault-dir`/`KV_VAULT_DIR` 说明。
- **TabBar 初始标签错位（三页全中）**：`add()` 立即 place 内容层，place 堆叠序 =
  创建序，后注册的标签盖住初始页 —— 捕获/批量操作/安全页首显时高亮第一个标签、
  显示的却是最后一个标签的内容（点一下才自愈）。修：`add()` 每次重新 tkraise 当前页；
  新增 `tests/test_gui_tabbar.py`（winfo containing 行为级断言，旧代码 FAILED）。
- **`kv copy` 自动擦除两处静默失效**：(1) `spawn_helper` 不传 `--vault-dir`，
  helper 打开默认库查不到 wipe 行就放弃 —— `--vault-dir` 下密钥永远留在剪切板；
  (2) 冻结态 helper 命令 `python -I kv.py _wipe N` 在 exe 里等于再启动一个 GUI，
  每复制一次弹一个幽灵窗口且永不擦除。修：`spawn_helper` 显式带 vault_dir 并在
  `sys.frozen` 下改派 exe 自身；`kv_gui.py` 对 `_wipe` argv 做 CLI 分发；
  spec 把 `kv.cli` 进 hiddenimports。真机验证：源码模式与双 exe 模式擦除均到位。
- **`kv scan` 谎报 clean**：路径不存在时 `os.walk` 静默返回空 → 「未发现泄露」exit 0。
  安全工具的假 clean 比报错严重。修：不存在报 `NotFoundError`（exit 2）、非目录报
  `UsageError`（exit 1）；应用户报告顺带剪枝 `.git`/`__pycache__`/`node_modules`/
  虚拟环境与工具缓存目录（GUI 实测扫同目录 25 文件 vs CLI 2 文件即此因）。
- **`kv export` 命令不存在**：`init` 的 R1 警告点名「现在就做一次导出：kv export
  --to …」但 CLI 没有该命令。修：新增 `kv export --to` / `kv restore --from`
  （`kv/commands/backup.py`，口令走隐藏提示不进 argv），复用 `kv/ops/backup.py`。
- **打包链路补 CLI 产物**：spec 新增 `kv-cli.exe`（console=True，入口 `kv.py`，独立
  Analysis），补上命令行用户拿 kv.exe 只能得 GUI 的缺口。
- **测试卫生**：`tests/test_capture_wipe.py` 不再真起分离子进程（曾经每轮测试开一个
  打开真实用户 vault 的进程 + ResourceWarning），mock `spawn_helper` 并断言 vault_dir
  传递；`tests/test_ops_fix.py` 的 `[OK] 已记住` 不再泄漏进测试报告。
- **杂项**：设置页信息区高度 16→10 行（修复底部「界面缩放」被窗口裁切）；
  扫描结果表「文件」列 280→430（Windows 长路径不再截断）；
  `use` 被 git 跟踪时的提示不再循环推荐 `--gitignore`（改指 `git rm --cached`）。
- 门禁：全量 572 条 `OK (skipped=2)`；`selftest --golden` 92 条全 PASS；
  重打包通过，双 exe 均冒烟验证。

## 2026-10-12 · 遗留任务处理批（TODO 四项清空）+ 两处计划外修复

任务 1（剪贴板测试稳定性观察）：连续两轮全量 `Ran 572, OK (skipped=2)`、0 error，
10-04 记录的环境耦合 error 未再复现，按验收标准在 TODO 注销本条。

任务 2（scan 性能基准）：新增 `tests/bench_scan.py`（不进 unittest discover）——
造 N 条密钥的真 DPAPI vault + M 文件目录树，计时 `scan_directory`。实测（i9-13900H /
32 GB / NVMe SSD）：10 000 指纹 × 200 文件 / 1.2 MB → 3.0 s，其中约 2.4 s 是
1 万次 DPAPI 解密、遍历+匹配本体约 0.6 s（`--fake` 对照 0.65 s）。数字与机器说明
已登记 AGENTS「当前状态」。

任务 3（打包校验固化）：新增 `packaging/verify.py`，五查任一失败退出码 1 ——
版本号（kv-cli --version vs kv/__init__.py）、init 建库（schema 进包）、
selftest --golden（语料进包）、`_wipe` 冻结态短路（不弹窗口）、GUI 真启动
（窗口标题出现，只回收本次新起的进程，绝不动用户自己开的 kv.exe）。
已接入 build.bat（构建→校验→失败即退）；失败路径自测（--expect 9.9.9 → exit 1）。

任务 4（GUI exe 冻结态擦除冒烟）：实测通过 —— 在 dist/kv.exe（GUI）里经命令面板
复制 → 状态栏「已复制，30 秒后自动擦除」→ 剪贴板 35 s 后被换成 benign 文本，
审计链有 copy(gui) + wipe(wipe-helper)，pending_wipe=done，全程无幽灵窗口。

**计划外发现 1 —— schema.sql 与 golden 语料未进包（严重）**：spec 的 datas 只带
了 icon.png，两个 exe 一 `init` 就报「文件不存在：_MEIxxxx/kv/core/schema.sql」，
`selftest --golden` 找不到语料 —— 冻结态 scan 的闸门永远过不了。此前测试都从源码
建库所以从未暴露。修复：两个 Analysis 的 datas 补 `kv/core/schema.sql` 与
`tests/corpus`（冻结态路径都基于 `__file__`，_MEIPASS 下同构，db.py 无需改）；
`tests/test_arch.py::TestPackagingDataFiles` 新增断言防回归；verify.py 的
init/golden 两查在旧包上实测拦截成功。

**计划外发现 2 —— build.bat 中文行被 cmd 吞字节**：chcp 65001 + UTF-8 bat 下，
中文行在特定字节偏移处会吞掉下一行行首（164 字节脚本复现：末行 `echo 不需要…`
的 `echo 不需` 被吃，cmd 报 `'??安装' is not recognized`）。文案调整治不了，
改为 build.bat 输出纯 ASCII（Python 侧的中文输出不受影响，正常显示）；
packaging/README「别动」记录该约束与复现尺寸。

## 2026-10-14 · GUI 质感升级批（用户反馈「太简单没质感」）

设计语言：浅灰画布 + 白卡片分层、深墨蓝渐变侧边栏、主色聚焦光晕、行级 hover
反馈。全部 tkinter 手绘（stdlib 不变），无位图资源。

- **画布分层**：内容区底色改浅灰 CANVAS（theme.py 新令牌 + Page.* 样式族），
  白卡片浮在画布上——此前全白底只剩 1px 描边可用，是「平」的主要来源；
  六页全部适配（卡片、工具栏、页头、LabelFrame 各自换 Page 样式）。
- **侧边栏重写为一整块 Canvas**（app.py）：垂直渐变（SIDEBAR_TOP/BOTTOM）+
  手绘品牌区/分组导航/主色指示条（弹簧滑动）/页脚版本栏。tk.Label 拼 sidebar
  的话每个不透明控件都会在渐变上盖出色差，手绘是唯一无色差的做法。
  顺带修复：首帧 sidebar 未画好时 show_page 提前返回不抬页面（启动停在设置页）。
- **组件精修**（controls/button/paint/widgets）：RoundButton 拆到 button.py、
  绘图原语拆到 paint.py（controls 回落 330 行预算内）；accent 按钮加深色描边
  （立体感）；RoundEntry 聚焦画 ACCENT_SOFT 光晕环；TabBar pill 改圆角胶囊 +
  非选中项 hover 渐变；Card 描边精修 + 底部影线；表格去斑马纹改行 hover 高亮
  （点选交回选中色），扫描/检测列宽微调（aws-access-key-id 不再截断）；
  状态栏加状态点（消息性质编码成颜色）。
- **空态插图**：密钥管理页空态画主色浅底圆 + 钥匙线稿，居中排版。
- **测试**：TabBar 回归测试重写为两层——机制层（数 tkraise，旧实现只抬 1 次、
  修复后每次 add 都抬）+ 屏幕层（winfo_containing，桌面被全屏遮挡层覆盖时
  自动 skipTest；实测 explorer 的 Backstop Window 会整面挡住 hit-test 且
  topmost 也压不过，此类环境跳过不误报）。真剪切板测试加探针：OS 拒绝
  剪贴板访问时显式 skip 而非误报（本会话实测剪贴板被系统级锁死）。
- 门禁：`Ran 575, OK (skipped=4)`（2 项原有 skip + 屏幕层/剪贴板的环境条件
  skip）；六页离屏截图（PrintWindow）逐页目检通过；重打包通过。

## 2026-10-15 · GUI 设计元素批（用户反馈「加点设计元素」）

在画布分层的基础上加了五件东西，全部 tkinter 手绘、零依赖：

- **密钥页仪表盘**（新模块 kv/gui/statbar.py）：四张指标卡（全部/启用/30 天内
  到期/已吊销过期，语义色条 + 图标芯片 + 大数字）+ 平台分布环形图（Canvas arc
  圆环段 + 色点图例，长平台名截断省略）。聚合是纯函数（collect_metrics /
  platform_distribution），tests/test_gui_statbar.py 6 条守口径——仪表盘不许
  说谎；有记录才显示，空态/未初始化隐藏。
- **页头图标芯片**：六页 PageHeader 统一加 ACCENT_SOFT 圆角芯片 + 各自的
  矢量线稿图标（钥匙/放大镜/闪电/层叠/盾牌/滑杆）。
- **表格状态列中文化**：● 启用 / ● 已吊销 / ● 已过期，点色随整行语义 tag。
- **侧边栏页脚快捷键提示**：描边胶囊「Ctrl + K 快速复制密钥」。
- **详情窗徽章状态着色**：启用主色 / 吊销红 / 过期橙（此前固定主色）。

修的三个实现坑：StatCard/DonutCard 落在 Canvas 默认请求宽上会把一行卡片撑出
横向溢出（width=1 让位给 pack）；空环形图误用 create_oval（无 style 选项，
改 create_arc extent=359.9）；build_detail_window 加色后破 60 行函数预算
（徽章色表提为模块常量 BADGE_STYLES）。

门禁 `Ran 581, OK (skipped=3)`；secrets/detect/security 三页离屏截图目检；
全部文件在 460/60 行预算内。

## 2026-10-15 · GUI 交互批（用户反馈「增加交互」）

焦点：给「已经好看」的界面补上操作反馈与键盘层。全部改动遵循一个原则：
一个动作只有一个实现，键盘/快捷键只是同一条路径的新入口。

- **表格排序指示**：点表头排序早就存在但从无反馈——当前列表头带 ▲/▼，
  换列/反向时箭头随动（DataTable._paint_sort_arrow）。
- **键盘层**（secrets 表格）：Enter 详情 / Delete 删除 / Ctrl+C 复制到剪切板 /
  F5 刷新，全部委托右键菜单同一批方法；Ctrl+F 全局委派当前页的搜索框
  （app._focus_search，页面无搜索就不接）。
- **搜索占位符**（RoundEntry placeholder 参数）：未聚焦且值为空时显示。
  坑：Entry 是窗口项永远压过画布项，占位文字画在 Canvas 上会被整个盖住
  （实测截图发现）——改为叠在 Entry 内部的 Label 覆盖层，点击穿透聚焦。
  另一个坑：StringVar 的 trace 先于 Entry 内部同步触发，判空必须读 var
  而不是 entry.get()。
- **搜索无匹配态**：查无结果不再是干巴巴的空表——「没有匹配的记录 + 命中
  的条件 + 一键清除搜索」。与空库空态明确区分（StateCards 统一管理，
  从 secrets.py 拆出 secrets_states.py 回落 460 行预算）。
- **悬浮提示**（新模块 kv/gui/tooltip.py）：停驻 600ms 的深色气泡，
  attach() 一行接入；搜索框/状态筛选/添加/刷新四个工具栏控件已挂。
- **详情窗**：Esc 关闭；sha256 / 掩码预览点击即复制（非敏感值，明文仍只走
  copy 命令的归零窗口），底栏反馈「已复制×××」；复制走同一
  Win32Clipboard 排除历史。

门禁 `Ran 587, OK (skipped=3)`（新增 6 条：排序箭头、占位符三态、键盘接线、
屏幕级 TabBar 命中在遮挡层消失后恢复运行并通过）；secrets 两态截图目检。

## 2026-10-16 · 侧边栏排版重做（用户反馈截图「左侧栏文字排布不美观」）

用户截图里的三个问题：图标与文字间距过窄（图标不随 UI 缩放长）、分组
标题/图标/文字三套左对齐互相打架、品牌区副标题对齐到 logo 左缘而歪向一侧。

- **排版网格单源化**（app._sidebar_grid）：margin / 图标列 / 文字列 / 行高 /
  组间距一套列位定义，品牌、分组标题、导航行、页脚全部从网格取值——
  分组标题对齐图标列，文字列 = 图标列 + 图标宽 + 间距，不再各画各的。
- **几何随 UI 缩放联动**（theme.sp：DPI×UI 缩放，区别于只按 DPI 的 _s）：
  此前侧边栏图标/间距只按 DPI 缩，用户在设置页放大字体后图标不长大、
  间距相对变小，出现「图标贴字」。侧边栏宽度也随缩放联动（_px(252)），
  放大字体后行内容不再挤满整栏。
- 品牌区：logo 加大到 34，副标题改与标题左对齐（同一条基线网格）。

验证：zoom 1.0 与 1.25 两档截图对比（图标/间距/栏宽三联动）；全量 587
`OK (skipped=2)`（屏幕级命中测试恢复运行并通过）。

## 2026-10-17 · 侧边栏间距再放宽（用户反馈「靠的还是有点紧」）

网格呼吸感整体加一档：图标→文字间距 12→16、行高 40→42、组间距 10→12、
分组标题高 30→32、品牌 logo→标题 10→12。zoom 1.0 / 1.25 双档截图确认
（图标与文字间有明确空隙，选中态胶囊内文字不顶边）。门禁 587 OK。

## 2026-10-17 · 高缩放档位显示不全修复批（用户反馈截图）

根因：窗口高度被屏幕上限卡住后，固定布局在高 UI 缩放档位总高/总宽超出
视口，pack/Canvas 对放不下的内容直接裁掉。修法分三层：

- **ScrollPage 可滚动页**（widgets.py 新组件）：内容超高自动出细滚动条 +
  收起；设置页 / 密钥页 / 检测页整体换装，子控件挂 content。全局滚轮
  委派（app._on_wheel）：指针所在页可滚则滚，Treeview/Text/Listbox 的
  内建滚动不抢，Ctrl+滚轮缩放优先。
- **侧边栏拆组件 + 可滚**：sidebar.py（214 行）从 app.py 独立（app 回落
  270 行）；页脚锚「视口底与导航底取更靠下者」，导航超高时页脚随内容走、
  侧边栏滚轮可滚——修掉页脚与导航行重叠（_paint_footer 双重减页脚高的
  签名 bug）与指示条弹簧旧几何落错行（整块重画一律静态落位）。
- **横向自适应**：RoundEntry 宽度、DataTable 列宽（声明值只定**比例**，
  Configure 时按视口分摊，任何缩放档位列都完整可见）、仪表盘栅格重排
  （视口窄自动 3×2，宽则 1×6）全部随 DPI×UI 缩放；指标卡/环形图高度
  以内容请求高自适应（修「已吊销 / 过期」裁半）。

坑：tk Canvas 占位文字会被窗口项（Entry）盖住；StringVar trace 先于
Entry 同步；Treeview 变量 trace 时序；_paint_footer 参数重复减页脚高。
截图验证：zoom 1.5 六页逐一目检（本批所有截图均为 150% 档）。
门禁 `Ran 587, OK (skipped=3)`。

## 2026-10-17 · 全屏→小屏裁切 + 缩放卡顿修复批（用户反馈截图）

- **栅格空列残留（全屏→小屏裁切的根因）**：仪表盘 6 列布局时
  columnconfigure(0..5) 全部带 weight，窄视口折回 3 列后只重排了 0..2 ——
  列 3..5 的 weight 残留，栅格把一半宽度分给空列，卡片挤在左半、
  环形图图例被裁。修：_reflow 对 0..5 全量重置 weight。
- **指标卡/图例按像素截断**：按字符数截断在高缩放下必超宽（「ogle a」
  被硬裁）——改为 tkfont.measure 逐字收缩加省略号，可用宽按卡片实际宽
  推导；标签改紧凑文案（30天内到期 / 吊销/过期）。
- **缩放防抖**：Ctrl+滚轮连发时每次 tick 都同步跑 set_zoom 全量级联
  （命名字体 + 全部 LISTENERS 重绘 + 写库）——卡顿根因。改为 140ms
  防抖：连发只累积目标值，停手应用一次、写库一次。
- **侧边栏 hover O(1)**：hover 从整块重画（140 条渐变带 + 全部条目）
  改为局部更新（删/建一行底色块 + 两个文字色），鼠标扫过导航列不再
  触发全量重绘。
- StatCard._fit 去掉 update_idletasks（reqheight 即时有效，缩放级联里
  同步跑布局队列是隐藏卡顿源）。
- 门禁 587 OK；最大化→还原截图验证栅格铺满全宽；zoom 1.0/1.25 双档验证。

## 2026-10-17 · 品牌区标题/副标题间距修复（用户反馈截图「挨的还是太紧」）

副标题位置原是固定 24px 偏移，而标题（28px 逻辑字号）的行高半在
高缩放下超过它——两行字实际是叠着的，不是「紧」是「重叠」。
修：副标题 y 按字体真实行高度量排（标题底 + 8px 间距 + 副标题半高），
任何缩放档位下两行都不接触；导航起始位相应下移到 120。
zoom 1.0 / 1.25 品牌区截图验证。

## 2026-10-17 · 品牌区间距微调（用户反馈「又有一点分的太开了」）

度量排版后的 8px 显式间距叠加字体行高自带内边距，视觉上偏松。
副标题改为与标题行高盒**直接相接**（间距项归零）——字形之间的呼吸感
由 linespace 内建的内边距提供，两档缩放截图确认不再需要额外间距。

## 2026-10-18 · 标签页高缩放溢出修复批（ScrollPage 下沉到 TabBar）

zoom 1.5 实测：标签页内的固定表单（备份/恢复、导入/注入、轮换）**双向溢出**
——「导出备份」按钮被右缘裁掉、恢复区被底缘裁掉。此前 ScrollPage 只覆盖了
三个整页，TabBar 内的十来个标签还是固定布局。

- **TabBar.add 统一包 ScrollPage**：每个标签的内容层自动变成可滚容器
  （bg=SURFACE 白底变体），返回其 content——页面的构建代码不用改。
  局部导入解决 controls↔widgets 循环依赖。
- **横向**：全行入口（备份路径/恢复路径/导入路径/注入目标）改弹性宽
  （width=200 + fill x expand）；口令类收窄；rotate/inject 表单加
  columnconfigure 让值框跟随伸缩。
- **纵向**：标签内表格（扫描/审计/修复未识别/纠正规则）传 rows=8/9 定高，
  更多行由表格自带滚动条接管。
- **根因级修复**：Card/RoundEntry 的 _draw 用的是 winfo_reqwidth——
  pack fill/expand 放大后请求宽不跟随，圆角框画在分配区一角，看起来像
  控件「断掉」（路径输入框就是这样）。统一改用实际分配尺寸绘制。
- **滚轮委派泛化**：app._on_wheel 对嵌套 ScrollPage（标签内）同样生效
  （hasattr wheel_scroll 协议，不再依赖 _pages 成员判断）。
- 门禁 587 OK；zoom 1.5 五个固定表单标签逐一截图验证无裁切。

## 2026-10-19 · 侧边栏整行可点 + 启动/缩放性能批（用户反馈「只有放到文字上才能点、启动缩放卡顿」）

**点击区**：导航行的点击与 hover 用 tag_bind 挂在文字 item 上——tag 只在文字
像素上有效，图标与行的空白区域点不动也悬停不了。

- **Canvas 级命中**：Sidebar 改绑 `<Button-1>`/`<Motion>`/`<Leave>` 到整块画布，
  `_hit_row` 按 `_row_geo` + `canvasy`（滚动换算）判定落点行——整行可点可悬停，
  行外（品牌区/页脚）不误触；行上给手型光标（值不变不动 Tk）。
- 新增 `tests/test_gui_sidebar.py` 6 条：行右端空白/图标列点击、行外不触发、
  滚动后命中「看见的行」、hover 随指针、重绘合帧契约。

**启动**：6 个页面全部同步预建是几百个 widget 的构造 + 全部 LISTENERS 注册，
且 `set_zoom` 在建页之后又触发一次全应用重排。

- **页面懒构建**：`_register_pages` 只登记类，`show_page` 首次导航才实例化。
  启动只建 secrets 页（实测 init 364 ms，此前约 700 ms+）；未访问页面不参与
  布局也不参与缩放级联（级联 LISTENERS 14 个 vs 此前 ~30）。
- **缩放前置**：`set_zoom(ui_scale)` 挪到建页之前——页面按最终字号一次建对，
  消掉启动时第二次全应用重排。

**缩放/重绘级联**：

- `set_zoom` 值不变时早退（重复 Ctrl+= 到顶、启动重放持久值）：实测 146 ms → 0.006 ms。
- `DonutCard._render` 去掉 `update_idletasks`——缩放级联里同步跑整个布局队列，
  与 StatCard._fit 同源（该处 10-12 已修，此处漏网）。
- Sidebar 重绘合帧：Configure/缩放的重绘统一走 `after_idle`（`_schedule_paint`），
  同一批事件只画一次，拖窗不再逐事件全量重绘；渐变 140 带 → 64 带（两端色差
  ~20/255，带间差 <0.35 肉眼不可辨），item 数近乎减半。

- 门禁 593 OK（587 + 新增 6）；`selftest --golden` 92 条全 PASS；
  全页面导航冒烟 + 同值 set_zoom 幂等实测通过。

## 2026-10-19 · 侧边栏居中结构化 + 监听器销毁摘除（用户反馈「不管怎么缩放字和图标应居中」）

**居中**：像素级测量（GDI 截屏 + 墨迹 bbox，100%/130%/160% 三档 + 运行中 Ctrl+滚轮
动态缩放）证明图标/文字墨迹中心与行中心偏差 ≤±1.5px——原实现的居中在测量意义
上本就成立；问题出在用户验证的是 10-17 旧构建的 dist/kv.exe。借此把「居中」从
恰好正确改成结构保证：

- 行内两个**未随缩放**的魔数偏移（药丸底距 `-4`、内容中心 `-2`）改为 `row_gap = _px(4)`，
  新增 `row_cy = (row_h - row_gap) / 2`——图标与文字统一落**药丸几何中心**，
  药丸/hover 块/行内容单源，任何缩放档位自动居中（药丸留白也随缩放等比）。

**级联健壮性**（诊断双 App 实例时暴露）：`theme.LISTENERS` 里 Sidebar/StatBar 的
回调销毁时不摘除，`set_zoom` 级联会打在已销毁控件上抛 TclError、中断整个级联
（其余控件停在半更新状态）。修：两者 <Destroy> 时摘除自己（与 RoundEntry/
RoundButton/TabBar/StatCard 同款）；`set_zoom` 的监听循环对 TclError 容错跳过
（与 motion.Spring 同款防线），单个控件销毁不再拖垮整批重绘。

- 门禁 593 OK（skipped=3）；`selftest --golden` 92 条全 PASS；
  侧边栏 6 条命中测试全过；`packaging/build.bat` 流程重建 dist 产物，
  `verify.py` 五查（版本/init/golden/_wipe/GUI）全过——**用户侧验证请用新
  dist/kv.exe 或 `python kv_gui.py`，旧 exe 不含修复**。

## 2026-10-19 · GUI 细节打磨批（用户反馈「抠细节：图标、框大小、便捷性、丝滑度」）

**图标（icons.py）**：线宽原是固定像素（主 1.9/细 1.4），坐标随 size 缩放而
线宽不缩——高缩放下图标相对变「瘦」，品牌盾徽（0.94 格）比导航图标（1.7 格）
细近一半。改：线宽按设计网格基准随 size 等比（`_sw`，size=16 基准，主 1.7/
细 1.25，size=18 时与旧观感一致）；盾徽改细节线基准（34px 下 2.0→2.7px，
不与粗标题抢重但不再单薄）；层叠图标墨迹重心原偏下 0.85 格，调正；
导航图标 18→20px（42px 行内墨迹占比 ~31%→~35%，更醒目）。

**框大小（theme.py）**：RoundButton 高度 = max(CONTROL_HEIGHT, 行高+2×BTN_PAD_Y)，
BTN_PAD_Y=12 时按钮 47px、RoundEntry/TabBar 42px——工具栏里按钮比输入框高出
5px。BTN_PAD_Y 12→9（行高 23+18=41 < 42），全缩放档位按钮与输入框同高。
页脚 Ctrl 键帽直角矩形 → 圆角胶囊（与全应用 pill 语言一致）。

**丝滑（sidebar.py / app.py）**：行 hover 原是瞬时换色（且色块会盖住行内
图标——旧实现漏抬图标，只抬了文字），改为 4 帧 ~64ms 淡入淡出（hover 高频
交互走快档，与按钮 8 帧同语言；底色从该行渐变色起步平滑过渡，离开从当前色
续变不跳变；图标打 tag 与文字一起抬到色块之上）；页面滑入距离 18px → sp(18)
随缩放。

**便捷性**：N 键（工具提示一直承诺但从未绑定）真绑定——App 级 `<Key-n>`，
焦点在 Entry/Text/Combobox 等输入控件时让位给打字；F5 从树级上收 App 全局
（refresh/on_show 优先，树级绑定移除避免同键双触发）；搜索框 Esc 一键清空；
搜索键入防抖 120ms（每击键同步全表重建在多记录时卡手）；tooltip 字体/内距
随缩放（原硬编码 9pt 固定内距），连续悬停免 600ms 延迟（距上个气泡 <0.5s
立即显示，逐个扫按钮看提示的节奏不被打断）。

- 门禁 594 OK（593 + App 快捷键接线 1 条）；`selftest --golden` 92 条全 PASS；
  GUI 契约测试 22 条全过（F5 上收后树级断言同步更新）；dist 重建 + 五查全过。

## 2026-10-19 · 长文案换行随页面/缩放重排（用户反馈「这个文字没有随页面变化」）

安全页备份说明、过期检查结果、详情弹窗的值字段、空态提示都在用**固定
wraplength**（720/640/380/520 物理px）——窗口拉宽不重排、缩放后与字号失配，
用户截图里的备份说明就是卡死在 720px 不跟标签页宽。

- **widgets.track_wraplength(label, base, reserve=, anchor=)**：容器 Configure
  时把换行宽收到 `min(sp(base), 容器宽 - reserve)`——base 是可读性上限
  （逻辑px 随缩放），窄窗跟随、宽窗封顶；布局未完成（宽 <sp(280)）不动，
  防首帧抖动。reserve 支持实时测量（如详情弹窗按列 0 标签实际宽垫底）。
- 接入四处：备份说明（720）、过期检查（640，预留同排两按钮宽）、详情弹窗
  值字段（380→base 560，宽弹窗里 sha256 不再浪费一半宽度）、空态提示
  （520）。
- 顺带：secrets_states 的 `_px` 原来只乘 SCALE 不乘 ZOOM——高 UI 缩放下
  空态卡尺寸不跟字号长（与侧边栏居中同源的病），统一改 `theme.sp`。
- 新增 2 条契约测试（跟随容器宽 / reserve 预留；withdrawn 窗口容器宽恒 1，
  用例内 deiconify）；门禁 596 OK、金标 92 全 PASS、dist 重建五查全过。
- 实测：900px 窗口换行宽 673（跟标签页），1300px 窗口回到 720 上限。

## 2026-10-20 · 专业感打磨批（用户反馈「增加细节，整体界面更专业」）

**模态对话框现代化（FormDialog）**：原是直角 ttk.Entry + 左置双按钮，与全应用
圆角语言脱节，且无键盘路径。重建：内容区白纸 + 标题行（Heading 字号复述窗口
标题）+ RoundEntry 字段（口令型 show="*"）+ 取消/确定（主色右置）；**Enter 提交
/ Esc 取消**；打开时焦点落首字段，带 initial 值时**自动全选**——重命名/改标签
直接输入即覆盖。grab_set 对不可视父窗 TclError 静默（测试环境合法场景）。

**长操作忙碌反馈（security.py + RoundButton.set_enabled）**：扫描/加载审计/
清除预览与执行走 run_async 期间按钮**置灰防连点**（此前连点会并发跑两趟扫描），
完成/失败恢复并回写状态栏；RoundButton 新增禁用态——文字退灰、accent 主按钮
描边同步退灰、无 hover/按压反馈、光标还原 arrow。

**滚动条悬浮（ScrollPage + DataTable）**：自绘细滚动条从 pack/grid 改 **place
悬浮**在内容右缘——出现/消失不再挤压内容宽（pack 时代每次溢出切换内容横向
跳 ~8px）；SlimScrollbar 增 bg 参数融入所在底色。

**表格细节（DataTable）**：空表居中「暂无记录」幽灵提示（place 悬浮，插入/清空
自动切换）；扫描表行号、审计表 ID 列右对齐（数字列右对齐是表格专业性的基准）。

**小件**：侧边栏页脚 Ctrl+K 提示可点击直达命令面板（提示本来就是「可点的视觉
暗示」，现在言副其实，光标裁决收拢到 _on_motion 单点）；密钥详情弹窗 minsize
520×420（换行宽随窗宽收缩但不再挤碎）。

**测试卫生（关键根因修复）**：`_GuiTestCase` 原来从不销毁 root——首个 root 泄漏
成 `tk._default_root`，后续测试无 master 的 StringVar/Font 全落到旧解释器，与
当前控件**跨 interp 失联**（var 读不到值、选区失效，新对话框测试曾因此全挂）。
补 tearDown 销毁；新增 7 条契约（禁用态、空态幽灵、对话框接线/提交/取消/全选），
GUI 29 条全过。

- 门禁 601 OK（596 + 7 新增 - 2 合并）；`selftest --golden` 92 条全 PASS；
  对话框/忙碌态截图目检；dist 重建五查全过。

## 2026-10-20 · 选中涟漪 + 页面视差过渡（用户拍板上次的两个建议方向）

**表格选中涟漪（DataTable）**：`<<TreeviewSelect>>`（按下即触发）给选中行打
`sel-pulse` tag，底色从比选中色深一档的 `#c9d7ff` 4 帧（~64ms）退回
`TABLE_SELECT_BG` 后摘 tag——点击「被听见」的按压反馈。tag 级动画只动被选行
不碰全局样式；连点重定向（取消在飞帧从头播）；与 hover tag 互不干扰。

**页面视差过渡（app._slide_in）**：tkinter 控件树不支持逐像素透明度（alpha
只对整个 Toplevel 生效），「内容淡入」以**视差交叉滑动**替代——新页从右
sp(18) 滑入的同时旧页向左微退 sp(8)，新页左缘露出的条带是旧页在退场，
观感是两页互相推移而非硬切。弹簧复用同一注册表、可中断重定向（快速连切
不跳变：旧页被再次显示时 jump 回场）；首显仍无动画。

- 门禁 603 OK（601 + 涟漪生命周期/视差目标 2 条）；金标 92 全 PASS；
  GUI 契约 31 条全过；dist 重建五查全过。

## 2026-11-01 · KV-04 收尾 + 仓库卫生三件（周报 2026-11-01 待办清账）

- **KV-04（P1，上轮挂账③清账）**：选中涟漪的帧时钟提为 `DataTable.PULSE_FRAME_MS`
  类常量，测试注入 1ms 并改**完成态轮询**（sel-pulse tag 退场即过，2s 超时只作兜底）
  ——固定 sleep 预算（8×30ms 对动画 4×16ms）是结构性 flaky：套件高载时 Tcl timer
  派发延迟可吃掉余量。注入后收敛由动画完成态决定，与 wall-clock 余量解耦。
- **KV-08（P3）**：补 `.gitattributes`（`* text=auto eol=lf` + `*.bat eol=crlf` +
  位图 `-text`）——索引本就全 LF，工作树零 churn，提交侧 LF→CRLF 警告随之消失。
- **KV-09（P3）**：`kv/commands/m4.py` → `maintenance.py`（内容是 scan/audit/expire/purge，
  与「M4」里程碑已无关）；cli.py 的 `_m4_parsers` → `_maintenance_parsers`。
- **KV-10（P3，轻量收口）**：`theme.detach()` 单点收口 LISTENERS 摘除样板
  （7 处 if-in-remove）；完整 mixin 不做——跨 6 个控件类换方法名，收益配不上回归面。
- 门禁：全量两遍 `Ran 603, OK (skipped=2~3)`；`selftest --golden` 92 PASS 不受影响。

## 2026-11-01 · UI/动画强化批（周报目标：强化 UI 与动画设计，动效口径按 Emil 规则）

先按频率闸门给全部既有动效过筛（结论记录在案，防止将来加错）：命令面板开关
**永不动画**（键盘 100+/天，现状正确）；表格行 hover 与 RoundEntry 聚焦维持瞬时
（高频档，"要么没有要么不可察觉"）；侧边栏指示条/标签 pill 弹簧、页面视差、按钮
8 帧 hover、涟漪 4 帧全部在预算内，保持不动。本轮只补四处缺口 + 一处视觉断裂：

- **动效令牌单源（motion.py）**：`FRAME_MS=16` 帧时钟、`ease_out` 二次缓出、
  `fade_in_window`（Toplevel -alpha，ms/steps 可注入供测试按完成态轮询）——
  模态框/详情窗/明文窗/悬浮气泡是 tkinter 里仅有的可淡入表面。
- **四处生硬弹出 → 淡入**：模态 FormDialog 与详情窗/明文窗 150ms（模态档 200-500ms
  的干脆下限）、tooltip 64ms（数十次/天档只许 near-imperceptible）。
- **TabBar hover 言副其实**：文档一直写「hover 渐变」实现却是瞬时换色——补 3 帧
  渐变（代际令牌防叠加，选中项/重排即停手），与按钮/侧边栏同语言。
- **空态惊喜预算**：空态插图 0.85→1 缓出 6 帧入场（rare/first-time 层级才允许）；
  已在屏上（空库反复刷新）不重播。
- **按钮按压触感**：按下整块内缩 1px（tkinter 无 transform，内缩是 scale(0.97)
  的替代），反馈仍在 pointer-down 那一刻；忙碌置灰打断按住态时一并还原。
- **深色标题栏（kv_gui.py shim）**：DWM DWMWA_USE_IMMERSIVE_DARK_MODE（20/19 双取值
  best-effort）——深墨蓝侧边栏配系统白标题栏是界面最刺眼的断裂。10-06 曾把这段
  写进 kv/gui/ 被 test_arch 的 ctypes 隔离门禁正确拦下；本次放在规则允许的入口
  shim（与 DPI 感知同位置），`launch(on_window=…)` 回调挂入。
- **顺带修复（截图目检发现）**：`RoundButton.set_text` 只重绘不重测——空态卡三个
  按钮以空文案建出 42px 宽，文案互相叠压被裁（实测截图「化 V/看设置/解捕」）；
  补 `_measure_size()` 后按文案定宽。
- 测试 +4：fade_in_window 完成态轮询、TabBar hover 渐变/重定向/选中项豁免、
  空态插图入场收敛。六页 + 对话框 + 详情 + 面板 PrintWindow 截图逐张目检
  （深色标题栏生效、空态按钮修复、淡入收尾 alpha=1、面板保持瞬时）。
- 门禁：`Ran 607, OK (skipped=2~3)`；test_arch 行数预算拦过一次（widgets.py 461 超
  460，注释收敛后回到预算内）。

## 2026-11-01 · TOTP 支持 + 剪贴板倒计时（周报功能候选 #8）

- **TOTP（RFC 6238，纯 stdlib）**：`kv/ops/totp.py`——hmac/sha1/base64/struct 实现，
  SHA1/6 位/30 秒兼容基线，`generate` 留 digits 给 RFC 附录 B 官方向量（8 位口径
  6 个向量全过）。设计定位：TOTP 是对值的**解释**而不是存储类别——schema 的 kind
  是封闭 CHECK 枚举，为它开新 kind 需要表重建迁移，代价配不上；种子能按 base32
  解析（宽容大小写/空格/连字符/补 padding）就出码，否则明确报错。
  - CLI：`kv totp <name>`（store.py，验证码 30 秒失效不算明文，直接上终端）。
  - GUI：密钥页右键菜单「TOTP 验证码」→ 活码窗（`kv/gui/totp_window.py`）——
    大号等宽验证码 + 倒计时环（Canvas arc 画剩余比例，剩 5s 转琥珀是状态指示
    不是装饰）+ 复制按钮（走剪贴板排除历史通道）。种子经 reveal 窗口取一次
    （即开即归零、顺手留审计），窗口销毁即停表。
- **剪贴板倒计时（GUI）**：复制后状态栏从静态的「30 秒后自动擦除」变成逐秒
  倒数，到点报告时点；重复制重置。实际擦除仍由分离 helper 按指纹执行——
  倒计时只是把既有的兑现过程变成看得见的（helper 换内容即跳过的语义不变）。
- 中途纠偏（诚实记录）：最初把 totp 加进 masking.KINDS 走 kind 路线，
  `schema.sql` 的 CHECK 约束当场拦下（IntegrityError）——kind 入库会砸掉
  既有 vault 的兼容性，回退为「值的解释」设计；fallback_verdict 的 --kind
  透传实验也一并回退，兜底判定维持原状。
- 测试 +12（RFC 向量、种子解析、CLI 端到端/错误路径、活码面板固定时钟、
  倒计时逐秒/重置）；test_arch 预算拦截三次（secrets.py 477/462、cli 函数 64
  ——TOTP 入口逻辑移入 totp_window、totp parser 独立成组后全部回预算内）。
  TOTP 活码窗截图目检（倒计时环/大码/复制按钮排版正确）。
- 门禁：`Ran 619, OK (skipped=2~3)`；金标 92 PASS 不受影响。

## 2026-11-01 · v0.2.0 发布收口（版本、CI、发布产物）

- **版本 `0.1.0` → `0.2.0`**（单源 `kv/__init__.py`）：本版累计 = KV 待办清账批 +
  UI/动画强化批 + TOTP/剪贴板倒计时批，功能面与 UI 都有实质变化，升 minor。
- **CI 接入（P3-2 清账）**：`.github/workflows/ci.yml`——windows-latest（DPAPI 绑定
  Windows，门禁只在这上有意义）跑全量 unittest + 金标；runner 无 vault 时闸门记录
  按既有设计跳过（`_record_gate` 的 warn 路径）。docs/GIT.md「CI 与 PR」同步改口。
- **数字对账**：README/TESTING/AGENTS 的 603 → 619、源文件 110/112 → 116 个 `.py`
  （+kv/ops/totp.py、kv/gui/totp_window.py、tests/test_ops_totp.py、
  tests/test_gui_totp.py）、21 667 行；AGENTS 版本行与 CI 行同步。
- 发布产物：`packaging/build.bat` 重建 dist/kv.exe 与 kv-cli.exe（PyInstaller 6.22.3），
  `packaging/verify.py` 五查（版本/init/golden/_wipe/GUI）全过。
- 门禁：全量两遍 `Ran 619, OK`；金标 92 PASS；check_docs 阻断 0 提醒 0。

## 2026-11-02 · 浏览器扩展批1：原生消息宿主 kv/host/（扩展 + Python 宿主架构）

- **可行性结论**：纯浏览器插件不可行——Python/DPAPI/SQLite/审计链进不了浏览器
  沙箱，重写等于放弃现有 core；采用 1Password/Bitwarden 同款的「MV3 扩展 +
  native messaging 宿主」架构：扩展管事件与界面，加密/存储/审计全留本机 vault。
- **新增 `kv/host/`**（入口 shim `kv_host.py`，架构决策详见 ARCHITECTURE.md 末节）：
  - `protocol.py`：native messaging 帧协议（4 字节小端长度 + UTF-8 JSON），
    入站 4 MB 闸、出站 1 MB 拒发；零长帧显式报错。
  - `service.py`：ping/capture/approve/reject/list/copy 六请求分发。捕获 =
    pipeline.parse + resolve（第 5 级信号用页面标题，与 watch 的窗口标题同语义）
    → 全量 spool 进 inbox（静息态只有 DPAPI 密文，页面 URL 随行 context_json，
    approve 时落 source_url 列）；promptable 口径与 `PendingItem.auto_acceptable`
    一致——「歧义绝不猜厂商」跨端不变。列表只出掩码行；复制走 copy_secret
    （排除 Win+V 历史 + 30s 自动擦除）。协议通道上任何异常都折成 {"error"} 响应。
  - `app.py`：stdio 主循环（tty 直跑拒绝 + vault 未就绪降级到 ping-only）；
    `registry.py`/`install.py`：manifest 写 `%LOCALAPPDATA%\KeyVault\host\`、
    Chrome/Edge 各写 HKCU 注册键（不提权）；`kv host install/uninstall/status`
    三个 CLI 命令（commands/host.py，dev 态注册需 `--host-path` 显式指定）。
- **配套**：`kv/ops/review.py` 的 `spool()` 增加可选 `context` 参数（并进
  context_json，向后兼容）；`kv_host.py` shim 承接冻结态 `_wipe` 分发与
  `--version`（与 kv_gui 的 `_wipe` 对子并列，spawn_helper 不改）。
- 测试 +34（帧协议往返/坏帧、捕获→spool→approve/reject 全链、去重三态
  pending/already_pending/existing、掩码断言、CLI 安装/卸载/状态 + 内存注册表
  替身、扩展 ID 校验）；真实 `kv host install` 冒烟通过后即卸载还原注册表。
- 门禁：全量 unittest 绿 + 金标 92 PASS + check_docs 零阻断；test_arch 全过
  （host 不 import gui/cli/ctypes、无 DML 字符串、行数预算内）。

## 2026-11-02 · 浏览器扩展批2：extension/ MV3 扩展（捕获提示条 + popup 搜索复制）

- **新增 `extension/` 一级目录**（板块说明四段齐备，目录说明/ARCHITECTURE 已登记）：
  - `manifest.json`（MV3）：权限只有 nativeMessaging；内容脚本 `<all_urls>`；
    Chrome/Edge 通用（同为 Chromium，native messaging 注册表路径不同但 manifest 相同）。
  - `content.js`：监听页面 copy/cut 事件（用户主动动作，clipboardData 即被复制
    内容，**不需要 clipboardRead 权限**，也读不到用户没复制的东西）→ 文本 +
    页面 URL/标题发宿主检测；**恰有一条 promptable 候选**才在页内弹提示条
    （Shadow DOM 隔离样式、全 textContent、异常就地吞掉——扩展绝不弄坏网页）。
    提示条 [存入] → approve（记录带页面 URL），[忽略] → reject；多条/低置信
    静默进桌面审批队列。页面级 20 条去重环防同文本重发。
  - `background.js` + `host-client.js`：SW 只做请求转发（零业务逻辑）；宿主
    连接用**每次操作一条短连接**——MV3 SW 会被浏览器回收，长连接保活不可靠，
    每次拉起宿主的百毫秒开销对复制/点击可接受；一切失败折成 {error} 响应。
  - `popup.html/css/js`：搜索已存密钥（宿主 list，掩码行）→ 点击复制（宿主
    copy_secret，30s 自动擦除）；状态点显示宿主连接与 vault 初始化状态。
  - `icons/icon.png` 与 packaging/icon.png 同源整包复用。
- **测试**：+2 e2e（`tests/test_host_e2e.py`，真子进程跑 kv_host.py shim，
  stdio 上 ping → capture → approve → list → copy 全链断言响应无明文、
  source_url 落进记录；坏帧终止 exit 1——subprocess 级按 TestSubprocess 模式）。
  扩展侧 JS 无 Python 门禁：4 个 JS 文件语法检查 + manifest JSON 校验，
  行为验证 = 浏览器加载扩展手工走查（README 有步骤）。
- 门禁：全量 unittest 绿 + check_docs 零阻断（extension/ 板块说明/目录说明/
  架构索引三处齐）。

## 2026-11-02 · 浏览器扩展批3 + v0.3.0 发布收口

- **打包**：`kv.spec` 三个独立 Analysis/PYZ/EXE——新增 `dist/kv-host.exe`
  （入口 `kv_host.py`，console=True：宿主必须继承标准句柄，windowed 冻结进程
  stdout 是 None，协议通道就没了）；datas 按 test_arch 对每个块的统一断言带上
  schema.sql + corpus。`build.bat` 产物文案同步（保持纯 ASCII）。
- **verify.py 五查 → 六查**：新增 host 查——`--version` 单源一致 + native
  messaging **真帧 ping 往返**（管道子进程，未初始化 vault 如实上报
  initialized=false 也算过——「浏览器拉起即报状态」是宿主契约）。
- **真机走查的边界（诚实记录）**：本会话的浏览器自动化通道（iab/cdp）不支持
  加载未打包扩展，扩展侧 JS 的行为验证 = 语法检查 + 代码评审 + 文档化手动
  三步（GET-START §4.8）；宿主侧全链已有真子进程 e2e（capture→approve→
  list→copy，含响应无明文断言）与打包后协议 ping 两道机器门禁。
- **v0.3.0**（单源 `kv/__init__.py`，manifest.json 同步 0.3.0）：本版 =
  浏览器扩展三件（宿主/扩展/打包）+ v0.2.0 的全部内容。数字对账：
  655 条测试、129 个 `.py`、23 023 行；README/TESTING/AGENTS 同步。
- 发布产物：`packaging/build.bat` 重建 dist 三件（kv.exe / kv-cli.exe /
  kv-host.exe），`verify.py` 六查全过。
- 门禁：全量两遍 `Ran 659, OK`；金标 92 PASS；check_docs 零阻断。

## 2026-11-03 · Browser_extension 独立浏览器插件版（五批，v0.3.0）

- **定位**：`Browser_extension/` = **独立自包含**的浏览器插件版（用户点名重开发）——
  无桌面软件、无 Python 宿主；与 `extension/`（桌面伴侣版，native messaging 连
  本机 vault）定位不同、共存。已拍板：主密码 + 自动锁定（PBKDF2-SHA256 600k →
  AES-GCM-256，派生密钥只存 chrome.storage.session）；v1 全功能（核心捕获/管理 +
  TOTP + 轮换/去重/批量导入 + 备份/审计/仪表盘）；与桌面 vault 完全独立。
- **批1 检测大脑**（`src/core/`，1:1 移植 `kv/detect` + `kv/parse`）：24 条规则
  表与歧义推导、22 类占位符闸门（连接串豁免顺序陷阱保真）、六解析器 + pipeline
  去重、七级判定（needs_context 拒绝猜测 / context 弱证据 / 标题形态收窄）、掩码
  三态、纠正规则五类。**差分 parity：同批 21 候选过 Python 原版与 JS 移植，判定
  逐一相同**；sha256（同步，仅检测去重用）与 node:crypto 对拍。
- **批2 vault 层**（`src/vault/`）：crypto（WebCrypto，派生密钥走会话内存，
  keyStore 注入可测）、db.js（IndexedDB）与 memory-backend.js（同一接口的测试
  替身）、store.js（saveops 语义：去重合并/命名冲突/rotate 旧指纹 revoked/
  审计 hash 串链 + 伪造检出/importText/过期/统计/备份 KVBE1 口令独立）、
  totp.js（RFC 6238）。修出三个移植真 bug：nextSecretId 不自增覆盖记录、
  fpPut 缺更新语义、parseSeed 误用 base64 atob（应 RFC 4648 base32）。
- **批3 扩展骨架**：manifest（clipboardWrite/alarms/offscreen/storage）、SW
  （无状态设计：[存入] 回传原文重新检测——MV3 SW 可回收；自动锁定；TTL 擦除
  经 offscreen，页面 copy 事件 = 剪贴板已换新 → 取消在飞擦除，桌面指纹语义的
  浏览器等价；promptable = trusted 且平台非 unknown，ambiguous 诚实落点也给
  存入出路——浏览器无桌面审批队列）、content.js（Shadow DOM 提示条）、popup
  三态（设主密码/解锁/搜索复制）。
- **批4 options 管理页**：仪表盘、密钥表格 + 详情抽屉（TOTP 活码倒计时/指纹
  历史/轮换/吊销/重命名/设平台+记住纠正/删除）、审计列表（链完整性标示）、
  批量导入、加密备份导出/恢复、设置（自动锁定/捕获开关/忽略站点/清库）。
  全 textContent，无 innerHTML。
- 测试与门禁：node --test 61 条全绿（`npm test`）；全部 JS node --check；
  主仓 659 条 Python 门禁不受影响（独立目录，无 Python）；check_docs 零阻断
  （README 四段 + 子目录节 + 目录说明三处 + 根 README 能力表 + extension/README
  互相指引）。真机走查步骤在 Browser_extension/README（浏览器内行为 = 手动
  三步，与上一扩展版同口径——自动化通道不支持加载未打包扩展）。
