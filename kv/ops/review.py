"""候选 → 记录：交互式消歧、接受、记住选择。

`review()` 接受一个可迭代对象，所以内存队列（kv watch 默认）和 spool 表
（kv watch --spool）是它的两个调用方 —— **同一个接缝，不重复逻辑**。

三条规矩：

* **默认不自动落盘。** watch 会看到密码管理器复制的密码、2FA 验证码、带 token 的
  URL。占位符闸门拒绝的是模板，不是真凭据。自动保存会用你从没打算存的东西污染库，
  还会把它们写进审计轨迹。--auto 只对 exact/high/manual 生效。
* **歧义走菜单，绝不猜厂商。** 猜错会把假事实写进明文、可搜索、永久的元数据列。
* **「记住这个选择」按仍然有用的最窄范围持久化。** 组级默认不会被静默提供 ——
  它是唯一一条能悄悄给未来每一把 OpenAI key 贴错标签的纠正。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from kv import PROGRAM, clock, console
from kv.core import audit as auditlog
from kv.core import masking, repo
from kv.detect import report, resolve, rules
from kv.detect.corrections import (
    CorrectionSet,
    group_correction,
    suggest_correction_scope,
)
from kv.model import Candidate, SaveResult, Verdict
from kv.ops import save as saveops

AUTO_CONFIDENCES = ("manual", "exact", "high")

ACTION_ACCEPT = "accept"
ACTION_SKIP = "skip"
ACTION_KEEP_LABEL = "keep-label"


@dataclass(frozen=True)
class PendingItem:
    candidate: Candidate
    verdict: Verdict
    captured_at: str = ""
    inbox_id: int | None = None

    @property
    def auto_acceptable(self) -> bool:
        return self.verdict.confidence in AUTO_CONFIDENCES and self.verdict.platform != "unknown"


@dataclass
class ReviewResult:
    accepted: int = 0
    skipped: int = 0
    deduped: int = 0
    saved: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def describe_item(index: int, total: int, item: PendingItem) -> str:
    candidate, verdict = item.candidate, item.verdict
    head = f"[{index}/{total}] {candidate.preview(console.mask_char(), *verdict.mask_style)}"
    parts = [f"来自 {candidate.source or '?'}"]
    if candidate.span:
        parts.append(candidate.span)
    if candidate.key_name:
        parts.append(f"键名 {candidate.key_name}")
    if candidate.hostnames:
        parts.append(f"主机 {candidate.hostnames[0]}")
    elif candidate.context_hostnames:
        parts.append(f"同块主机 {candidate.context_hostnames[0]}")
    return f"{head}   {'  '.join(parts)}"


def list_pending(items, *, limit: int = 0) -> None:
    materialised = list(items)
    if not materialised:
        console.echo("  （队列为空）")
        return
    shown = materialised[:limit] if limit > 0 else materialised
    for index, item in enumerate(shown, 1):
        console.echo("  " + describe_item(index, len(materialised), item))
        console.echo(f"        -> {report.summarize(item.verdict)}")
    if limit and len(materialised) > limit:
        console.echo(f"  …还有 {len(materialised) - limit} 条")


def choose_platform(item: PendingItem) -> tuple[str, str]:
    """交互消歧。返回 (动作, 平台)。动作是 accept / skip / keep-label。"""
    verdict = item.verdict
    if not verdict.candidates:
        return (ACTION_ACCEPT, verdict.platform)

    label = verdict.platform
    options = [(str(i + 1), f"{p}  {rules.display_name(p)}")
               for i, p in enumerate(verdict.candidates)]
    options.append(("0", f"就存成 {label}（{rules.display_name(label)}）"))
    extra = [("s", "跳过这条"), ("r", "选一个平台并记住这个选择")]

    console.echo(f"  {verdict.evidence}")
    choice = console.ask_menu("  哪个平台？", options, extra)

    if choice == "s":
        return (ACTION_SKIP, "")
    if choice == "0":
        return (ACTION_KEEP_LABEL, label)
    if choice == "r":
        picked = console.ask_menu(
            "  记住成哪个平台？（会挑最窄的有效范围）",
            [(str(i + 1), f"{p}  {rules.display_name(p)}")
             for i, p in enumerate(verdict.candidates)],
            [("s", "算了，不记")],
        )
        if picked == "s":
            return (ACTION_SKIP, "")
        platform = verdict.candidates[int(picked) - 1]
        return ("remember", platform)
    return (ACTION_ACCEPT, verdict.candidates[int(choice) - 1])


def decide(item: PendingItem, store, *, actor: str = "watch",
           interactive: bool = True, tags: tuple[str, ...] = ()) -> tuple[str, SaveResult | None]:
    """对一条候选做出决定并落盘。返回 (动作, 结果)。"""
    action, platform = (
        choose_platform(item) if interactive else (ACTION_ACCEPT, item.verdict.platform)
    )
    if action == ACTION_SKIP:
        return ACTION_SKIP, None

    verdict = item.verdict
    if action == "remember":
        _remember(item, store, platform)
        verdict = Verdict(
            platform=platform, confidence="manual", source="correction",
            evidence=f"用户在 {PROGRAM} watch 里指定并记住了这个选择",
            mask_style=verdict.mask_style, kind=verdict.kind,
        )
    elif action in (ACTION_ACCEPT, ACTION_KEEP_LABEL) and platform != verdict.platform:
        verdict = Verdict(
            platform=platform, confidence="manual", source="correction",
            evidence=f"用户在 {PROGRAM} watch 里指定为 {platform}",
            mask_style=rules.mask_style_for(platform), kind=verdict.kind,
        )

    result = saveops.commit(
        item.candidate, verdict, store=store,
        policy=saveops.SavePolicy(tags=tags, origin=item.candidate.source or "watch",
                                  actor=actor),
    )
    if result.outcome == "rejected":
        console.warn(result.warning)
        return ACTION_SKIP, result
    if result.warning:
        console.warn(result.warning)
    return action, result


def review(items, store, *, interactive: bool = True, auto: bool = False,
           actor: str = "watch", tags: tuple[str, ...] = (),
           on_decided=None) -> ReviewResult:
    """走一遍待处理队列。内存队列和 spool 表都调它。

    on_decided(item, action, result) 在每条决定之后调用。用回调而不是返回一个
    平行列表：平行列表要靠调用方按顺序对齐，一旦中途 continue 就会错位 ——
    而错位在这里意味着把没存的条目标成已存。
    """
    materialised = list(items)
    outcome = ReviewResult()
    total = len(materialised)

    for index, item in enumerate(materialised, 1):
        if auto and item.auto_acceptable:
            action, result = decide(item, store, actor=actor, interactive=False, tags=tags)
        elif interactive:
            console.echo(describe_item(index, total, item))
            action, result = decide(item, store, actor=actor, interactive=True, tags=tags)
        else:
            outcome.skipped += 1
            if on_decided:
                on_decided(item, "deferred", None)
            continue

        if on_decided:
            on_decided(item, action, result)
        if result is None:
            outcome.skipped += 1
            continue
        if result.outcome == "deduped":
            outcome.deduped += 1
        elif result.outcome == "created":
            outcome.accepted += 1
            outcome.saved.append(result.name)
        if result.warning:
            outcome.warnings.append(result.warning)

    return outcome


def _remember(item: PendingItem, store, platform: str) -> None:
    """按仍然有用的最窄范围持久化一条纠正规则。"""
    scope = suggest_correction_scope(item.candidate, item.verdict)
    if scope is None:
        group_id = next(
            (r.group for r in rules.RULES
             if r.platform == item.verdict.platform and r.group), None
        )
        if not group_id:
            console.warn("没有可以记住的范围（既无键名也无主机名），这次选择不持久化")
            return
        if not console.confirm(
            f"没有键名或主机名可用。要把整组 {group_id} 的裸值默认成 {platform} 吗？",
            False,
        ):
            return
        kind, pattern, warning = group_correction(group_id, platform)
        console.warn(warning)
    else:
        kind, pattern, note = scope
        console.echo(f"  记住：{kind} {pattern} -> {platform}（{note}）")

    with store.session() as conn:
        repo.insert_correction(conn, kind=kind, pattern=pattern, platform=platform,
                               note=f"{PROGRAM} watch")
        auditlog.append(conn, "platform_set", "watch", name_snapshot=None,
                        detail={"correction_kind": kind, "platform": platform,
                                "pattern_len": len(pattern)})


def echo_outcome(outcome: ReviewResult) -> None:
    parts = [f"新建 {outcome.accepted} 条"]
    if outcome.deduped:
        parts.append(f"去重 {outcome.deduped} 条")
    if outcome.skipped:
        parts.append(f"跳过 {outcome.skipped} 条")
    console.echo("  " + "，".join(parts))
    if outcome.saved:
        console.echo("  已存：" + ", ".join(outcome.saved))


def pending_from_inbox(store) -> list[PendingItem]:
    """--spool 模式：从 inbox 表重建队列。剪切板衍生内容静息状态永不明文。"""
    with store.read() as conn:
        rows = repo.list_inbox(conn, "pending")
    items = []
    for row in rows:
        value = store.protector.unprotect(bytes(row["value_blob"]))
        candidate = Candidate(
            value=value, kind=row["kind"], key_name=row["key_name"],
            source=row["source"], span="inbox",
        )
        items.append(PendingItem(
            candidate=candidate,
            verdict=Verdict(
                platform=row["platform"], confidence=row["confidence"],
                evidence=row["evidence"],
                candidates=tuple(_loads_list(row["candidates_json"])),
            ),
            captured_at=row["captured_at"], inbox_id=int(row["id"]),
        ))
    return items


def _loads_list(text: str) -> list:
    try:
        loaded = json.loads(text or "[]")
    except ValueError:
        return []
    return loaded if isinstance(loaded, list) else []


def mark_inbox_decided(store, item: PendingItem, accepted: bool) -> None:
    if item.inbox_id is None:
        return
    with store.session() as conn:
        repo.mark_inbox(conn, item.inbox_id, "accepted" if accepted else "rejected")


def spool(store, item: PendingItem, *, ttl_days: float = 7.0) -> int | None:
    """--spool 模式：把候选 DPAPI 加密后写进 inbox。**静息状态永不明文。**"""
    canonical = item.candidate.canonical()
    head, tail = masking.split_mask(canonical, *item.verdict.mask_style)
    with store.session() as conn:
        existing = repo.get_secret_by_sha256(conn, item.candidate.sha256())
        if existing is not None:
            return None
        return repo.insert_inbox(conn, row={
            "sha256": item.candidate.sha256(),
            "mask_head": head,
            "mask_tail": tail,
            "value_len": len(canonical),
            "value_blob": store.protector.protect(canonical),
            "kind": item.candidate.kind,
            "key_name": item.candidate.key_name,
            "platform": item.verdict.platform,
            "confidence": item.verdict.confidence,
            "evidence": item.verdict.evidence,
            "candidates_json": json.dumps(list(item.verdict.candidates), ensure_ascii=False),
            "suggested_name": saveops.suggest_name(conn, item.verdict.platform,
                                                   item.candidate.key_name),
            "source": item.candidate.source,
            "context_json": json.dumps({
                "span": item.candidate.span,
                "hostnames": list(item.candidate.hostnames),
                "context_hostnames": list(item.candidate.context_hostnames),
            }, ensure_ascii=False),
            "captured_at": clock.now_iso(),
            "ttl_until": clock.plus_days(ttl_days),
            "status": "pending",
        })


def resolve_with_window(candidate: Candidate, corrections: CorrectionSet,
                        window_title: str | None) -> Verdict:
    """watch 用的入口：把窗口标题接进判定。

    标题在这里用完就丢 —— 它不进 PendingItem、不进 inbox、不进审计。
    """
    return resolve.resolve(candidate, corrections, window_title=window_title or None)
