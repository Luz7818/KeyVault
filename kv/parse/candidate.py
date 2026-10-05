"""构造 Candidate 的共用助手，以及主机名抽取。

所有解析器都经这里过占位符闸门，于是「闸门是闸门不是打分器」这条性质只在一处
实现，不会各解析器各写一套。
"""

from __future__ import annotations

import re
from dataclasses import replace

from kv.model import Candidate, Rejection
from kv.parse import placeholders
from kv.parse.placeholders import Context

URL_RE = re.compile(r"(?i)\b(?:https?|ftps?|wss?|mysql|postgres(?:ql)?|mongodb(?:\+srv)?|redis|rediss|amqp|ssh|git)\://(?:[^:/@\s]+(?::[^@\s]*)?@)?([^/:?\s#]+)")

EMAIL_DOMAIN_RE = re.compile(r"(?i)[a-z0-9._%+\-]+@([a-z0-9.\-]+\.[a-z]{2,})")


def hosts_in(*texts: str) -> tuple[str, ...]:
    """从任意文本里抽出所有主机名。**只是信号** —— 本包永不把主机名映射成平台。"""
    found: list[str] = []
    for text in texts:
        if not text:
            continue
        found.extend(m.group(1).lower() for m in URL_RE.finditer(text))
        found.extend(m.group(1).lower() for m in EMAIL_DOMAIN_RE.finditer(text))
    unique: list[str] = []
    for host in found:
        if host and host not in unique:
            unique.append(host)
    return tuple(unique)


def emit(
    value: str,
    *,
    kind: str = "token",
    key_name: str | None = None,
    hostnames: tuple[str, ...] = (),
    source: str = "manual",
    span: str = "",
    extra: dict | None = None,
    context: Context | None = None,
) -> Candidate | Rejection:
    """过闸门，然后要么给出候选，要么给出一个不含值的拒绝原因。"""
    ctx = context or Context(source=source, line_text=value)
    # 键名要进 Context：占位符闸门靠它执行「键名拒绝清单」。
    if key_name and ctx.key_name != key_name:
        ctx = replace(ctx, key_name=key_name)
    verdict = placeholders.classify(value, ctx)
    if verdict is not None:
        reason, why = verdict
        return Rejection(span=span, reason=reason, detail=why)
    return Candidate(
        value=value.encode("utf-8"),
        kind=kind,
        key_name=key_name,
        hostnames=tuple(hostnames),
        source=source,
        span=span,
        extra=dict(extra or {}),
    )


def split_outcome(items) -> tuple[list[Candidate], list[Rejection]]:
    candidates, rejected = [], []
    for item in items:
        (candidates if isinstance(item, Candidate) else rejected).append(item)
    return candidates, rejected
