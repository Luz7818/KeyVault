# packaging/ —— Windows 打包链路

> 用途：说明 kv.exe 怎么打包、配置在哪、产物落在哪。改打包行为只看这一处，不跨目录对账。

## 文件清单

| 文件 | 干什么 | 备注 |
|---|---|---|
| `build.bat` | 一键构建：装 PyInstaller → 按本目录 `kv.spec` 打包 → 提示产物位置 | 双击或在仓库根运行均可；先回仓库根再打包，产物固定在 `dist/kv.exe` |
| `kv.spec` | PyInstaller 配置：入口是仓库根 `kv_gui.py`（GUI shim），`kv.gui.pages.*` 六个页面模块进 hiddenimports | 产物单文件 `kv.exe`，`console=False` |

## 和谁打交道

- **上游**：仓库根 `kv_gui.py`（打包入口，DPI/UTF-8 shim）、`kv/gui/`（被冻结的全部界面
  代码）、`kv/__init__.py` 的版本号。
- **下游**：`dist/kv.exe`（运行产物，不入库；`build/` 是中间产物）。
- **改这里之后要跑**：`packaging/build.bat` 打包后双击 `dist/kv.exe` 冒烟一遍
  （开库 → 加一条密钥 → 扫描 → 关窗），版本号与 `kv/__init__.py` 目视一致。

## 别动

- `kv.spec` 的入口路径 `../kv_gui.py` 与 shim 在仓库根是配套的：spec 在本目录、入口在根，
  挪动任何一侧要同步改另一侧。
- `--distpath dist --workpath build` 写死在 `build.bat`：产物路径是 README/目录说明引用的
  约定位置，别让它随打包时的当前目录漂移。
- `console=False` 是桌面交付约定：排查问题时临时改 True 可以，提交前必须改回。
