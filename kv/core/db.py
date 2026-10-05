"""连接、PRAGMA、事务、schema 应用与版本闸。

本文件**不含 DML** —— 读写行的 SQL 全在 repo.py，这样整个数据访问面能被一次
阅读审完。这里只留三类东西：连接配置（PRAGMA）、DDL 应用、事务边界。

PRAGMA 放这里而不是 schema.sql：foreign_keys 是每连接生效的，写进脚本会给人
「它是持久的」错觉。journal_mode=WAL 确实持久，但也在这里设，好让所有连接一致。
WAL 的 -wal/-shm 边车文件落在 vault 目录里，已被那份 * 的 .gitignore 覆盖。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from kv import SCHEMA_VERSION, clock, errors, paths
from kv.core import repo

SCHEMA_FILE = Path(__file__).with_name("schema.sql")


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
        conn.execute("PRAGMA journal_mode = WAL")


def connect(target: Path | str) -> sqlite3.Connection:
    """开一个连接。row_factory 是 sqlite3.Row，PRAGMA 已设好。"""
    conn = sqlite3.connect(str(target), timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    return conn


@contextmanager
def session(target: Path | str) -> Iterator[sqlite3.Connection]:
    """一个工作单元一个连接。提交或回滚，然后关闭。

    每单元一连接而不是共享一个，是为了绕开跨线程共享连接的全部问题 ——
    gui 从后台线程提供服务。
    """
    conn = connect(target)
    try:
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def schema_sql() -> str:
    return SCHEMA_FILE.read_text(encoding="utf-8")


def apply_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(schema_sql())


def read_version(conn: sqlite3.Connection) -> int | None:
    value = repo.get_meta(conn, "schema_version")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise errors.KvError(
            "vault 的 schema_version 不是整数",
            hint="这个库可能已损坏，或来自一个不兼容的版本",
        )


def check_version(conn: sqlite3.Connection) -> int:
    """版本闸：库比代码新就直接拒绝打开，不做静默降级。"""
    version = read_version(conn)
    if version is None:
        raise errors.VaultNotInitializedError("meta 表里没有 schema_version")
    if version > SCHEMA_VERSION:
        raise errors.KvError(
            f"vault 的 schema 版本 {version} 高于本程序支持的 {SCHEMA_VERSION}",
            hint="先升级 kv，不要用旧版程序写新版的库",
        )
    return version


def is_initialized(target: Path) -> bool:
    if not target.exists():
        return False
    try:
        conn = connect(target)
    except sqlite3.Error:
        return False
    try:
        return repo.has_schema_version(conn)
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def initialize(db_path: Path, *, canary_blob: bytes, canary_text: str) -> None:
    """建目录、写自排除 .gitignore、建 schema、写 meta 与 binding 金丝雀。"""
    vault_root = db_path.parent
    marker = paths.is_cloud_synced(vault_root)
    if marker:
        raise errors.UsageError(
            f"拒绝把 vault 建在云同步目录里（命中 {marker}）：{vault_root}",
            hint="云同步盘会把加密库复制到你不控制的机器上，"
                 "而 DPAPI blob 换机器就解不开 —— 你会得到一堆废铁。"
                 f"改设 {paths.ENV_OVERRIDE} 指向一个本地目录",
        )

    vault_root.mkdir(parents=True, exist_ok=True)
    paths.ensure_self_excluding_gitignore(vault_root)

    conn = connect(db_path)
    try:
        apply_schema(conn)
        conn.execute("BEGIN")
        repo.bootstrap_meta(
            conn,
            schema_version=SCHEMA_VERSION,
            created_at=clock.now_iso(),
            canary_blob=canary_blob,
            canary_text=canary_text,
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
