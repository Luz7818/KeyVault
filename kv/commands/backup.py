"""export / restore —— 口令加密备份的 CLI 入口。

备份 ops 在 kv/ops/backup.py（GUI 安全页共用同一层）。命令只做三件事：
读口令（不回显）、调 ops、把结果说成人话。口令不进 argv、不进 shell 历史。

export 在 init 的输出里被点名（store.py 的 R1 提示），所以它必须存在：
DPAPI blob 换机/重装即废铁，口令加密的 .kvb 是唯一可搬运的出路。
"""

from __future__ import annotations

from pathlib import Path

from kv import console, errors
from kv.commands.common import open_store
from kv.ops import backup as backupops


def cmd_export(args) -> int:
    store = open_store(args)
    target = Path(args.to).expanduser()
    password = console.hidden_input("备份口令（不回显，至少 8 位）: ")
    confirm = console.hidden_input("确认口令: ")
    if password != confirm:
        raise errors.UsageError(
            "两次输入的口令不一致",
            hint="口令是备份的唯一凭据：写错口令的备份等于废纸",
        )
    try:
        result = backupops.export_backup(store, target, password)
    finally:
        password = confirm = ""
    console.ok(f"已加密导出 {result.count} 条到 {result.path}")
    console.echo("  口令是唯一凭据：丢失无法恢复，请把口令记在别处")
    return 0


def cmd_restore(args) -> int:
    store = open_store(args)
    source = Path(args.from_file).expanduser()
    if not source.exists():
        raise errors.NotFoundError(f"备份文件不存在：{source}")
    console.warn("恢复会把备份里的记录合并进当前库（重名同值跳过，不覆盖已有记录）")
    if not console.confirm("继续吗？", False):
        console.echo("已取消")
        return 0
    password = console.hidden_input("备份口令（不回显）: ")
    try:
        result = backupops.restore_backup(store, source, password, actor="cli")
    finally:
        password = ""
    console.ok(
        f"恢复完成：新建 {result.created}，重名同值跳过 {result.deduped}"
        f"（备份共 {result.total} 条）"
    )
    return 0
