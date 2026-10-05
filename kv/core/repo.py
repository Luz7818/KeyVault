"""全项目每一条 SQL 语句都在这里。

这不是风格偏好：把 SQL 集中到一个文件，才可能用一次阅读审完整个数据访问面，
也才可能用 test_arch.py 机械地断言别处没有 SQL 字面量。

规则：表名列名只来自本文件的固定白名单，用户输入一律走 ? 参数。
"""

from __future__ import annotations

import json
import sqlite3

from kv import clock

SECRET_COLUMNS = (
    "id", "name", "platform", "confidence", "evidence", "detect_source", "key_name",
    "kind", "value_blob", "sha256", "mask_head", "mask_tail", "value_len", "status",
    "note", "source_url", "origin", "extra_json", "created_at", "updated_at",
    "expires_at", "last_used_at", "last_revealed_at",
)

# 派生而不是手写第二份 —— 两份手写的同一列表一定会漂移。
IMMUTABLE_COLUMNS = frozenset({"id", "created_at", "updated_at"})
UPDATABLE_COLUMNS = frozenset(c for c in SECRET_COLUMNS if c not in IMMUTABLE_COLUMNS)

LISTED_STATUSES = ("active", "revoked", "expired")
AUDIT_EVENTS = (
    "save", "dedupe_hit", "reveal", "copy", "wipe", "wipe_skipped", "export", "import",
    "rotate", "revoke", "purge", "inject", "scan", "ui_copy", "platform_set",
    "decrypt_failed", "login_failed", "rename", "delete", "tag", "init", "doctor",
    "expire",
)


def canonical_json(detail: dict | None) -> str:
    return json.dumps(detail or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def parse_json(text: str | None) -> dict:
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def escape_like(text: str) -> str:
    """转义 LIKE 通配符。不转义的话用户搜 '100%' 会匹配到一切。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------- meta / setting

def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def bootstrap_meta(
    conn: sqlite3.Connection,
    *,
    schema_version: int,
    created_at: str,
    canary_blob: bytes,
    canary_text: str,
) -> None:
    """建库时写 meta 与 DPAPI 绑定金丝雀。只有 db.initialize 调它。"""
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
        ("schema_version", str(schema_version)),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
        ("created_at", created_at),
    )
    conn.execute(
        "INSERT OR IGNORE INTO binding (id, canary_blob, canary_text, created_at)"
        " VALUES (1, ?, ?, ?)",
        (canary_blob, canary_text, created_at),
    )


def has_schema_version(conn: sqlite3.Connection) -> bool:
    """这个库初始化过吗。用 sqlite_master 而不是 SELECT meta —— meta 表本身
    可能还不存在，那时 SELECT 会抛 no such table。"""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
    ).fetchone()
    if not int(row["n"]):
        return False
    return get_meta(conn, "schema_version") is not None


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO setting (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def list_settings(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return [
        (row["key"], row["value"])
        for row in conn.execute("SELECT key, value FROM setting ORDER BY key")
    ]


# ---------------------------------------------------------------- binding

def get_binding(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT canary_blob, canary_text, created_at FROM binding WHERE id = 1"
    ).fetchone()


# ---------------------------------------------------------------- secret

def insert_secret(conn: sqlite3.Connection, *, row: dict) -> int:
    """低层插入。只有 ops/save.py 该调它 —— 规范化、指纹、去重、审计的编排
    全部集中在那一处，别处不要复制。"""
    columns = ", ".join(SECRET_COLUMNS[1:])
    placeholders = ", ".join("?" for _ in SECRET_COLUMNS[1:])
    values = [row[c] for c in SECRET_COLUMNS[1:]]
    cursor = conn.execute(
        f"INSERT INTO secret ({columns}) VALUES ({placeholders})", values
    )
    return int(cursor.lastrowid)


def get_secret_by_id(conn: sqlite3.Connection, secret_id: int) -> sqlite3.Row | None:
    columns = ", ".join(SECRET_COLUMNS)
    return conn.execute(f"SELECT {columns} FROM secret WHERE id = ?", (secret_id,)).fetchone()


def get_secret_by_name(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    columns = ", ".join(SECRET_COLUMNS)
    return conn.execute(f"SELECT {columns} FROM secret WHERE name = ?", (name,)).fetchone()


def get_secret_by_sha256(conn: sqlite3.Connection, sha256: str) -> sqlite3.Row | None:
    columns = ", ".join(SECRET_COLUMNS)
    return conn.execute(f"SELECT {columns} FROM secret WHERE sha256 = ?", (sha256,)).fetchone()


def find_by_alias(conn: sqlite3.Connection, name: str) -> int | None:
    row = conn.execute("SELECT secret_id FROM alias WHERE name = ?", (name,)).fetchone()
    return int(row["secret_id"]) if row else None


def resolve_name(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    """按名字取记录，名字可以是主名也可以是别名。"""
    return get_secret_by_name(conn, name) or (
        get_secret_by_id(conn, find_by_alias(conn, name)) if find_by_alias(conn, name) else None
    )


def update_secret(conn: sqlite3.Connection, secret_id: int, fields: dict) -> None:
    """按白名单更新若干列，并自动 touch updated_at。"""
    unknown = set(fields) - UPDATABLE_COLUMNS
    if unknown:
        raise KeyError(f"不允许更新的列：{sorted(unknown)}")
    if not fields:
        return
    assignments = ", ".join(f"{c} = ?" for c in fields)
    values = list(fields.values()) + [clock.now_iso(), secret_id]
    conn.execute(
        f"UPDATE secret SET {assignments}, updated_at = ? WHERE id = ?", values
    )


def touch(conn: sqlite3.Connection, secret_id: int) -> None:
    """只更新 updated_at。去重命中时用 —— 那次没有别的列要改，但时间戳该动。"""
    conn.execute("UPDATE secret SET updated_at = ? WHERE id = ?", (clock.now_iso(), secret_id))


def delete_secret(conn: sqlite3.Connection, secret_id: int) -> None:
    conn.execute("DELETE FROM secret WHERE id = ?", (secret_id,))


def count_secrets(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS n FROM secret").fetchone()["n"])


def _query_predicate(query: str) -> tuple[str, list]:
    like = f"%{escape_like(query)}%"
    sql = (
        "(s.name LIKE ? ESCAPE '\\' COLLATE NOCASE"
        " OR s.platform LIKE ? ESCAPE '\\' COLLATE NOCASE"
        " OR s.note LIKE ? ESCAPE '\\' COLLATE NOCASE"
        " OR s.key_name LIKE ? ESCAPE '\\' COLLATE NOCASE"
        " OR s.source_url LIKE ? ESCAPE '\\' COLLATE NOCASE"
        " OR EXISTS (SELECT 1 FROM alias a WHERE a.secret_id = s.id"
        "             AND a.name LIKE ? ESCAPE '\\' COLLATE NOCASE))"
    )
    return sql, [like] * 6


def _within_horizon(expires_at: str | None, horizon, days: float) -> bool:
    target = clock.parse(expires_at)
    if target is None:
        return False
    if target.tzinfo is None:
        target = target.replace(tzinfo=horizon.tzinfo)
    return (target - horizon).total_seconds() / 86400.0 <= days


def list_secrets(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    platform: str = "",
    tag: str = "",
    status: str = "",
    expiring_days: float | None = None,
    limit: int = 0,
) -> list[sqlite3.Row]:
    """筛选列表。全部条件走参数；表名列名来自固定白名单。"""
    where: list[str] = []
    params: list = []

    if status:
        where.append("s.status = ?")
        params.append(status)
    if platform:
        where.append("s.platform = ?")
        params.append(platform)
    if query:
        fragment, fragment_params = _query_predicate(query)
        where.append(fragment)
        params.extend(fragment_params)
    if tag:
        where.append(
            "EXISTS (SELECT 1 FROM secret_tag st JOIN tag t ON t.id = st.tag_id"
            "        WHERE st.secret_id = s.id AND t.name = ?)"
        )
        params.append(tag)
    if expiring_days is not None:
        where.append("s.expires_at IS NOT NULL")

    sql = f"SELECT {', '.join(SECRET_COLUMNS)} FROM secret s"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY s.created_at DESC, s.id DESC"
    if limit > 0:
        sql += " LIMIT ?"
        params.append(limit)

    rows = list(conn.execute(sql, params))
    if expiring_days is None:
        return rows
    horizon = clock.now()
    return [r for r in rows if _within_horizon(r["expires_at"], horizon, expiring_days)]


# ---------------------------------------------------------------- fingerprint

def insert_fingerprint(
    conn: sqlite3.Connection,
    *,
    secret_id: int,
    sha256: str,
    mask_head: str,
    mask_tail: str,
    value_len: int,
    status: str,
    value_blob: bytes | None,
    first_seen_at: str,
    revoked_at: str | None = None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO fingerprint (secret_id, sha256, mask_head, mask_tail, value_len,"
        " status, value_blob, first_seen_at, revoked_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (secret_id, sha256, mask_head, mask_tail, value_len, status,
         value_blob, first_seen_at, revoked_at),
    )
    return int(cursor.lastrowid)


def list_fingerprints(conn: sqlite3.Connection, secret_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT id, sha256, mask_head, mask_tail, value_len, status,"
            " first_seen_at, revoked_at"
            " FROM fingerprint WHERE secret_id = ? ORDER BY id",
            (secret_id,),
        )
    )


def all_scan_fingerprints(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """scan 要的：每条记录**曾经持有过的每一个值**，含已吊销的。"""
    return list(
        conn.execute(
            "SELECT f.sha256, f.status, f.secret_id, s.name, s.platform"
            " FROM fingerprint f JOIN secret s ON s.id = f.secret_id"
        )
    )


def zero_fingerprint_blob(conn: sqlite3.Connection, sha256: str) -> int:
    cursor = conn.execute(
        "UPDATE fingerprint SET value_blob = NULL WHERE sha256 = ?", (sha256,)
    )
    return cursor.rowcount


def revoke_fingerprint(conn: sqlite3.Connection, sha256: str, revoked_at: str) -> int:
    """把一条指纹标记为已吊销。status='revoked'，revoked_at 填时间戳。

    返回值是受影响的行数。调用方负责保证 sha256 存在 —— 不存在就返回 0。
    """
    cursor = conn.execute(
        "UPDATE fingerprint SET status = 'revoked', revoked_at = ? WHERE sha256 = ?",
        (revoked_at, sha256),
    )
    return cursor.rowcount


def get_active_fingerprint(conn: sqlite3.Connection, secret_id: int) -> sqlite3.Row | None:
    """取一条记录当前生效的那条指纹。rotate 用它拿旧 sha256。"""
    return conn.execute(
        "SELECT id, sha256, mask_head, mask_tail, value_len, status, value_blob,"
        " first_seen_at, revoked_at"
        " FROM fingerprint WHERE secret_id = ? AND status = 'active'"
        " ORDER BY id DESC LIMIT 1",
        (secret_id,),
    ).fetchone()


def get_mask_head_len(conn: sqlite3.Connection, secret_id: int) -> int:
    """secret 行的 mask_head 长度。rotate 用它推断掩码风格。"""
    row = conn.execute(
        "SELECT mask_head FROM secret WHERE id = ?", (secret_id,),
    ).fetchone()
    return len(row["mask_head"]) if row and row["mask_head"] else 0


# ---------------------------------------------------------------- alias / tag

def insert_alias(conn: sqlite3.Connection, secret_id: int, name: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO alias (secret_id, name) VALUES (?, ?)", (secret_id, name)
    )


def list_aliases(conn: sqlite3.Connection, secret_id: int) -> list[str]:
    return [
        row["name"]
        for row in conn.execute(
            "SELECT name FROM alias WHERE secret_id = ? ORDER BY name", (secret_id,)
        )
    ]


def ensure_tag(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM tag WHERE name = ?", (name,)).fetchone()
    if row:
        return int(row["id"])
    cursor = conn.execute("INSERT INTO tag (name) VALUES (?)", (name,))
    return int(cursor.lastrowid)


def attach_tag(conn: sqlite3.Connection, secret_id: int, tag_name: str) -> None:
    tag_id = ensure_tag(conn, tag_name)
    conn.execute(
        "INSERT OR IGNORE INTO secret_tag (secret_id, tag_id) VALUES (?, ?)",
        (secret_id, tag_id),
    )


def detach_tag(conn: sqlite3.Connection, secret_id: int, tag_name: str) -> None:
    conn.execute(
        "DELETE FROM secret_tag WHERE secret_id = ? AND tag_id = (SELECT id FROM tag WHERE name = ?)",
        (secret_id, tag_name),
    )


def tags_for(conn: sqlite3.Connection, secret_id: int) -> list[str]:
    return [
        row["name"]
        for row in conn.execute(
            "SELECT t.name FROM tag t JOIN secret_tag st ON st.tag_id = t.id"
            " WHERE st.secret_id = ? ORDER BY t.name",
            (secret_id,),
        )
    ]


def list_tags(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    return [
        (row["name"], int(row["n"]))
        for row in conn.execute(
            "SELECT t.name, COUNT(st.secret_id) AS n FROM tag t"
            " LEFT JOIN secret_tag st ON st.tag_id = t.id"
            " GROUP BY t.id ORDER BY n DESC, t.name"
        )
    ]


# ---------------------------------------------------------------- correction

def insert_correction(
    conn: sqlite3.Connection, *, kind: str, pattern: str, platform: str, note: str = ""
) -> None:
    conn.execute(
        "INSERT INTO correction (kind, pattern, platform, note, created_at)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(kind, pattern) DO UPDATE SET"
        "   platform = excluded.platform, note = excluded.note",
        (kind, pattern, platform, note, clock.now_iso()),
    )


def list_corrections(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT id, kind, pattern, platform, note, created_at"
            " FROM correction ORDER BY kind, pattern"
        )
    )


def delete_correction(conn: sqlite3.Connection, correction_id: int) -> int:
    return conn.execute("DELETE FROM correction WHERE id = ?", (correction_id,)).rowcount


# ---------------------------------------------------------------- audit

def next_audit_id(conn: sqlite3.Connection) -> int:
    """显式取号。chain_hash 必须把 id 算进去，而 id 在 INSERT 之前就得知道。
    审计是追加式的、单用户、且整个写入在一个事务里，所以这里不存在竞态。"""
    row = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 AS n FROM audit").fetchone()
    return int(row["n"])


def insert_audit(
    conn: sqlite3.Connection,
    *,
    audit_id: int,
    ts: str,
    event: str,
    actor: str,
    secret_id: int | None,
    name_snapshot: str | None,
    sha256_prefix: str | None,
    detail_json: str,
    chain_hash: str,
) -> None:
    conn.execute(
        "INSERT INTO audit (id, ts, event, actor, secret_id, name_snapshot,"
        " sha256_prefix, detail_json, chain_hash)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (audit_id, ts, event, actor, secret_id, name_snapshot, sha256_prefix,
         detail_json, chain_hash),
    )


def last_audit_row(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, ts, event, actor, secret_id, name_snapshot, sha256_prefix,"
        " detail_json, chain_hash FROM audit ORDER BY id DESC LIMIT 1"
    ).fetchone()


def all_audit_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT id, ts, event, actor, secret_id, name_snapshot, sha256_prefix,"
            " detail_json, chain_hash FROM audit ORDER BY id"
        )
    )


def list_audit(
    conn: sqlite3.Connection, *, event: str = "", secret_id: int | None = None, limit: int = 50
) -> list[sqlite3.Row]:
    where: list[str] = []
    params: list = []
    if event:
        where.append("event = ?")
        params.append(event)
    if secret_id is not None:
        where.append("secret_id = ?")
        params.append(secret_id)
    sql = (
        "SELECT id, ts, event, actor, secret_id, name_snapshot, sha256_prefix,"
        " detail_json, chain_hash FROM audit"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return list(conn.execute(sql, params))


# ---------------------------------------------------------------- pending_wipe

def insert_pending_wipe(
    conn: sqlite3.Connection, *, sha256: str, secret_id: int | None, deadline_at: str
) -> int:
    cursor = conn.execute(
        "INSERT INTO pending_wipe (sha256, secret_id, deadline_at, created_at, status)"
        " VALUES (?, ?, ?, ?, 'pending')",
        (sha256, secret_id, deadline_at, clock.now_iso()),
    )
    return int(cursor.lastrowid)


def get_pending_wipe(conn: sqlite3.Connection, wipe_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, sha256, secret_id, deadline_at, created_at, status"
        " FROM pending_wipe WHERE id = ?",
        (wipe_id,),
    ).fetchone()


def due_wipes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT id, sha256, secret_id, deadline_at, created_at, status"
            " FROM pending_wipe WHERE status = 'pending' AND deadline_at <= ? ORDER BY id",
            (clock.now_iso(),),
        )
    )


def mark_wipe(conn: sqlite3.Connection, wipe_id: int, status: str) -> None:
    conn.execute("UPDATE pending_wipe SET status = ? WHERE id = ?", (status, wipe_id))


# ---------------------------------------------------------------- inbox

def insert_inbox(conn: sqlite3.Connection, *, row: dict) -> int:
    columns = (
        "sha256", "mask_head", "mask_tail", "value_len", "value_blob", "kind",
        "key_name", "platform",
        "confidence", "evidence", "candidates_json", "suggested_name", "source",
        "context_json", "captured_at", "ttl_until", "status",
    )
    cursor = conn.execute(
        f"INSERT INTO inbox ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        [row[c] for c in columns],
    )
    return int(cursor.lastrowid)


def list_inbox(conn: sqlite3.Connection, status: str = "pending") -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM inbox WHERE status = ? ORDER BY id", (status,)
        )
    )


def get_inbox(conn: sqlite3.Connection, inbox_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM inbox WHERE id = ?", (inbox_id,)).fetchone()


def mark_inbox(conn: sqlite3.Connection, inbox_id: int, status: str) -> None:
    conn.execute(
        "UPDATE inbox SET status = ?, decided_at = ? WHERE id = ?",
        (status, clock.now_iso(), inbox_id),
    )


def expire_stale_inbox(conn: sqlite3.Connection) -> int:
    cursor = conn.execute(
        "UPDATE inbox SET status = 'expired', decided_at = ?"
        " WHERE status = 'pending' AND ttl_until <= ?",
        (clock.now_iso(), clock.now_iso()),
    )
    return cursor.rowcount


# ---------------------------------------------------------------- M4: scan / expiry / purge

def scan_fingerprints_with_blobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """scan 用：每条指纹含 value_blob，解密后当搜索模式。含已吊销的。"""
    return list(
        conn.execute(
            "SELECT f.sha256, f.status, f.value_blob, f.value_len,"
            " f.secret_id, s.name, s.platform"
            " FROM fingerprint f JOIN secret s ON s.id = f.secret_id"
            " WHERE f.value_blob IS NOT NULL"
        )
    )


def list_expired_active_secrets(
    conn: sqlite3.Connection, now_iso: str,
) -> list[sqlite3.Row]:
    """expires_at 已过但 status 还是 active 的记录。expiry 命令用。"""
    columns = ", ".join(SECRET_COLUMNS)
    return list(
        conn.execute(
            f"SELECT {columns} FROM secret"
            " WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?",
            (now_iso,),
        )
    )


def expire_secret(conn: sqlite3.Connection, secret_id: int, now_iso: str) -> None:
    """把一条 secret 和它所有 active 指纹翻成 expired。"""
    conn.execute(
        "UPDATE secret SET status = 'expired', updated_at = ? WHERE id = ?",
        (now_iso, secret_id),
    )
    conn.execute(
        "UPDATE fingerprint SET status = 'expired' WHERE secret_id = ? AND status = 'active'",
        (secret_id,),
    )


def list_secrets_by_status(
    conn: sqlite3.Connection, status: str,
) -> list[sqlite3.Row]:
    """按状态列记录。purge 用它找可清理的目标。"""
    columns = ", ".join(SECRET_COLUMNS)
    return list(
        conn.execute(f"SELECT {columns} FROM secret WHERE status = ?", (status,))
    )


def zero_secret_blob(conn: sqlite3.Connection, secret_id: int) -> int:
    """把 secret.value_blob 设为空 blob。purge --zero 用。"""
    cursor = conn.execute(
        "UPDATE secret SET value_blob = X'' WHERE id = ?", (secret_id,),
    )
    return cursor.rowcount


def zero_fingerprints_for_secret(conn: sqlite3.Connection, secret_id: int) -> int:
    """把该记录所有指纹的 value_blob 置 NULL。purge 用。"""
    cursor = conn.execute(
        "UPDATE fingerprint SET value_blob = NULL WHERE secret_id = ?",
        (secret_id,),
    )
    return cursor.rowcount
