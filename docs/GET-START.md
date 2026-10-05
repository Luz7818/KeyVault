# kv 上手手册

> 用途：给要真的把它用起来或改它的人。每一步给命令、给预期输出、给出错时怎么办。
> 阅读顺序：从第一节往下做,不需要跳读。术语第一次出现都有解释。

## 1. 你需要准备什么

| 项目 | 要求 | 怎么确认 |
|---|---|---|
| 操作系统 | Windows 10 / 11 | 设置 → 系统 → 关于 |
| 运行时 | Python 3.12+ | `python --version`,期望输出 `Python 3.12.x` |
| 网络 | 不需要 | — |
| 密钥 | 不需要外部服务密钥 | — |

kv 依赖 Windows DPAPI（Data Protection API），仅支持 Windows。Linux/macOS 无法运行。

## 2. 装好它

kv 是零依赖项目，不需要 `pip install`。把仓库克隆到本地即可：

```bash
git clone <repo-url> KeyVault
cd KeyVault
```

预期输出：无报错，目录里能看到 `kv.py`、`kv/`、`tests/`。

验证 Python 版本：

```bash
python --version
```

预期看到：`Python 3.12.x` 或更高。

## 3. 跑一遍最小流程

```bash
# 1. 初始化 vault（在当前目录创建 .kv/ 子目录和 SQLite 数据库）
python kv.py init

# 2. 存一条密钥
python kv.py add my-api-key sk-abcdef1234567890abcdef1234567890abcdef12

# 3. 查看记录列表
python kv.py list
```

预期看到：

```
  #  名称          平台         状态    创建时间
  1  my-api-key    <detected>   active  2026-10-04 ...
```

各参数含义：

| 参数 | 作用 | 常用值 |
|---|---|---|
| `init` 后的目录 | vault 位置，`.kv/` 子目录存放数据库 | 默认当前目录 |
| `add <名称> <值>` | 名称是你给这条记录起的别名，值是原始密钥文本 | — |
| `list` | 列出所有记录，不显示明文值 | — |

## 4. 日常怎么用

按场景分小节。每节:要做什么 → 命令 → 看到什么算对。

### 4.1 查看密钥详情（不显示明文）

```bash
python kv.py show my-api-key
```

预期看到名称、平台、状态、sha256 前缀、掩码后的值预览（`sk-a******************12`）。

### 4.2 查看明文值

```bash
python kv.py reveal my-api-key
```

明文输出到 stdout。注意：这是唯一会打印明文的命令。

### 4.3 复制到剪贴板（自动擦除）

```bash
python kv.py copy my-api-key
```

值被复制到剪贴板，30 秒后自动擦除。可以用 `--ttl 60` 改擦除等待时间。

### 4.4 扫描目录中的泄露密钥

```bash
# 先确保检测规则是最新的
python kv.py selftest --golden

# 扫描指定目录
python kv.py scan ./my-project
```

预期看到：每个匹配的文件名、行号、匹配到的记录名称。退出码 3 表示发现泄露，0 表示干净。

### 4.5 轮换密钥

```bash
python kv.py rotate my-api-key sk-newvalue1234567890abcdef1234567890abcdef1234
```

旧值被标记为 revoked（但指纹保留给 scan 用），新值替换当前值。

### 4.6 批量导入 .env 文件

```bash
python kv.py import .env
```

自动解析 `KEY=value` 格式，逐条检测并存入 vault。重复值跳过。

### 4.7 注入密钥到 .env 文件

```bash
python kv.py use my-api-key --target ./my-project/.env
```

从 vault 取出明文，写入目标 `.env` 文件的对应变量。自动检查 `.gitignore`。

## 5. 想改它:常见三种改动

### 5.1 加一条检测规则

检测规则定义在 `kv/detect/rules.py` 的 `RULES` 表中。每条规则指定平台名、值形状（正则）、典型前缀等。

改完必须跑：

```bash
python -I -m unittest discover -s tests -t .
python kv.py selftest --golden
```

如果金标语料不匹配新规则，`selftest --golden` 会报错并提示需要更新 `golden_passed_for`。

### 5.2 加一个 CLI 命令

1. 在 `kv/cli.py` 的对应 parser 函数里加子命令定义。
2. 在 `kv/commands/` 里加实现函数。
3. 在 `kv/cli.py` 的 dispatch 表里加映射。
4. 加测试到 `tests/test_cli_m*.py`。

改完必须跑：`python -I -m unittest discover -s tests -t .`

### 5.3 加一个测试

测试文件命名 `tests/test_*.py`，类继承 `unittest.TestCase`。用 `tests/fakes.py` 里的替身：

- `PlaintextProtector`：不依赖 DPAPI 的加密替身
- `FakeClipboard`：内存剪贴板
- `FakeClock`：可控时间

```bash
python -I -m unittest tests.test_your_module -v
```

## 6. 常见故障

| 现象 / 报错原文 | 原因 | 处理 |
|---|---|---|
| `ModuleNotFoundError: No module named 'kv'` | 没有从仓库根运行 | `cd` 到 `KeyVault/` 目录再跑 |
| `UnicodeDecodeError: 'cp936' codec can't decode` | Windows 默认编码 cp936，文件是 UTF-8 | 用 `python kv.py`（shim 会设 UTF-8），不要直接 `python -m kv` |
| `BindingError: vault 不属于当前用户` | DPAPI 绑定检查失败 | 在同一个 Windows 账户下运行；不要复制 vault 到其他机器 |
| `GateError: selftest golden corpus 未通过` | scan 要求先通过金标自测 | `python kv.py selftest --golden` |
| `sqlite3.OperationalError: attempt to write a readonly database` | vault 数据库被设为只读 | 检查文件权限 |

## 7. 验证你改的东西没弄坏

```bash
python -I -m unittest discover -s tests -t .
```

通过标准：退出码 0、543 个用例通过（允许 2 个 skip）。这几条命令的含义写在仓库根的 `AGENTS.md`。
