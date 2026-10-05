"""init / doctor / add / list / show / reveal / rm / config。

M0 的命令面。这里只做「把参数翻译成 ops 调用、把结果翻译成人话」，
不含存储逻辑 —— 那在 core/ 和 ops/ 里。
"""

from __future__ import annotations

import json
import sys

from kv import PROGRAM, clock, console, errors
from kv.core import db, repo
from kv.core.vault import VaultStore
from kv.crypto import dpapi
from kv.ops import save as saveops

from kv.commands.common import (
    db_path,
    detect_for_add,
    load_corrections,
    open_store,
    policy_from_args,
    read_value,
    row_to_dict,
)


def cmd_init(args) -> int:
    target = db_path(args)
    if db.is_initialized(target):
        console.warn(f"vault 已经初始化过了：{target}")
        return 0

    try:
        canary_blob = dpapi.protect(
            dpapi.CANARY_TEXT.encode("utf-8"), local_machine=args.local_machine
        )
    except errors.DpapiError as exc:
        console.error(f"DPAPI 自检失败，无法初始化 vault：{exc}")
        return exc.exit_code

    db.initialize(target, canary_blob=canary_blob, canary_text=dpapi.CANARY_TEXT)
    store = VaultStore(target)
    store.verify_binding()
    store.record_audit(
        "init", "cli", detail={"local_machine": bool(args.local_machine)}
    )

    console.ok(f"vault 已建在 {target.parent}")
    console.echo("  数据库      " + target.name)
    console.echo("  自排除规则  .gitignore（* 加 !.gitignore），外层 git add -A 带不走它")
    console.echo(f"  加密        Windows DPAPI，{'机器级' if args.local_machine else '当前用户级'}")
    console.echo("")
    # R1：DPAPI 密钥丢失不可恢复，而它是本设计里最坏的结局。检测得到但恢复不了，
    # 所以首次导出必须是一等行为，不是事后补丁。
    console.warn("重置 Windows 密码、加域、重装系统都会让这个库里的每个 blob 变成废铁，")
    console.warn("而且**无法恢复**。现在就做一次导出：")
    console.echo(f"  {PROGRAM} export --to D:\\backup\\vault.kvb")
    return 0


def cmd_doctor(args) -> int:
    store = VaultStore(db_path(args))
    field = console.echo_field

    # 先清扫，再体检 —— 反过来的话「待擦除 N 条」报的是清扫前的旧数字。
    # 清扫只在本来就要碰剪切板的命令里做（copy / watch / doctor）。计划原稿说
    # 「任何 kv 调用都清扫」，那会让 kv list 也去读你的剪切板 —— 一个与命令职责
    # 无关的副作用。doctor 的职责就是「检查各种状态」，所以它做。
    swept = _sweep_pending(store)
    info = store.doctor()
    info["swept"] = swept

    field("vault 目录", info["vault_dir"])
    field("数据库", f"{info['db_path']}（{info['db_bytes']} 字节）")
    field("已初始化", "是" if info.get("initialized") else "否")
    field("自排除 .gitignore", "正确" if info["gitignore_ok"] else "缺失或内容不对")
    field("SQLite", info["sqlite_version"])
    field("Python", f"{sys.version.split()[0]} @ {sys.executable}")

    if info.get("cloud_sync_marker"):
        console.error(f"vault 在云同步目录里（命中 {info['cloud_sync_marker']}）—— 立刻搬走")

    if not info.get("initialized"):
        console.warn(f"还没初始化，跑 {PROGRAM} init")
        return 0

    field("schema 版本", str(info["schema_version"]))
    field("记录数", str(info["secret_count"]))
    field("纠正规则数", str(info["correction_count"]))
    field("审计行数", str(info["audit_rows"]))
    field("DPAPI 绑定", "正常" if info["binding_ok"] else "失败")
    if not info["binding_ok"]:
        console.error(info["binding_error"])
        return errors.BindingError.exit_code
    if info["pending_wipes"]:
        field("待擦除", f"{info['pending_wipes']} 条（本次调用会顺手清扫）")
    if info.get("swept"):
        field("本次已清扫", f"{info['swept']} 条过期擦除")
    return 0


def _sweep_pending(store) -> int:
    """清扫过期未擦的剪切板。剪切板不可用时返回 0 —— 体检不该因此失败。"""
    import sqlite3

    from kv.capture import wipe
    from kv.capture.clipboard import ClipboardError, Win32Clipboard

    try:
        return len(wipe.sweep_due_wipes(store, Win32Clipboard(), actor="doctor"))
    except (errors.KvError, sqlite3.Error, OSError, ClipboardError, ValueError):
        return 0


def cmd_add(args) -> int:
    store = open_store(args)
    value = read_value(args)
    if not value.strip():
        console.error("值为空。空值表示未配置，不该进库")
        return errors.EXIT_USAGE

    candidate, verdict = detect_for_add(args, value, load_corrections(args))
    result = saveops.commit(
        candidate, verdict, store=store, policy=policy_from_args(args)
    )

    if result.outcome == "rejected":
        console.error(result.warning)
        return errors.EXIT_USAGE
    if result.warning:
        console.warn(result.warning)
    if result.outcome == "deduped":
        console.echo(f"这把 key 已经在库里了（#{result.secret_id} {result.name}），没有新建记录")
        if result.merged_tags:
            console.echo("  合并了：" + ", ".join(result.merged_tags))
        return 0

    console.ok(f"已保存 #{result.secret_id} {result.name}")
    row = store.by_id(result.secret_id)
    console.echo(f"  平台      {row.platform}（{row.confidence}，依据 {row.evidence or '无'}）")
    console.echo(f"  预览      {row.preview(console.mask_char())}   长度 {row.value_len}")
    console.echo(f"  创建时间  {row.created_at}")
    if row.confidence == "ambiguous":
        console.warn(
            f"平台是歧义的（候选：{', '.join(verdict.candidates)}）—— 已存成 {row.platform}，"
            f"没有猜厂商。用 {PROGRAM} set-platform {row.name} <平台> 修正"
        )
    return 0


def _row_to_table(row, expiring: bool = False) -> list[str]:
    """单元格数必须与表头列数一致 —— 空标签也要占一格，否则表格会错位。"""
    cells = [str(row.id), row.name, row.platform,
             row.preview(console.mask_char()), row.created_at[:10], row.status]
    if expiring:
        left = clock.days_until(row.expires_at) if row.expires_at else None
        cells.append(f"{left:.0f}d" if left is not None else "")
    cells.append(",".join(row.tags))
    return cells


def cmd_list(args) -> int:
    store = open_store(args)
    if args.expiring is not None:
        from kv.ops import expiry as expiryops
        expiryops.mark_expired(store)
    rows = store.list_secrets(
        query=args.query, platform=args.platform, tag=args.tag, status=args.status,
        expiring_days=args.expiring, limit=args.limit,
    )
    if args.json:
        console.echo(json.dumps([row_to_dict(r) for r in rows], ensure_ascii=False, indent=2))
        return 0
    if not rows:
        console.echo("（空）")
        return 0

    headers = ["#", "名称", "平台", "预览", "创建", "状态"]
    if args.expiring is not None:
        headers.append("剩余")
    headers.append("标签")
    console.print_table([_row_to_table(r, args.expiring is not None) for r in rows], headers)
    console.echo(f"共 {len(rows)} 条")
    return 0


def cmd_show(args) -> int:
    store = open_store(args)
    row = store.get(args.name)
    if args.json:
        console.echo(json.dumps(row_to_dict(row), ensure_ascii=False, indent=2))
        return 0

    console.echo(f"#{row.id}  {row.name}")
    field = console.echo_field
    field("平台", row.platform)
    field("置信度", f"{row.confidence}（来源 {row.detect_source}）")
    field("判定依据", row.evidence or "（无）")
    field("预览", f"{row.preview(console.mask_char())}   长度 {row.value_len}")
    field("指纹", row.sha256)
    if row.key_name:
        field("原键名", row.key_name)
    field("类型", row.kind)
    field("状态", row.status)
    field("创建时间", row.created_at)
    field("更新时间", row.updated_at)
    if row.expires_at:
        left = clock.days_until(row.expires_at)
        field("过期时间", row.expires_at + ("（已过期）" if left is not None and left < 0 else ""))
    if row.last_used_at:
        field("最后使用", row.last_used_at)
    if row.last_revealed_at:
        field("最后 reveal", row.last_revealed_at)
    if row.source_url:
        field("来源", row.source_url)
    if row.note:
        field("备注", row.note)
    if row.tags:
        field("标签", ", ".join(row.tags))
    if row.aliases:
        field("别名", ", ".join(row.aliases))
    if row.extra:
        field("附加", json.dumps(row.extra, ensure_ascii=False))

    history = store.fingerprints_of(row.id)
    if len(history) > 1:
        console.echo(f"  历史值        {len(history)} 个（含已轮换掉的）")
        for item in history:
            console.echo(f"    {item['status']:<8} {item['sha256'][:12]}  {item['first_seen_at']}")
    return 0


def cmd_reveal(args) -> int:
    """唯一会把明文值打到 stdout 的命令。"""
    store = open_store(args)
    if not args.yes:
        console.warn("即将把明文密钥打印到终端 —— 它会留在滚屏缓冲、终端日志和录屏里")
        if not console.confirm("继续吗？", False):
            console.echo("已取消")
            return 0
    with store.reveal(args.name) as buffer:
        sys.stdout.write(buffer.decode("utf-8", "replace"))
        sys.stdout.write("\n" if args.newline else "")
        sys.stdout.flush()
    return 0


def cmd_rm(args) -> int:
    store = open_store(args)
    row = store.get(args.name)
    if not args.yes:
        console.echo(
            f"将删除 #{row.id} {row.name}（{row.platform}，{row.preview(console.mask_char())}）"
        )
        console.echo("审计行会保留（带名字快照），所以历史仍可读；但值本身没了")
        if not console.confirm("确认删除？", False):
            console.echo("已取消")
            return 0
    store.delete(args.name)
    console.ok(f"已删除 {row.name}")
    return 0


def cmd_config(args) -> int:
    store = open_store(args)
    if args.set:
        key, sep, value = args.set.partition("=")
        if not key or not sep:
            raise errors.UsageError("--set 需要 KEY=VALUE 形式")
        store.set_setting(key.strip(), value)
        console.ok(f"{key.strip()} = {value}")
        if key.strip() == "mask_char":
            console.init_mask_char(value)
        return 0
    if args.get:
        console.echo(store.get_setting(args.get))
        return 0
    with store.read() as conn:
        rows = repo.list_settings(conn)
    if not rows:
        console.echo("（还没有任何配置项）")
        return 0
    console.print_table([list(r) for r in rows], ["键", "值"])
    return 0
