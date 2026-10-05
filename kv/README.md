# kv/ —— 主包：CLI、加密、检测、存储、操作

> 用途：说明本目录在整个项目里管哪一段,以及每个文件干什么。

kv 是项目的核心 Python 包。它接收 CLI 命令（来自 `kv.py` shim 或 `python -m kv`），通过 argparse 分发到各 commands 模块，再调用 ops/core/crypto 完成实际操作。所有数据经 DPAPI 加密后存入 SQLite，审计日志 append-only 带链式 hash。

## 文件清单

| 文件 | 干什么 | 备注 |
|---|---|---|
| `__init__.py` | 版本号 `0.1.0`、`SCHEMA_VERSION=1`、`PROGRAM="kv"` | 常量定义 |
| `__main__.py` | `python -m kv` 入口，调用 `cli.main()` | 入口 |
| `cli.py` | argparse 命令树 + dispatch | 入口，所有子命令在这里注册 |
| `console.py` | stdio 设置、掩码渲染、表格输出、用户交互 | 被所有命令引用 |
| `errors.py` | 自定义异常 + 退出码常量 | 被所有模块引用 |
| `model.py` | 数据类：Candidate、Verdict、SecretRow 等 | 跨模块数据载体 |
| `paths.py` | vault 路径解析、安全检查（云同步检测、git 排除） | 被 store/scan 引用 |
| `clock.py` | 时间抽象（可注入 FakeClock 用于测试） | 被测试引用 |
| `selftest.py` | 检测规则自测引擎 + golden 语料运行 | `kv selftest --golden` |

超过 30 个文件时,只列对外有接口的;其余写清命名规律,例如
`<其余:case_*.json,一个文件一条测试用例,由 runner 逐个加载>`。

## 子目录

| 子目录 | 负责 |
|---|---|
| `capture/` | 剪贴板读写、窗口标题捕获、watch 循环、wipe 擦除 |
| `commands/` | CLI 命令实现层（参数解析 → ops 调用 → 输出格式化） |
| `core/` | DB 连接、schema、vault 封装、审计链、repo（唯一 DML 层）、掩码计算 |
| `crypto/` | Windows DPAPI 封装（ctypes），protect/unprotect/self_test |
| `detect/` | 检测规则定义、多阶段解析、用户纠正、报告格式化 |
| `gui/` | tkinter 桌面 GUI（叶子模块），侧边栏导航 + 6 个功能页 |
| `ops/` | 操作命令：save、scan、rotate、import、inject、expiry、purge、fix、review |
| `parse/` | 输入解析：dotenv、AWS CSV、curl、JSON、bare 文本、pipeline 编排 |

## 和谁打交道

- **上游**：`kv.py`（CLI shim）、`kv_gui.py`（GUI shim）和 `python -m kv` 调用入口
- **下游**：所有命令最终操作 `vault/` 下的 SQLite 数据库和 `.kv/` 目录
- **改这里之后要跑**：`python -I -m unittest discover -s tests -t .`

## 别动

- `__init__.py` 里的 `SCHEMA_VERSION` 改了必须同步改 `core/schema.sql` 和迁移逻辑，否则 `check_version()` 拒绝启动。
- `errors.py` 里的退出码常量被测试断言引用，改名要全局搜索替换。
- `console.py` 的 `setup_stdio()` 处理 Windows cp936 → UTF-8 转换，删掉会导致中文路径乱码。
