# golden 语料

一行一个 JSON 用例，由 `kv selftest --golden` 跑。这是检测器的**存在理由**：
本项目的前身出过三次「检测器静默失效却输出全仓干净」，所以任何改动都要先过这里。

## 所有 fixture 值都是合成的

由 `kv/selftest.py` 的 `SYNTHETIC` 表在跑之前展开，语料文件里只有占位符。
不来自任何真实凭据，所以**可以安全提交、可以安全打印** —— `--golden` 失败时
打印用例也不构成泄漏。

| 占位符 | 展开成 |
|---|---|
| `<48位合成>` / `<48>` | `A` × 48 |
| `<32hex>` | `0123456789abcdef` × 2 |
| `<40hex>` | `0123456789abcdef` × 2 + `01234567`（git SHA-1 形态） |
| `<64hex>` | `0123456789abcdef` × 4（sha256 形态） |
| `<36位字母数字>` / `<36>` | `abcdefghij0123456789` × 2 取前 36 |
| `<20位字母数字>` / `<20>` | 同上取前 20 |
| `<40位字母数字>` | 同上取前 40 |
| `<16位大写>` | `ABCDEFGHIJKLMNOP` |
| `<24位>` | `aB3xK9mQ2pL7vR1sT4uW6y` |
| `<合成 200 字符 base64>` | `MIIEvA` + `A` × 194 |

加一个新占位符要同时改 `kv/selftest.py` 的 `SYNTHETIC` 和这张表。

## 文件划分

| 文件 | 管什么 |
|---|---|
| `01-shape.jsonl` | 值形态独有的平台，形态命中即可直接判定 |
| `02-context.jsonl` | 分级优先级：主机名 > 键名 > 形态 > 窗口标题 |
| `03-negative.jsonl` | **反例**：占位符、哈希形态、歧义必须如实报告而不是猜 |
| `04-structures.jsonl` | curl / JSON / AWS CSV / 整段 .env 的结构解析 |

## 用例字段

`id` `input` 必填。`window_title` 可选，模拟前台窗口标题。断言字段都是可选的，
只断言写了的那些：

| 字段 | 含义 |
|---|---|
| `expect` | `accepted` 或 `rejected` |
| `expect_reason` | 配合 `rejected`：`placeholder` / `not-a-secret` / `empty` / `too-long` |
| `expect_count` | 候选条数 |
| `expect_platform` | 第一条候选的平台 id |
| `expect_platforms` | 全部候选的平台 id（无序，内部排序后比） |
| `expect_confidence` | `manual` / `exact` / `high` / `medium` / `ambiguous` / `none` |
| `expect_source` | `correction` / `hostname` / `keyname` / `shape` / `window` / `none` |
| `expect_kind` | `token` / `json` / `pem` / `pair` / `connstring` |
| `expect_not` | 这个平台**不得**出现在结果里 |
| `expect_candidates_min` | 歧义候选至少几个 |
| `expect_refused` | true 表示必须判成 `none`（拒绝落盘，除非 `--force`） |
| `expect_extra_has` | `extra` 里必须有这个键 |
| `expect_pair` | true 表示两条候选共享一个 `pair_id` |

## 跑

```
kv selftest --golden
```

全通过则把当前的 `TABLE_HASH` 记进 vault 的 `setting`。之后 `kv scan` 会在
「表变了而自检没重跑」时拒绝运行并退出码 5 —— 一个静默失效的检测器比没有检测器
更危险，因为它会说「全仓干净」。
