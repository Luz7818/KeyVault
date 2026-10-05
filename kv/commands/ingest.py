"""import / use / rotate / revoke。

M3 的命令面。批量摄入、.env 注入、密钥轮换与吊销。
每条命令只做「参数 → ops 调用 → 人话输出」的翻译。
"""

from __future__ import annotations

import sys
from pathlib import Path

from kv import console, errors
from kv.commands.common import load_corrections, open_store, read_value
from kv.ops import importer, inject, rotate


def cmd_import(args) -> int:
    """批量导入 .env 或 AWS credentials.csv。"""
    store = open_store(args)
    source = Path(args.file).expanduser().resolve()
    if not source.exists():
        console.error(f"文件不存在：{source}")
        return errors.EXIT_NOT_FOUND

    corrections = load_corrections(args)
    tags = tuple(args.tag) if hasattr(args, "tag") and args.tag else ()

    result = importer.import_file(
        source, store,
        corrections=corrections,
        tags=tags,
        force=getattr(args, "force", False),
        actor="cli",
    )

    console.echo(f"导入完成：{source.name}")
    console.echo(f"  新增    {result.created}")
    console.echo(f"  去重    {result.deduped}")
    console.echo(f"  拒绝    {result.rejected}")
    if result.errors:
        console.echo(f"  错误    {len(result.errors)}")
        for err in result.errors[:5]:
            console.echo(f"    {err}")
        if len(result.errors) > 5:
            console.echo(f"    ... 还有 {len(result.errors) - 5} 条")
    if result.created == 0 and result.deduped == 0 and result.rejected == 0:
        console.warn("没有识别出任何 —— 确认文件格式是 .env 或 AWS credentials.csv")
    return 0


def cmd_use(args) -> int:
    """把密钥值注入 .env 文件。"""
    store = open_store(args)
    target = Path(args.to).expanduser().resolve()

    key_name = getattr(args, "key_name", "") or None
    force = getattr(args, "force", False)
    do_gitignore = getattr(args, "gitignore", False)

    result = inject.inject(
        store, args.name, target,
        key_name=key_name,
        force=force,
        gitignore=do_gitignore,
        actor="cli",
    )

    console.ok(f"已把 {result.key_name} 写入 {result.path}")
    console.echo(f"  行号  {result.line_no}")
    console.echo(f"  操作  {result.action}")
    return 0


def cmd_rotate(args) -> int:
    """轮换密钥值。旧指纹保留为 revoked。"""
    store = open_store(args)
    new_value = read_value(args)
    if not new_value.strip():
        console.error("新值为空。空值表示未配置，不该进库")
        return errors.EXIT_USAGE

    result = rotate.rotate(store, args.name, new_value, actor="cli")

    console.ok(f"已轮换 {result.name}")
    console.echo(f"  旧指纹  {result.old_sha256[:12]}（已吊销）")
    console.echo(f"  新指纹  {result.new_sha256[:12]}")
    row = store.by_id(result.secret_id)
    console.echo(f"  预览    {row.preview(console.mask_char())}   长度 {row.value_len}")
    return 0


def cmd_revoke(args) -> int:
    """标记记录为已吊销。值不变，status 翻成 revoked。"""
    store = open_store(args)
    row = store.get(args.name)

    rotate.revoke(store, args.name, actor="cli")

    console.ok(f"已吊销 {row.name}（{row.platform}）")
    console.echo("  记录仍保留，但 list 默认不显示。用 --status revoked 查看")
    return 0
