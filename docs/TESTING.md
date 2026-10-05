# kv 测试规范

> 用途：给写测试和跑门禁的人与 AI。分层、运行命令、替身规范、改动后的验证都在这里；
> 测试数的事实口径在根目录 `AGENTS.md` 的「当前状态」。

## 测试分层

| 层 | 管什么 | 放哪 | 数量级 |
|---|---|---|---|
| 单元/集成 | 各命令与核心模块行为 | `tests/test_*.py` | 543 条 |
| 金标自测 | 检测规则对语料的判定 | `tests/corpus/*.jsonl` + `kv selftest --golden` | 语料驱动 |
| GUI | tkinter 界面 | 手工目检（无自动化） | —— |

## 运行命令

```bash
python -I -m unittest discover -s tests -t .        # 全量，预期 OK (skipped=2)
python kv.py selftest --golden                      # 金标，预期全过
python -I -m unittest tests.test_cli_m4 -v          # 单模块
```

注意用 `python -I`（隔离模式），避免本机环境变量干扰测试。

## 用例编写规范

- 用 `tests/fakes.py` 的替身：`PlaintextProtector`（不依赖 DPAPI）、`FakeClipboard`、
  `FakeClock`（可控时间）。**生产代码不许 import 测试模块**。
- 改 `tests/corpus/*.jsonl` 或 `kv/detect/` 的规则后，`selftest --golden` 若报不匹配，
  按提示更新 `golden_passed_for`——不许绕过 gate 直接跑 scan。
- 篡改审计链的测试要先 `DROP TRIGGER IF EXISTS audit_no_update`，测完让用例自己重建。

## gate 规则

`kv scan` 要求金标自测先通过，否则拒绝执行（`EXIT_GATE`）。这是防检测规则退化的闸门，
不要放宽。

## 改动后的验证

| 你动了 | 必须跑 |
|---|---|
| `kv/core/*.py`、`kv/ops/*.py`、`kv/commands/*.py`、`kv/gui/*.py` | 全量 unittest |
| `kv/detect/rules.py`、`kv/detect/resolve.py` | 全量 unittest + `selftest --golden` |
| `tests/corpus/*.jsonl` | `selftest --golden`（gate 会要求更新 `golden_passed_for`） |
| `kv/core/schema.sql` | 全量 unittest + 检查 `SCHEMA_VERSION` 是否需要递增 |
