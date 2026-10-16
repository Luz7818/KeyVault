# HISTORY —— 版本更新记录

> 用途：记录每次版本与规范变更的内容和缘由。只追加，禁止删除或改写既有条目；写错了就追加一条
> 更正。新条目写在文件末尾。当前版本 `0.1.0`（单源：`kv/__init__.py`）。

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
