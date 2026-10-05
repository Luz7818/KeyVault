"""过期标记：把 expires_at 已过的 active 记录翻成 expired。

只改状态，不删数据、不零化 blob —— 那是 purge 的事。
"""

from __future__ import annotations

from dataclasses import dataclass

from kv import clock
from kv.core import audit as auditlog
from kv.core import repo


@dataclass(frozen=True)
class ExpiryResult:
    expired_count: int


def mark_expired(store) -> ExpiryResult:
    """把 expires_at 已过且仍 active 的记录标记为 expired。"""
    now = clock.now_iso()
    with store.session() as conn:
        stale = repo.list_expired_active_secrets(conn, now)
        for row in stale:
            repo.expire_secret(conn, int(row["id"]), now)
            auditlog.append(
                conn, "expire", "cli",
                secret_id=int(row["id"]), name_snapshot=row["name"],
                sha256_prefix=row["sha256"][:12],
                detail={"expires_at": row["expires_at"]},
            )
    return ExpiryResult(expired_count=len(stale))
