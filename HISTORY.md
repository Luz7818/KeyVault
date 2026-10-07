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
