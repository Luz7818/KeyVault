"""清除已吊销 / 已过期的记录。

两种模式：
  zero   —— 把 value_blob 设 NULL，元数据保留（审计轨迹完整）
  delete —— 整行删掉，CASCADE 带走指纹 / 别名 / 标签关联

默认只 zero，因为 delete 不可逆而 zero 已经拿走了「机密不再落盘」的安全保证。
"""

from __future__ import annotations

from dataclasses import dataclass

from kv.core import audit as auditlog
from kv.core import repo


@dataclass(frozen=True)
class PurgeResult:
    zeroed: int
    deleted: int


def purge(
    store, *, zero: bool = True, delete: bool = False, actor: str = "cli",
) -> PurgeResult:
    """清除 revoked + expired 记录的数据。"""
    zeroed = 0
    deleted = 0
    targets = ("revoked", "expired")
    with store.session() as conn:
        for status in targets:
            rows = repo.list_secrets_by_status(conn, status)
            for row in rows:
                secret_id = int(row["id"])
                if delete:
                    repo.delete_secret(conn, secret_id)
                    deleted += 1
                elif zero:
                    repo.zero_secret_blob(conn, secret_id)
                    repo.zero_fingerprints_for_secret(conn, secret_id)
                    zeroed += 1
                auditlog.append(
                    conn, "purge", actor,
                    secret_id=secret_id, name_snapshot=row["name"],
                    sha256_prefix=row["sha256"][:12],
                    detail={"action": "delete" if delete else "zero", "status": status},
                )
    return PurgeResult(zeroed=zeroed, deleted=deleted)
