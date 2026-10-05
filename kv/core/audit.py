"""追加式审计：哈希链 + 校验。

追加式由**数据库触发器**强制（schema.sql 里的 audit_no_update / audit_no_delete），
不靠约定。chain_hash 让删行即使有人 drop 掉触发器也能被发现。

本模块不含任何 SQL —— 全部走 repo。

detail 只允许白名单的非机密字段。这是纵深防御的最后一道：审计日志是最可能被
导出或粘进 bug 报告的东西，所以它连完整 sha256 都不存，只存 12 位前缀。
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from kv import clock, errors
from kv.core import repo

GENESIS = "keyvault-audit-genesis-v1"
FIELD_SEP = "\x1f"
LINK_SEP = "\x1e"

PREFIX_WIDTH = 12
MAX_DETAIL_VALUE_LEN = 200

FORBIDDEN_DETAIL_KEYS = frozenset({
    "value", "secret", "token", "password", "passwd", "key", "api_key", "apikey",
    "plaintext", "blob", "credential", "authorization",
})


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    count: int
    first_bad_id: int | None = None
    reason: str = ""


def _field(value) -> str:
    return "" if value is None else str(value)


def canonical_row(
    *,
    audit_id: int,
    ts: str,
    event: str,
    actor: str,
    secret_id: int | None,
    name_snapshot: str | None,
    sha256_prefix: str | None,
    detail_json: str,
) -> str:
    return FIELD_SEP.join([
        str(audit_id), ts, event, actor,
        _field(secret_id), _field(name_snapshot), _field(sha256_prefix), detail_json,
    ])


def compute_chain(prev_hash: str, canonical: str) -> str:
    payload = (prev_hash + LINK_SEP + canonical).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sanitize_detail(detail: dict | None) -> dict:
    """把 detail 收敛成「按构造不含机密」的东西。

    规则：拒绝 bytes 值；拒绝机密味的键名；字符串截断到 MAX_DETAIL_VALUE_LEN。
    键名白名单会比这个更强，但每加一种事件都要改白名单，容易漏 —— 而这三条
    对任何事件都成立。
    """
    if not detail:
        return {}
    clean: dict = {}
    for raw_key, raw_value in detail.items():
        key = str(raw_key).lower()
        if key in FORBIDDEN_DETAIL_KEYS:
            raise errors.KvError(
                f"审计 detail 里出现了机密味的键名 {raw_key!r}",
                hint="审计日志按构造不含机密。要记的是长度、指纹前缀、错误码",
            )
        if isinstance(raw_value, (bytes, bytearray)):
            raise errors.KvError(
                f"审计 detail 的 {raw_key!r} 是 bytes",
                hint="审计日志不接受任何字节值 —— 传长度或 sha256 前缀",
            )
        if isinstance(raw_value, str) and len(raw_value) > MAX_DETAIL_VALUE_LEN:
            clean[raw_key] = raw_value[:MAX_DETAIL_VALUE_LEN] + f"…(+{len(raw_value) - MAX_DETAIL_VALUE_LEN})"
        else:
            clean[raw_key] = raw_value
    return clean


def append(
    conn: sqlite3.Connection,
    event: str,
    actor: str,
    *,
    secret_id: int | None = None,
    name_snapshot: str | None = None,
    sha256_prefix: str | None = None,
    detail: dict | None = None,
    ts: str | None = None,
) -> int:
    """追加一条审计行，返回它的 id。必须在调用方已开启的事务里执行。"""
    if sha256_prefix and len(sha256_prefix) > PREFIX_WIDTH:
        sha256_prefix = sha256_prefix[:PREFIX_WIDTH]

    detail_json = repo.canonical_json(sanitize_detail(detail))
    stamp = ts or clock.now_iso()

    previous = repo.last_audit_row(conn)
    prev_hash = previous["chain_hash"] if previous else GENESIS

    audit_id = repo.next_audit_id(conn)
    canonical = canonical_row(
        audit_id=audit_id, ts=stamp, event=event, actor=actor, secret_id=secret_id,
        name_snapshot=name_snapshot, sha256_prefix=sha256_prefix, detail_json=detail_json,
    )
    chain_hash = compute_chain(prev_hash, canonical)

    repo.insert_audit(
        conn,
        audit_id=audit_id, ts=stamp, event=event, actor=actor, secret_id=secret_id,
        name_snapshot=name_snapshot, sha256_prefix=sha256_prefix,
        detail_json=detail_json, chain_hash=chain_hash,
    )
    return audit_id


def verify(conn: sqlite3.Connection) -> VerifyResult:
    """重算整条链。返回第一个对不上的行 id。"""
    rows = repo.all_audit_rows(conn)
    prev_hash = GENESIS
    for row in rows:
        canonical = canonical_row(
            audit_id=int(row["id"]), ts=row["ts"], event=row["event"], actor=row["actor"],
            secret_id=row["secret_id"], name_snapshot=row["name_snapshot"],
            sha256_prefix=row["sha256_prefix"], detail_json=row["detail_json"],
        )
        expected = compute_chain(prev_hash, canonical)
        if expected != row["chain_hash"]:
            return VerifyResult(False, len(rows), int(row["id"]), "链哈希对不上")
        prev_hash = row["chain_hash"]
    return VerifyResult(True, len(rows))
