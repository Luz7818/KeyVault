"""copy / watch / review / fix / set-platform / rename / tag / corrections。

M2 的命令面：捕获与取用。这里也是唯一会让明文离开 vault 的地方（copy 写剪切板、
reveal 打终端），所以两条路径都必须经 store.reveal() 那个归零窗口。
"""

from __future__ import annotations

from kv import PROGRAM, console, errors
from kv.capture import wipe
from kv.capture import watch as watchmod
from kv.capture.clipboard import Win32Clipboard
from kv.commands.common import open_store
from kv.core import audit as auditlog
from kv.core import repo
from kv.detect import rules
from kv.ops import fix as fixops
from kv.ops import review as reviewops

# 让 cli.py 只依赖 commands 层：默认值在这里转出，argparse 树不必 import capture。
DEFAULT_TTL = wipe.DEFAULT_TTL_SECONDS
DEFAULT_INTERVAL = watchmod.DEFAULT_INTERVAL


def cmd_sweep(args) -> int:
    """显式清扫。copy / watch / doctor 会自动做，这条是给「我就想现在擦掉」用的。"""
    store = open_store(args)
    backend = Win32Clipboard()
    swept = wipe.sweep_due_wipes(store, backend, actor="cli")
    if not swept:
        console.ok("没有过期未擦的剪切板内容")
        return 0
    console.ok(f"清扫了 {len(swept)} 条：{', '.join(swept)}")
    return 0


def cmd_copy(args) -> int:
    store = open_store(args)
    backend = Win32Clipboard()

    # 先清扫：上次 copy 的 helper 可能被关机或崩溃杀掉了，而那把 key 还在剪切板上。
    swept = wipe.sweep_due_wipes(store, backend, actor="cli")
    if swept:
        console.echo(f"顺手清扫了 {len(swept)} 条过期擦除（{', '.join(swept)}）")

    result = wipe.copy_secret(
        store, args.name, backend, ttl=args.ttl, actor="cli",
        wipe=not args.no_wipe, exclude_history=not args.no_exclude_history,
    )
    console.ok(f"已把 {result.name} 复制到剪切板")
    console.echo(f"  平台      {result.platform}")
    console.echo(f"  预览      {result.preview}")
    if result.wipe_id:
        console.echo(f"  自动清除  {result.ttl:.0f} 秒后（擦除 id {result.wipe_id}）")
    else:
        console.warn("未排定自动清除 —— 这把 key 会一直留在剪切板上")
    if result.excluded_from_history:
        console.echo("  Win+V 历史  已排除（也不会同步到其他设备）")
    else:
        console.warn("未排除剪切板历史 —— 这把 key 会留在 Win+V 里，并可能同步到其他设备")
    return 0


def cmd_watch(args) -> int:
    store = open_store(args)
    backend = Win32Clipboard()
    wipe.sweep_due_wipes(store, backend, actor="watch")
    return watchmod.watch(
        store, backend, interval=args.interval, spool=args.spool,
        auto=args.auto, window_hint=not args.no_window_hint,
    )


def cmd_review(args) -> int:
    """处理 --spool 落盘的待审队列。内存队列在 watch 里当场处理，不经过这里。"""
    store = open_store(args)
    items = reviewops.pending_from_inbox(store)
    if not items:
        console.echo("待审队列是空的（kv watch --spool 会把候选落盘到这里）")
        return 0
    if args.limit > 0:
        items = items[: args.limit]

    console.echo(f"待审 {len(items)} 条。逐条处理，歧义的会走平台菜单")

    def mark(item, action, result):
        # deferred（非交互且不够自动接受门槛）保持 pending，下次还能审。
        if action == "deferred":
            return
        reviewops.mark_inbox_decided(store, item, accepted=action != "skip")

    outcome = reviewops.review(items, store, interactive=not args.auto, auto=args.auto,
                              actor="review", on_decided=mark)
    reviewops.echo_outcome(outcome)
    return 0


def cmd_fix(args) -> int:
    store = open_store(args)
    outcome = fixops.fix_walk(store, limit=args.limit)
    console.echo("")
    console.echo(f"过了 {outcome.walked} 条：改对 {outcome.fixed}，跳过 {outcome.skipped}，"
                 f"新增纠正规则 {outcome.corrections_added}")
    return 0


def cmd_set_platform(args) -> int:
    store = open_store(args)
    if args.platform not in rules.BY_PLATFORM and not args.allow_unknown_platform:
        raise errors.UsageError(
            f"{args.platform!r} 不在检测表里",
            hint=f"已知的平台：{', '.join(sorted(rules.all_platforms()))}"
                 "；确要自定义就加 --allow-unknown-platform",
        )
    fixops.set_platform(store, args.name, args.platform, remember=args.remember)
    row = store.get(args.name)
    console.ok(f"{row.name} 的平台现在是 {row.platform}（{row.confidence}）")
    return 0


def cmd_rename(args) -> int:
    store = open_store(args)
    store.rename(args.old, args.new)
    console.ok(f"已改名 {args.old} -> {args.new}（旧名保留为别名）")
    return 0


def cmd_tag(args) -> int:
    store = open_store(args)
    row = store.get(args.name)
    with store.session() as conn:
        for tag in args.add:
            repo.attach_tag(conn, row.id, tag)
        for tag in args.remove:
            repo.detach_tag(conn, row.id, tag)
        auditlog.append(conn, "tag", "cli", secret_id=row.id, name_snapshot=row.name,
                        sha256_prefix=row.sha256[:12],
                        detail={"added": len(args.add), "removed": len(args.remove)})
    updated = store.get(args.name)
    console.ok(f"{updated.name} 的标签：{', '.join(updated.tags) or '（无）'}")
    return 0


def cmd_corrections(args) -> int:
    store = open_store(args)
    if args.delete:
        fixops.drop_correction(store, args.delete)
        console.ok(f"已删除纠正规则 #{args.delete}")
        return 0
    rows = fixops.list_corrections(store)
    if not rows:
        console.echo("还没有任何纠正规则。kv watch 里按 r，或 kv set-platform --remember 会创建")
        return 0
    console.print_table(
        [[str(r["id"]), r["kind"], r["pattern"], r["platform"], r["created_at"][:10]]
         for r in rows],
        ["#", "范围", "匹配", "判成", "创建"],
    )
    console.echo(f"共 {len(rows)} 条。用户规则在每一级都排在内置规则前面")
    return 0


def cmd_wipe_helper(args) -> int:
    """隐藏子命令：分离子进程跑的就是它。argv 里只有行 id，**没有密钥**。"""
    store = open_store(args)
    backend = Win32Clipboard()
    outcome = wipe.run_wipe(store, args.wipe_id, backend, actor="wipe-helper")
    if outcome == "missing":
        return errors.EXIT_NOT_FOUND
    return 0
