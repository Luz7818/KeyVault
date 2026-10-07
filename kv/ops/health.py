"""密钥健康报告：即将过期与长期未轮换。

纯读判定，不写库。now 可注入（测试用 FakeClock 之外的场景），生产走 kv.clock。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from kv import clock
from kv.core.vault import VaultStore


@dataclass(frozen=True)
class HealthReport:
    expiring: tuple[str, ...]   # 30 天内到期（未过期）
    stale: tuple[str, ...]      # updated_at 距今超过 stale_days（建议轮换）
    expired_count: int          # 已过期数量


def _parse(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.astimezone()
    except ValueError:
        return None


def health_report(
    store: VaultStore, *,
    expiring_days: int = 30, stale_days: int = 180,
    now: datetime | None = None,
) -> HealthReport:
    now = now or _parse(clock.now_iso()) or datetime.now().astimezone()
    expiring: list[str] = []
    stale: list[str] = []
    expired = 0
    for row in store.list_secrets():
        if row.status == "revoked":
            continue
        exp = _parse(row.expires_at)
        if exp is not None:
            if exp <= now:
                expired += 1
            elif exp <= now + timedelta(days=expiring_days):
                expiring.append(row.name)
        updated = _parse(row.updated_at)
        if updated is not None and now - updated > timedelta(days=stale_days):
            stale.append(row.name)
    return HealthReport(tuple(expiring), tuple(stale), expired)
