# AGENTS.md —— 项目协作与代码开发规范（唯一权威入口）

> 用途：给 AI 编码助手与所有开发者。这里是规范入口与索引：目标、原则、流程、模块规则、
> 维护矩阵、阅读清单都在这份文件里。细则一律链接到对应文件，冲突时以细则文件为准并回改本文件。
> 被别的文档引用的事实（测试数、命令、路径）以本文件的「当前状态」为准，其他文档只链接不复述。

## 项目目标

- 定位：纯 Python（stdlib only）的本地密钥保管 CLI + GUI，Windows DPAPI 加密、SQLite 存储，
  支持密钥存取、泄露扫描、剪贴板捕获、审计溯源，数据不出本机。
- 核心功能：存取/展示/轮换/批量导入、目录泄露扫描、剪贴板自动捕获与擦除、审计链、金标自测门禁。
- 技术栈：Python 3.12+ stdlib、tkinter、PyInstaller 打包（复核：`python --version`）。
- 详情：[README.md](README.md)、[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## 开发原则

1. 正确性优先。
2. 可维护性优先。
3. 代码简洁、项目简洁。
4. 小步迭代。
5. 单模块开发。
6. 每个改动必须有明确设计与验收标准。
7. 禁止一次生成整个项目。
8. 禁止跳步开发。

执行口径：先想后写（假设与歧义先挑明）；最简优先（不加没要求的功能与抽象——本仓的铁律是
stdlib 零依赖）；外科手术式改动（不动无关代码，每行改动可追溯到需求）；目标驱动（先有可验证
判据再动手，宣称完成前先跑通 [docs/TESTING.md](docs/TESTING.md) 的门禁）。

## 开发流程

**分析 → 设计 → 实现 → 测试 → 文档更新 → Git提交 → 等待确认**。不得跳过任何阶段。

| 阶段 | 产出物 | 放行标准 |
|---|---|---|
| 分析 | 影响面清单（动哪些模块、是否触及 DML/schema/检测规则） | 影响面说全 |
| 设计 | 方案说明（数据/接口变化、回退方式） | 验收标准已定义；与更简方案比较过 |
| 实现 | 代码 | 只含设计内改动，符合 [docs/CODE-STYLE.md](docs/CODE-STYLE.md) |
| 测试 | 门禁结果 | [docs/TESTING.md](docs/TESTING.md) 全过 |
| 文档更新 | 受影响文档 diff | 维护矩阵逐项过完 |
| Git提交 | 提交 | 符合 [docs/GIT.md](docs/GIT.md)，一批一提交 |
| 等待确认 | —— | 等人确认后进入下一步 |

## 模块开发规则

- 一个智能体一次只开发一个模块；模块完成后才能进入下一模块。
- 如需同时开发，使用多个子智能体，每个子智能体同样一次只开发一个模块。

模块完成标准（全部满足才算完成）：

1. 功能完成：达到 [TODO.md](TODO.md) 中该任务的验收标准。
2. 测试通过：符合 [docs/TESTING.md](docs/TESTING.md)。
3. 最简原则：代码和项目架构都保持最简洁，无冗余抽象与重复实现。
4. [TODO.md](TODO.md) 更新：勾选完成项、明确下一项。
5. [HISTORY.md](HISTORY.md) 追加变更记录。
6. 受影响的 docs 更新（按需）。
7. [README.md](README.md) 更新（如有面向使用者的变化）。
8. Commit message 符合 [docs/GIT.md](docs/GIT.md)。

## 文档维护规则

| 事件 | 需更新 |
|---|---|
| 模块完成 | `TODO.md`、`HISTORY.md`、受影响 docs |
| 架构决策（schema 变更、新命令、新依赖、推翻既有决策） | `docs/ARCHITECTURE.md` + `HISTORY.md` 记录缘由 |
| 命令/入口/参数变化 | `README.md` / `docs/GET-START.md` / 对应子目录 README |
| 增删一级或二级目录 | 仓根 `目录说明.md` + 父目录 README 的子目录表 |
| 测试数/退出码/门禁变化 | 本文件「当前状态」 |
| 新对话/新任务开始 | 按下方阅读清单阅读 |

## 开发前阅读清单

每个新对话/新任务，按顺序阅读：

1. 本文件（`AGENTS.md`）
2. [TODO.md](TODO.md)
3. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
4. [docs/GET-START.md](docs/GET-START.md)
5. [HISTORY.md](HISTORY.md)
6. 与任务相关的 [docs/CODE-STYLE.md](docs/CODE-STYLE.md)、[docs/TESTING.md](docs/TESTING.md)、[docs/GIT.md](docs/GIT.md)

阅读完成后**不要写代码**：先做架构评审，输出——项目理解 / 核心模块 / 模块依赖关系 / 潜在风险 /
建议优化项 / 推荐开发顺序 / 是否发现架构问题——然后等待确认。

## Git 索引

- Git 规范：[docs/GIT.md](docs/GIT.md)（2026-10-05 建档；vault 数据永不入库；一批一提交）

## 当前状态

| 项 | 值 | 复核命令 |
|---|---|---|
| 测试 | `Ran 548, OK (skipped=2)`，0 失败（2026-10-05 实测） | `python -I -m unittest discover -s tests -t .` |
| 金标自测 | `selftest --golden` 全过 | `python kv.py selftest --golden` |
| 静态检查 | 无 lint 配置（有意） | — |
| CI | 无 | — |
| 运行时依赖 | 零（stdlib only） | — |
| Python | 3.12+ | `python --version` |
| 源文件 | 88 个 `.py` + 1 个 `.sql` | `find . -name "*.py" -not -path "*/__pycache__/*" \| wc -l` |
| 版本 | `0.1.0` / `SCHEMA_VERSION=1` | `python kv.py --version` |
| 版本控制 | 2026-10-05 起 git 建档（main） | `git log --oneline` |

## 已知坑（省下一次的调查时间）

- `bytes` 不可变——`unprotect()` 返回的明文要归零，先转 `bytearray` 再逐字节置 0。
- 中文路径 + Windows 控制台默认 cp936：一律走 `kv.py` shim（强制 UTF-8），交互编码由
  `console.setup_stdio()` 处理。
- `list_audit` 返回 `ORDER BY id DESC`：`events[0]` 是最新、`events[-1]` 是最旧。
- rotate 保留旧指纹的 `value_blob`——scan 靠它检测泄露的旧值，不要"顺手"归零。
- `kv scan` 有 gate：先 `selftest --golden` 通过，否则 `EXIT_GATE` 拒绝执行。
- 不要在 `repo.py` 以外写 DML（审计链会断）；不要删 `tests/corpus/` 金标语料；
  不要改 `schema.sql` 而不递增 `SCHEMA_VERSION`；不要引入外部依赖（stdlib only）。
- vault 数据（`.kv/`）永不入库——见 [docs/GIT.md](docs/GIT.md)。
