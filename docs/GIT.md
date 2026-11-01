# kv Git 规范

> 用途：给提交 kv 代码的人。2026-10-05 起本仓启用版本控制，分支、入库边界、message 风格都在这里。

## 分支与提交策略

- 单 `main` 分支，推 `main` 即同步 GitHub 远端（公开仓库 `github.com/Luz7818/KeyVault`）。
- **一批一提交**：一个提交 = 一个可回退单元；提交前跑 [TESTING.md](TESTING.md) 的全量 unittest。

## 必须入库 / 禁止上传

| 判定 | 规则 |
|---|---|
| 必须入库 | `kv/`、`tests/`（含金标语料与 `runtests.py` 入口 shim）、`docs/`、根 shim（`kv.py`/`kv_gui.py`）、打包链路（`packaging/`）、九件文档 |
| 禁止上传 | `build/`、`dist/`（PyInstaller 产物，`.docsignore`/`.gitignore` 已挡）；`__pycache__/`、`.pytest_cache/` |
| **绝对禁止** | **任何 vault 数据（`.kv/` 数据库）与明文密钥**——密钥本体只活在 DPAPI 加密的 vault 里，永不入库 |

## commit message

- 风格与工作区其他仓一致：`<type>: 中文一句话`，type 取 `feat / fix / docs / refactor / test / chore`。

## CI 与 PR

- CI：GitHub Actions（`.github/workflows/ci.yml`，2026-11-01 接入）——windows-latest
  上跑全量 unittest + `selftest --golden`，push main 与 PR 触发。DPAPI 绑定 Windows，
  门禁只在 windows-latest 有意义。
- 无 PR 流程约束（直接提交 `main`），PR 路径是为外部贡献保留的。
