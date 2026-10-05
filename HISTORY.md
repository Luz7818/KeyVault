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
