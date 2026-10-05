# kv — 本地密钥保管箱

> 用途：给第一次打开这个仓库的人。看完知道它是什么、能不能解决你的问题、怎么跑起来。

kv 是一个纯 Python 实现的本地密钥保管 CLI 工具，用 Windows DPAPI 加密、SQLite 存储，零外部依赖。它能存 API Key、检测泄露、自动剪贴板捕获、审计溯源，所有数据不出本机。

输入一条 `kv add ds sk-abc...`，值经 DPAPI 加密后写入 SQLite；`kv copy ds` 把明文放到剪贴板，30 秒后自动擦除；`kv scan ./repo` 扫描目录树，用已存指纹匹配泄露的密钥，输出文件名和行号。

**543 条测试 · 76 个源文件 · 13 837 行 Python（复核：`python -I -m unittest discover -s tests -t .`）**

## 30 秒跑通

```bash
# 在仓库根目录执行（Windows，Python 3.12+）
python kv.py init
python kv.py add mykey sk-abcdef1234567890
python kv.py list
```

看到 `1 条记录` 就说明装对了。想要完整步骤看 [上手手册](docs/GET-START.md)。

## 它能做什么

| 能力 | 怎么用 | 细节 |
|---|---|---|
| 存取密钥 | `kv add` / `kv show` / `kv reveal` / `kv rm` | [kv/commands/store.py](kv/commands/store.py) |
| 桌面 GUI | `python kv_gui.py` 或 `dist/kv.exe` | [kv/gui/](kv/gui/) |
| 泄露扫描 | `kv scan <目录>` | [kv/ops/scan.py](kv/ops/scan.py) |
| 剪贴板捕获 | `kv copy` / `kv watch` | [kv/capture/](kv/capture/) |
| 密钥轮换 | `kv rotate` / `kv revoke` | [kv/ops/rotate.py](kv/ops/rotate.py) |
| 批量摄入 | `kv import` / `kv use` | [kv/ops/importer.py](kv/ops/importer.py) |
| 审计溯源 | `kv audit` | [kv/core/audit.py](kv/core/audit.py) |
| 过期/清除 | `kv expire` / `kv purge` | [kv/ops/expiry.py](kv/ops/expiry.py)、[kv/ops/purge.py](kv/ops/purge.py) |
| 检测规则自测 | `kv selftest --golden` | [kv/selftest.py](kv/selftest.py) |

## 目录怎么分

| 目录 | 负责 |
|---|---|
| `kv/` | 主包：CLI、加密、检测、存储、操作 |
| `tests/` | 测试套件：543 条用例 + 金标语料 |

逐个目录的说明见各目录下的 `README.md`。

## 已知做不到什么

- 仅支持 Windows —— DPAPI 绑定当前用户，Linux/macOS 无法运行。
- 不支持多用户共享 —— 每个 vault 绑定一个 Windows 账户，无法跨机器同步。
- 不支持硬件密钥（YubiKey 等）—— 加密完全依赖 DPAPI。
- 大量密钥（>10 000）未经性能测试 —— scan 对每个文件遍历全部指纹，规模大时可能变慢。

## 环境要求

Python 3.12+（stdlib only，零外部依赖）；Windows 10/11（DPAPI）；不需要网络；不需要外部服务或密钥。

## 许可与引用

私有项目，未发布许可证。

---

维护：AI 改动本仓库前请读 [AGENTS.md](AGENTS.md)。
