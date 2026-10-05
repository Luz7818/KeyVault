"""M4 命令：scan、audit、expire、purge。"""

from __future__ import annotations

from kv import console, errors
from kv.commands.common import open_store
from kv.commands.detect import gate_status
from kv.core import audit as auditlog
from kv.core import repo
from kv.ops import expiry as expiryops
from kv.ops import purge as purgeops
from kv.ops import scan as scanops


def cmd_scan(args) -> int:
    """扫描目录树，报告泄露的密钥。"""
    store = open_store(args)
    passed, message = gate_status(store)
    if not passed:
        console.error(message)
        return errors.EXIT_GATE

    root = args.directory
    result = scanops.scan_directory(store, root)

    for hit in result.hits:
        status_tag = f" [{hit.status}]" if hit.status != "active" else ""
        console.echo(
            f"  {hit.path}:{hit.line_no}"
            f"  {hit.name}  {hit.platform}{status_tag}"
            f"  {hit.sha256_prefix}"
        )

    console.echo(
        f"\n扫描 {result.scanned} 个文件，"
        f"跳过 {result.skipped_binary} 个二进制 / {result.skipped_large} 个过大"
    )
    if result.hits:
        console.error(f"发现 {len(result.hits)} 处泄露")
    else:
        console.ok("未发现泄露")

    with store.session() as conn:
        auditlog.append(
            conn, "scan", "cli",
            detail={
                "directory": root,
                "hits": len(result.hits),
                "scanned": result.scanned,
                "skipped_binary": result.skipped_binary,
                "skipped_large": result.skipped_large,
            },
        )
    return errors.EXIT_LEAKS if result.hits else 0


def cmd_audit(args) -> int:
    """审计链校验或事件列表。"""
    store = open_store(args)

    if args.verify:
        with store.read() as conn:
            result = auditlog.verify(conn)
        if result.ok:
            console.ok(f"审计链完好（{result.count} 行）")
            return 0
        console.error(
            f"审计链在第 {result.first_bad_id} 行断裂：{result.reason}"
        )
        return 1

    with store.read() as conn:
        rows = repo.list_audit(
            conn,
            event=getattr(args, "event", "") or "",
            secret_id=getattr(args, "secret_id", None),
            limit=getattr(args, "limit", 50),
        )
    if not rows:
        console.echo("（空）")
        return 0
    for row in rows:
        console.echo(
            f"  {row['ts']}  {row['event']:<14}  {row['actor']}"
            f"  #{row['secret_id'] or '-'}  {row['name_snapshot'] or ''}"
        )
    console.echo(f"共 {len(rows)} 条")
    return 0


def cmd_expire(args) -> int:
    """标记过期记录。"""
    store = open_store(args)
    result = expiryops.mark_expired(store)
    if result.expired_count:
        console.echo(f"已标记 {result.expired_count} 条为过期")
    else:
        console.ok("没有需要过期的记录")
    return 0


def cmd_purge(args) -> int:
    """清除已吊销 / 已过期的记录数据。"""
    store = open_store(args)
    do_delete = getattr(args, "delete", False)
    dry_run = getattr(args, "dry_run", False)

    if dry_run:
        count = _count_purgeable(store)
        console.echo(f"将清除 {count} 条记录（dry-run）")
        return 0

    result = purgeops.purge(store, zero=not do_delete, delete=do_delete)
    if do_delete:
        console.echo(f"已删除 {result.deleted} 条记录")
    else:
        console.echo(f"已清零 {result.zeroed} 条记录的数据")
    return 0


def _count_purgeable(store) -> int:
    """统计可清除的记录数。"""
    with store.read() as conn:
        revoked = repo.list_secrets_by_status(conn, "revoked")
        expired = repo.list_secrets_by_status(conn, "expired")
    return len(revoked) + len(expired)
