# tests/ —— 测试套件：548 条用例 + 金标语料

> 用途：说明本目录在整个项目里管哪一段,以及每个文件干什么。

tests 包含 kv 项目的全部测试：单元测试、集成测试、CLI 端到端测试，以及检测规则的金标语料。所有测试用 stdlib `unittest`，不需要 pytest（但兼容）。测试替身（PlaintextProtector、FakeClipboard、FakeClock）集中在 `fakes.py`，不依赖 Windows DPAPI。

## 文件清单

| 文件 | 干什么 | 备注 |
|---|---|---|
| `runtests.py` | 测试入口 shim：修 `python -I` 下 sys.path 缺失，支持按模块/用例名选用 | `python -I tests/runtests.py [模块[.类.方法]] [-v]` |
| `__init__.py` | 包标记 | — |
| `fakes.py` | 测试替身：PlaintextProtector、FakeClipboard、FakeClock | 被所有测试引用 |
| `test_arch.py` | 架构约束测试（函数长度、encoding= 强制等） | 门禁 |
| `test_cli_m0.py` | M0 验收：init/add/show/list/rm/reveal 端到端 | — |
| `test_cli_m2.py` | M2 验收：copy/watch/review/fix/rename/tag 端到端 | — |
| `test_cli_m3.py` | M3 验收：import/use/rotate/revoke 端到端 | — |
| `test_cli_m4.py` | M4 验收：scan/audit/expire/purge 端到端 | — |
| `test_console.py` | console 模块：表格输出、掩码渲染、用户交互 | — |
| `test_capture_watch.py` | watch 循环：自动检测、手动操作、spool 模式 | — |
| `test_capture_wipe.py` | 剪贴板擦除：定时、sweep、wipe helper | — |
| `test_detect_resolve.py` | 检测解析：多阶段匹配、纠正规则、歧义处理 | — |
| `test_errors_redaction.py` | 错误消息脱敏：确保异常不泄露明文值 | — |
| `test_masking.py` | 掩码计算：fingerprint、split_mask、preview | — |
| `test_ops_fix.py` | fix 命令：未识别平台、纠正规则管理 | — |
| `test_ops_review.py` | review 命令：inbox 处理、平台选择 | — |
| `test_parse_pipeline.py` | 解析管线：dotenv/AWS CSV/curl/JSON/bare | — |
| `test_parse_placeholders.py` | 占位符替换：环境变量、条件展开 | — |
| `test_paths.py` | 路径解析：vault 定位、云同步检测、安全检查 | — |
| `test_selftest_golden.py` | 金标自测：corpus 语料匹配、gate 检查 | — |
| `test_vault_store.py` | vault 存储层：加密写入、读取、去重 | — |
| `test_windows_dpapi.py` | DPAPI 封装：protect/unprotect/self_test | 仅 Windows 运行 |

## 子目录

| 子目录 | 负责 |
|---|---|
| `corpus/` | 检测规则金标语料（JSONL 格式），`kv selftest --golden` 加载；下钻说明见下节 |

## corpus/ —— 金标语料（下钻说明）

一行一个 JSON 用例，由 `kv selftest --golden` 跑。这是检测器的**存在理由**：
本项目的前身出过三次「检测器静默失效却输出全仓干净」，所以任何改动都要先过这里。

**所有 fixture 值都是合成的**：由 `kv/selftest.py` 的 `SYNTHETIC` 表在跑之前展开，
语料文件里只有占位符（`<48位合成>` / `<32hex>` / `<64hex>` / `<36位字母数字>` 等，
展开规则全表见 `kv/selftest.py`），不来自任何真实凭据，所以**可以安全提交、可以安全打印**
—— `--golden` 失败时打印用例也不构成泄漏。加一个新占位符要同时改 `SYNTHETIC` 和语料。

| 文件 | 管什么 |
|---|---|
| `01-shape.jsonl` | 值形态独有的平台，形态命中即可直接判定 |
| `02-context.jsonl` | 分级优先级：主机名 > 键名 > 形态 > 窗口标题 |
| `03-negative.jsonl` | **反例**：占位符、哈希形态、歧义必须如实报告而不是猜 |
| `04-structures.jsonl` | curl / JSON / AWS CSV / 整段 .env 的结构解析 |

用例字段：`id`、`input` 必填；`window_title` 可选，模拟前台窗口标题。断言字段都是可选的，
只断言写了的那些：`expect`（`accepted`/`rejected`）、`expect_reason`（配合 `rejected`：
`placeholder` / `not-a-secret` / `empty` / `too-long`）、`expect_count`（候选条数）、
`expect_platform` / `expect_platforms`（第一/全部候选平台 id）、`expect_confidence`
（`manual`…`none` 六档）、`expect_source`（`correction`/`hostname`/`keyname`/`shape`/`window`/`none`）、
`expect_kind`（`token`/`json`/`pem`/`pair`/`connstring`）、`expect_not`（这个平台不得出现）、
`expect_candidates_min`（歧义候选至少几个）、`expect_refused`（必须判 `none`，除非 `--force`）、
`expect_extra_has`（`extra` 里必须有的键）、`expect_pair`（两条候选共享 `pair_id`）。

跑法与防线：`kv selftest --golden` 全通过则把当前的 `TABLE_HASH` 记进 vault 的 `setting`；
之后 `kv scan` 会在「表变了而自检没重跑」时拒绝运行并退出码 5 —— 一个静默失效的检测器
比没有检测器更危险，因为它会说「全仓干净」。

## 和谁打交道

- **上游**：`kv/` 包的所有模块是被测对象
- **下游**：测试结果决定 CI 是否通过；`test_selftest_golden.py` 的结果决定 `kv scan` 的 gate 状态
- **改这里之后要跑**：`python -I -m unittest discover -s tests -t .`

## 别动

- `corpus/` 里的 JSONL 文件是金标语料，删改会导致 `selftest --golden` 失败，进而阻断 `kv scan`（gate 不过）。
- `fakes.py` 的 `PlaintextProtector` 被所有不依赖 DPAPI 的测试使用，改接口要全局替换。
- `test_arch.py` 强制架构约束（函数 ≤60 行、`open()` 必须带 `encoding=`），放松会引入违规代码。
