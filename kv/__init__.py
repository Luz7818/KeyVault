"""KeyVault —— 本地优先的统一密钥保管工具。"""

__version__ = "0.1.0"

SCHEMA_VERSION = 1

# 放在包顶层而不是 cli.py：命令实现要在提示语和 evidence 里引用它，
# 而 commands -> cli 会和 cli -> commands 成环。
PROGRAM = "kv"
