"""时间源。

单独一个模块是为了给测试一个注入点：过期预警、擦除 deadline、inbox TTL、审计链
全都要确定性可测。产品代码一律调 now_iso()，不要直接 datetime.now()。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

_provider: Callable[[], datetime] | None = None


def set_provider(provider: Callable[[], datetime] | None) -> None:
    """测试用。传 None 恢复真实时钟。"""
    global _provider
    _provider = provider


def now() -> datetime:
    if _provider is not None:
        return _provider()
    return datetime.now(timezone.utc).astimezone()


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def plus_seconds(seconds: float) -> str:
    return (now() + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def plus_days(days: float) -> str:
    return (now() + timedelta(days=days)).isoformat(timespec="seconds")


def parse(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def days_until(text: str | None) -> float | None:
    """距离某个 ISO 时间还有几天。已过期返回负数。无法解析返回 None。"""
    target = parse(text)
    if target is None:
        return None
    current = now()
    if target.tzinfo is None:
        target = target.replace(tzinfo=current.tzinfo)
    return (target - current).total_seconds() / 86400.0
