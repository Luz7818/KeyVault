# kv Git 规范

> 用途：给提交 kv 代码的人。2026-10-05 起本仓启用版本控制，分支、入库边界、message 风格都在这里。

## 分支与提交策略

- 单 `main` 分支，本地开发直接提交（私有项目，无远端时推的是本机）。
- **一批一提交**：一个提交 = 一个可回退单元；提交前跑 [TESTING.md](TESTING.md) 的全量 unittest。

## 必须入库 / 禁止上传

| 判定 | 规则 |
|---|---|
| 必须入库 | `kv/`、`tests/`（含金标语料）、`docs/`、根 shim（`kv.py`/`kv_gui.py`/`runtests.py`）、构建配置（`kv.spec`/`build.bat`）、九件文档 |
| 禁止上传 | `build/`、`dist/`（PyInstaller 产物，`.docsignore`/`.gitignore` 已挡）；`__pycache__/`、`.pytest_cache/` |
| **绝对禁止** | **任何 vault 数据（`.kv/` 数据库）与明文密钥**——密钥本体只活在 DPAPI 加密的 vault 里，永不入库 |

## commit message

- 风格与工作区其他仓一致：`<type>: 中文一句话`，type 取 `feat / fix / docs / refactor / test / chore`。

## CI 与 PR

- 无 CI（stdlib 零依赖项目的选择）；门禁 = 全量 unittest + `selftest --golden`。
- 无 PR 流程，直接提交 `main`。
