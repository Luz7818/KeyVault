# packaging/ —— Windows 打包链路

> 用途：说明 kv.exe / kv-cli.exe 怎么打包、配置在哪、产物落在哪。改打包行为只看这一处，不跨目录对账。

## 文件清单

| 文件 | 干什么 | 备注 |
|---|---|---|
| `build.bat` | 一键构建：装 PyInstaller → 按本目录 `kv.spec` 打包 → `verify.py` 校验 → 提示产物位置 | **纯 ASCII**（见「别动」）；双击或在仓库根运行均可；产物固定在 `dist/` |
| `kv.spec` | PyInstaller 配置：两个入口 —— `kv_gui.py`（GUI）+ `kv.py`（CLI），各自独立 Analysis；`datas` 必须带 `kv/core/schema.sql` 与 `tests/corpus`（冻结态按 `__file__` 相对路径找它们，`tests/test_arch.py` 有断言守着） | 产物 `kv.exe`（console=False）与 `kv-cli.exe`（console=True） |
| `verify.py` | 打包后校验：版本号一致、`init` 可用（schema 进包）、`selftest --golden` 通过（语料进包）、`_wipe` 冻结态短路、GUI 能启动；任一失败退出码 1 | 只回收校验时新起的 kv.exe，用户自己开着的绝不动 |

## 两个产物各是干什么的

| 产物 | 形态 | 说明 |
|---|---|---|
| `dist/kv.exe` | GUI（console=False） | 桌面主程序。同时兼职跑 `_wipe` 子命令：`kv_gui.py` 对 `_wipe` 开头的 argv 做 CLI 分发，`kv copy` 派出的擦除 helper 在冻结态就是它自己 |
| `dist/kv-cli.exe` | 命令行（console=True） | 完整 CLI 命令面（含 `export` / `restore` / `_wipe`）。入口是 `kv.py` shim |

## 和谁打交道

- **上游**：仓库根 `kv_gui.py`（GUI 入口，DPI/UTF-8 shim + 冻结态 `_wipe` 分发）、
  `kv.py`（CLI 入口 shim）、`kv/gui/`（被冻结的全部界面代码）、`kv/__init__.py` 的版本号。
  两者都需要 `kv.cli` 进 hiddenimports：GUI 侧为 `_wipe` 分发，CLI 侧为命令面。
- **下游**：`dist/kv.exe`、`dist/kv-cli.exe`（运行产物，不入库；`build/` 是中间产物）。
- **改这里之后要跑**：`packaging/build.bat` 打包后冒烟 —— `kv-cli.exe --version`、
  `kv-cli.exe list`（开库）、`kv.exe` 双击（开库 → 加一条密钥 → 扫描 → 关窗）、
  版本号与 `kv/__init__.py` 目视一致。

## 别动

- `kv.spec` 的两个入口路径（`../kv_gui.py`、`../kv.py`）与根 shim 是配套的：
  spec 在本目录、入口在根，挪动任何一侧要同步改另一侧。
- 两个 EXE 各自独立 `Analysis`/`PYZ`：复用 GUI 的 `a.scripts` 会让 `kv-cli.exe`
  启动 GUI 窗口，复用 CLI 的会让 `kv.exe` 丢掉界面。
- `kv.spec` 的 `datas` 少了 `kv/core/schema.sql` 或 `tests/corpus`，exe 会在
  **运行期**才炸（init 报「文件不存在」/ scan 被 golden 闸门永久拒绝）——
  `tests/test_arch.py::TestPackagingDataFiles` 与 `verify.py` 是两道防线。
- `build.bat` 保持**纯 ASCII**：实测中文行 + `chcp 65001` 的 cmd 在特定字节
  偏移处会吞掉下一行行首（164 字节的脚本即可复现，末行 `echo 不需要…` 的前
  半被吃）。中文说明留在本文件，构建脚本不要本地化。
- `--distpath dist --workpath build` 写死在 `build.bat`：产物路径是 README/目录说明引用的
  约定位置，别让它随打包时的当前目录漂移。
- `kv.exe` 的 `console=False` 是桌面交付约定：排查问题时临时改 True 可以，提交前必须改回；
  `kv-cli.exe` 反过来，必须是 `console=True`。
