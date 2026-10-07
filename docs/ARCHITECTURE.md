# kv 架构

> 用途：给要理解或改动 kv 结构的人。架构总览、目录结构（含根目录所有文件用途）、数据组织方式、
> 模块依赖关系都在这里。操作步骤在 `docs/GET-START.md`；数字口径在根目录 `AGENTS.md` 的「当前状态」。

## 架构总览

kv 是纯 Python（stdlib only）的本地密钥保管工具，Windows DPAPI 加密、SQLite 存储。运行形态两个：
CLI（`kv.py` → `kv/cli.py` 的 argparse 命令树）与桌面 GUI（`kv_gui.py` → `kv/gui/`，tkinter）。
分层：启动 shim → `kv/cli.py`（参数解析与 dispatch）→ `kv/commands/`（命令实现层）→
`kv/ops/`（业务操作）→ `kv/core/`（DB、schema、vault、审计链，**唯一 DML 层**）。

## 目录结构

```
KeyVault/
├── kv/                主包（capture/ commands/ core/ crypto/ detect/ gui/ ops/ parse/）
├── packaging/         打包链路：build.bat + kv.spec（详见 packaging/README.md）
├── tests/             测试套件（含金标语料 corpus/ 与 runtests.py 入口 shim）
├── build/ dist/       PyInstaller 产物，不入库（.gitignore 已排除）
├── kv.py              CLI 启动 shim：修正 sys.path + 强制 UTF-8 后调 kv.cli.main()
├── kv_gui.py          GUI 启动 shim
└── (九件文档)         README / AGENTS / 目录说明 / HISTORY / TODO / docs/
```

| 文件 | 用途 |
|---|---|
| `kv.py` | CLI 入口。直接 `python -m kv` 会因 cp936 踩编码坑，一律走这个 shim |
| `kv_gui.py` | GUI 入口 shim |
| `kv/__init__.py` | 版本号 `0.1.0`、`SCHEMA_VERSION=1`、`PROGRAM="kv"` 的常量单源 |

打包链路（`packaging/build.bat` + `packaging/kv.spec`）与测试入口（`tests/runtests.py`）
的职责见各自目录的 README。

## 数据组织方式

- vault 是 SQLite 库：`kv init` 在当前目录建 `.kv/`（已 gitignore）；默认全局位置
  `%LOCALAPPDATA%\KeyVault\`（特意不用 `%APPDATA%`，避开域漫游同步）。schema 单源在
  `kv/core/schema.sql`；改 schema 必须递增 `kv/__init__.py` 的 `SCHEMA_VERSION`，
  否则 `check_version()` 拒绝旧库。
- **`fingerprint` 表存所有历史值**：包括 revoked 的——`kv scan` 靠它找泄露的旧 key。
  rotate 时旧指纹的 `value_blob` 保留，不归零。
- **`audit` 表 append-only**：schema 有 `audit_no_update` 触发器阻止 UPDATE/DELETE；
  测试要篡改审计需先 `DROP TRIGGER IF EXISTS audit_no_update`。
- `secret.value_blob` 是 NOT NULL：`purge --zero` 置为空 blob `X''`，不是 NULL。

## 模块依赖关系

```
kv.py / kv_gui.py（shim）
   └── kv/cli.py（argparse + dispatch）
          └── kv/commands/（参数 → 调用 → 输出）
                 └── kv/ops/（save/scan/rotate/import/inject/expiry/purge/fix/review）
                        └── kv/core/（repo 唯一 DML 层、schema、audit、paths、clock）
领域支撑：kv/crypto（DPAPI ctypes 封装）、kv/detect（规则/解析/纠正/报告）、
         kv/parse（dotenv/AWS CSV/curl/JSON/pipeline）、kv/capture（剪贴板/watch/wipe）
叶子：kv/gui（tkinter，没有任何模块 import 它）
```

- 依赖方向单向向下，`kv/core` 不反向 import 上层。
- DML 只允许出现在 `kv/core/repo.py`——其他模块直接写 SQL 会断审计链（hash 对不上）。

## 子目录说明索引

| 子目录 | 说明 |
|---|---|
| `kv/` | [`kv/README.md`](../kv/README.md)（8 个子包逐个说明） |
| `tests/` | [`tests/README.md`](../tests/README.md) |
| `docs/` `build/` `dist/` | 豁免（见 `.docsignore`；docs 是文档目录本身，build/dist 是产物） |

## 已知架构问题

- `kv scan` 对每个文件遍历全部指纹，万级以上密钥规模未做过性能测试（`TODO.md` 任务 2）。
- Windows-only：DPAPI 绑定当前用户，无跨平台与多用户共享路径，这是有意取舍不是欠账。
- 无 lint、无 CI：stdlib 零依赖约束下的选择，门禁是 unittest 全量 + `selftest --golden`。

## 备份格式（2026-10-07，随批1 落地）

`.kvb` = `KVBK1\n` + salt(16) + nonce(16) + ct + HMAC-SHA256 tag(32)。
明文是 JSON（version/exported_at/records[]），**value 以 base64 存明文**，
不是 DPAPI 密文——blob 换机/重装即解不开，备份密文等于备份废铁。
口令是唯一防线：scrypt(n=2^15, r=8, p=1, dklen=64, maxmem 64MB) 派生
enc_key(32) + mac_key(32)；流加密 SHA256(key||nonce||counter)（nonce 16B
随机保证唯一）；encrypt-then-MAC。stdlib 无 AES，此组合（scrypt + HMAC
+ SHA256-CTR）是不引入依赖下防御充分的选择；若未来允许依赖，首选
AES-GCM 平移。恢复走 saveops.commit 正常入库（去重 + 审计链复用）。
