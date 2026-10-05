"""kv fix / set-platform / 纠正规则管理。

fix 处理的是识别的历史欠账：所有 confidence 为 ambiguous 或 none 的记录。
watch 是「捕获时消歧」，fix 是「事后补消歧」—— 两者共用 store.set_platform，
所以修正结果一致。

纠正规则的持久化范围遵循「仍然有用的最窄范围」：键名 > 主机名 > 组默认。
组默认必须显式要求，因为它是唯一一条能悄悄给未来每一把 OpenAI key 贴错标签的纠正。
"""

from __future__ import annotations

from dataclasses import dataclass

from kv import PROGRAM, console, errors
from kv.core import audit as auditlog
from kv.core import repo
from kv.detect import rules
from kv.detect.corrections import group_correction
from kv.model import SecretRow

UNRESOLVED_CONFIDENCES = ("ambiguous", "none")


@dataclass(frozen=True)
class FixOutcome:
    walked: int = 0
    fixed: int = 0
    skipped: int = 0
    corrections_added: int = 0


def unresolved(store) -> list[SecretRow]:
    """所有识别不出厂商的记录 —— 歧义的和不认得的。"""
    return [r for r in store.list_secrets(status="active")
            if r.confidence in UNRESOLVED_CONFIDENCES]


def set_platform(store, name: str, platform: str, *, remember: str = "",
                 actor: str = "cli") -> None:
    """改一条记录的平台，并可选地持久化一条纠正规则。"""
    row = store.get(name)
    store.set_platform(name, platform, evidence=f"{PROGRAM} set-platform {platform}")

    if not remember:
        return
    if remember == "keyname" and row.key_name:
        pattern = f"^{repo_escape(row.key_name)}$"
        _add_correction(store, "keyname", pattern, platform, actor)
    elif remember == "group":
        group_id = _group_of(row.platform)
        if not group_id:
            console.warn(f"{row.platform} 不属于任何歧义组，没有可记住的组默认")
            return
        kind, pattern, warning = group_correction(group_id, platform)
        console.warn(warning)
        _add_correction(store, kind, pattern, platform, actor)
    else:
        raise errors.UsageError(
            f"--remember 只接受 keyname 或 group，收到 {remember!r}",
            hint="keyname 精确且安全；group 会影响未来所有同形态的值，慎用",
        )


def fix_walk(store, *, limit: int = 0, actor: str = "cli") -> FixOutcome:
    """逐条走过识别不出的记录。"""
    rows = unresolved(store)
    if limit > 0:
        rows = rows[:limit]
    if not rows:
        console.ok("没有识别不出的记录，队列干净")
        return FixOutcome()

    outcome = FixOutcome(walked=len(rows))
    console.echo(f"有 {len(rows)} 条记录识别不出厂商。逐条过一遍（回车跳过，q 退出）")
    for index, row in enumerate(rows, 1):
        console.echo("")
        console.echo(f"[{index}/{len(rows)}] #{row.id} {row.name}  "
                     f"{row.preview(console.mask_char())}  平台 {row.platform}")
        console.echo(f"        判定依据：{row.evidence or '（无）'}")
        console.echo(f"        来源 {row.origin}，创建于 {row.created_at}")

        choices = _menu_choices(row)
        if not choices:
            console.echo("        这条没有候选平台，跳过")
            outcome.skipped += 1
            continue
        choice = console.ask_menu(
            "        改成哪个平台？",
            [(key, f"{platform}  {rules.display_name(platform)}") for key, platform in choices],
            [("0", "保持现状不改"), ("q", "退出 fix")],
        )
        if choice == "q":
            outcome.skipped += len(rows) - index
            break
        if choice == "0":
            outcome.skipped += 1
            continue

        platform = dict(choices)[choice]
        store.set_platform(row.name, platform, evidence=f"{PROGRAM} fix {platform}")
        outcome.fixed += 1
        console.ok(f"        已改成 {platform}")

        if row.key_name and console.confirm("        记住「这个键名 -> 这个平台」吗？", False):
            _add_correction(store, "keyname", f"^{repo_escape(row.key_name)}$", platform, actor)
            outcome.corrections_added += 1
    return outcome


def _menu_choices(row: SecretRow) -> list[tuple[str, str]]:
    """给一条记录列出可选平台：歧义记录只列同组成员，其余列全部平台。

    返回 (按键, 平台 id)，于是选中之后不需要再按顺序回推一次 —— 那种回推
    在两个地方各算一遍候选池，是迟早会对不上的写法。
    """
    if row.confidence == "ambiguous":
        group_id = _group_of(row.platform)
        members = rules.group_members(group_id) if group_id else ()
        pool = [m for m in members if m != row.platform]
    else:
        pool = []
    if not pool:
        pool = [p for p in sorted(rules.all_platforms()) if p != row.platform]
    return [(str(i + 1), platform) for i, platform in enumerate(pool)]


def _group_of(platform: str) -> str:
    rule = rules.BY_PLATFORM.get(platform)
    return rule.group if rule and rule.group else ""


def repo_escape(text: str) -> str:
    """把键名转成正则字面量。键名里可能含 . + 等元字符。"""
    import re

    return re.escape(text)


def _add_correction(store, kind: str, pattern: str, platform: str, actor: str) -> None:
    with store.session() as conn:
        repo.insert_correction(conn, kind=kind, pattern=pattern, platform=platform,
                               note=f"{PROGRAM} {actor}")
        auditlog.append(conn, "platform_set", actor, name_snapshot=None,
                        detail={"correction_kind": kind, "platform": platform,
                                "pattern_len": len(pattern)})
    console.ok(f"已记住：{kind} {pattern} -> {platform}")


def list_corrections(store) -> list:
    with store.read() as conn:
        return repo.list_corrections(conn)


def drop_correction(store, correction_id: int) -> None:
    with store.session() as conn:
        removed = repo.delete_correction(conn, correction_id)
        if not removed:
            raise errors.NotFoundError(f"没有 id={correction_id} 的纠正规则")
        auditlog.append(conn, "platform_set", "cli", name_snapshot=None,
                        detail={"correction_removed": correction_id})
