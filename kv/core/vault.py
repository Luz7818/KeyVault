"""VaultStore —— 存储层门面。ops/ 和 gui/ 唯一接触的东西。

明文值只允许通过 reveal() 这一个上下文管理器看到，它在退出时逐字节归零。
其余一切接口（list / get / scan / 网页）拿到的都是掩码、指纹和长度，
**从不调用 CryptUnprotectData**。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from kv import clock, errors, paths
from kv.core import audit as auditlog
from kv.core import db, masking, repo
from kv.crypto.dpapi import DpapiProtector, Protector
from kv.model import SecretRow


def _to_row(conn: sqlite3.Connection, record: sqlite3.Row) -> SecretRow:
    return SecretRow(
        id=int(record["id"]),
        name=record["name"],
        platform=record["platform"],
        confidence=record["confidence"],
        evidence=record["evidence"],
        detect_source=record["detect_source"],
        key_name=record["key_name"],
        kind=record["kind"],
        value_blob=bytes(record["value_blob"]),
        sha256=record["sha256"],
        mask_head=record["mask_head"],
        mask_tail=record["mask_tail"],
        value_len=int(record["value_len"]),
        status=record["status"],
        note=record["note"],
        source_url=record["source_url"],
        origin=record["origin"],
        extra=repo.parse_json(record["extra_json"]),
        created_at=record["created_at"],
        updated_at=record["updated_at"],
        expires_at=record["expires_at"],
        last_used_at=record["last_used_at"],
        last_revealed_at=record["last_revealed_at"],
        tags=tuple(repo.tags_for(conn, int(record["id"]))),
        aliases=tuple(repo.list_aliases(conn, int(record["id"]))),
    )


class VaultStore:
    def __init__(self, db_path: Path, protector: Protector | None = None):
        self.db_path = Path(db_path)
        self.protector = protector or DpapiProtector()
        self._stack: list[sqlite3.Connection] = []

    # ------------------------------------------------------------ 连接管理

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        """一个工作单元。嵌套调用会复用外层连接，不会二次 BEGIN。"""
        if self._stack:
            yield self._stack[-1]
            return
        with db.session(self.db_path) as conn:
            self._stack.append(conn)
            try:
                yield conn
            finally:
                self._stack.pop()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        if self._stack:
            yield self._stack[-1]
            return
        conn = db.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def require_initialized(self) -> None:
        if not db.is_initialized(self.db_path):
            raise errors.VaultNotInitializedError(str(self.db_path))

    # ------------------------------------------------------------ 绑定校验

    def verify_binding(self) -> None:
        """试解密金丝雀。失败意味着这个 vault 是在另一个 Windows 账户下建的。"""
        if not db.is_initialized(self.db_path):
            raise errors.VaultNotInitializedError(str(self.db_path))
        with self.read() as conn:
            binding = repo.get_binding(conn)
        if binding is None:
            raise errors.VaultNotInitializedError(str(self.db_path))
        try:
            recovered = self.protector.unprotect(bytes(binding["canary_blob"]))
        except errors.DpapiError as exc:
            raise errors.BindingError(
                "这个 vault 是在另一个 Windows 账户下创建的 —— DPAPI blob 不能跨账户/跨机器迁移",
                hint="用 kv import --from <backup.kvb> 从备份恢复；"
                     "以后记得定期 kv export，这是唯一能换机的路径",
                code=exc.code,
            ) from None
        if recovered.decode("utf-8", "replace") != binding["canary_text"]:
            raise errors.BindingError("DPAPI 金丝雀往返不一致，vault 可能已损坏")

    # ------------------------------------------------------------ 读取

    def get(self, name: str):
        """按主名或别名取。找不到抛 NotFoundError。"""
        with self.read() as conn:
            record = repo.resolve_name(conn, name)
            if record is None:
                raise errors.NotFoundError(
                    f"没有叫 {name!r} 的记录（也不是任何记录的别名）",
                    hint="kv list 看全部；名字大小写不敏感",
                )
            return _to_row(conn, record)

    def by_id(self, secret_id: int):
        with self.read() as conn:
            record = repo.get_secret_by_id(conn, secret_id)
            if record is None:
                raise errors.NotFoundError(f"没有 id={secret_id} 的记录")
            return _to_row(conn, record)

    def by_sha256(self, sha256: str):
        """按指纹查，查不到返回 None。去重和 scan 都靠它。"""
        with self.read() as conn:
            record = repo.get_secret_by_sha256(conn, sha256)
            return _to_row(conn, record) if record else None

    def find_by_sha256(self, conn: sqlite3.Connection, sha256: str) -> sqlite3.Row | None:
        """事务内的裸查询，给 ops/save.py 的去重路径用。"""
        return repo.get_secret_by_sha256(conn, sha256)

    def list_secrets(self, **filters) -> list:
        with self.read() as conn:
            records = repo.list_secrets(conn, **filters)
            return [_to_row(conn, r) for r in records]

    def count(self) -> int:
        with self.read() as conn:
            return repo.count_secrets(conn)

    def all_scan_fingerprints(self) -> list:
        with self.read() as conn:
            return repo.all_scan_fingerprints(conn)

    def fingerprints_of(self, secret_id: int) -> list:
        with self.read() as conn:
            return repo.list_fingerprints(conn, secret_id)

    # ------------------------------------------------------------ 写入原语
    # 这些是低层原语。规范化 → 指纹 → 去重 → 加密 → 掩码 → 指纹历史 → 审计
    # 的**编排**只在 ops/save.py 里，别处不要复制。

    def insert_secret(self, conn: sqlite3.Connection, fields: dict) -> int:
        return repo.insert_secret(conn, row=fields)

    def update_secret(self, conn: sqlite3.Connection, secret_id: int, fields: dict) -> None:
        repo.update_secret(conn, secret_id, fields)

    def add_alias(self, conn: sqlite3.Connection, secret_id: int, name: str) -> None:
        repo.insert_alias(conn, secret_id, name)

    def add_tag(self, conn: sqlite3.Connection, secret_id: int, tag: str) -> None:
        repo.attach_tag(conn, secret_id, tag)

    def remove_tag(self, conn: sqlite3.Connection, secret_id: int, tag: str) -> None:
        repo.detach_tag(conn, secret_id, tag)

    def rename(self, name: str, new_name: str) -> None:
        row = self.get(name)
        with self.session() as conn:
            if repo.get_secret_by_name(conn, new_name) is not None:
                raise errors.AlreadyExistsError(f"已经有一条叫 {new_name!r} 的记录")
            repo.update_secret(conn, row.id, {"name": new_name})
            repo.insert_alias(conn, row.id, row.name)
            auditlog.append(
                conn, "rename", "cli", secret_id=row.id, name_snapshot=new_name,
                sha256_prefix=row.sha256, detail={"from_len": len(row.name)},
            )

    def set_platform(self, name: str, platform: str, *, evidence: str = "") -> None:
        row = self.get(name)
        with self.session() as conn:
            repo.update_secret(conn, row.id, {
                "platform": platform,
                "confidence": "manual",
                "detect_source": "correction",
                "evidence": evidence or f"kv set-platform -> {platform}",
            })
            auditlog.append(
                conn, "platform_set", "cli", secret_id=row.id, name_snapshot=row.name,
                sha256_prefix=row.sha256, detail={"platform": platform},
            )

    def delete(self, name: str) -> int:
        """删记录。审计行留下（audit 无外键、带 name_snapshot），所以历史仍可读。"""
        row = self.get(name)
        with self.session() as conn:
            auditlog.append(
                conn, "delete", "cli", secret_id=row.id, name_snapshot=row.name,
                sha256_prefix=row.sha256, detail={"platform": row.platform},
            )
            repo.delete_secret(conn, row.id)
        return row.id

    def record_audit(self, event: str, actor: str, **kwargs) -> int:
        with self.session() as conn:
            return auditlog.append(conn, event, actor, **kwargs)

    def list_audit(self, **filters) -> list:
        with self.read() as conn:
            return repo.list_audit(conn, **filters)

    def verify_audit(self):
        with self.read() as conn:
            return auditlog.verify(conn)

    # ------------------------------------------------------------ 明文窗口

    @contextmanager
    def reveal(self, name: str, *, actor: str = "cli", event: str = "reveal") -> Iterator[bytearray]:
        """看到明文值的**唯一**许可路径。

        明文以 bytearray 形式存在，从不作为不可变 bytes；退出时逐字节归零。
        审计行在窗口关闭之后写，所以它拿到的是指纹而不是值。
        """
        row = self.get(name)
        buffer = bytearray()
        try:
            self.protector.unprotect_into(row.value_blob, buffer)
        except errors.DpapiError as exc:
            self.record_audit(
                "decrypt_failed", actor, secret_id=row.id, name_snapshot=row.name,
                sha256_prefix=row.sha256, detail={"code": exc.code},
            )
            raise
        try:
            yield buffer
        finally:
            for index in range(len(buffer)):
                buffer[index] = 0
            buffer.clear()
        with self.session() as conn:
            repo.update_secret(conn, row.id, {"last_revealed_at": clock.now_iso()})
            auditlog.append(
                conn, event, actor, secret_id=row.id, name_snapshot=row.name,
                sha256_prefix=row.sha256, detail={"len": row.value_len},
            )

    def encrypt(self, value: bytes) -> bytes:
        return self.protector.protect(value)

    def fingerprint_of(self, value: bytes, kind: str = masking.KIND_TOKEN) -> str:
        return masking.fingerprint(value, kind)

    # ------------------------------------------------------------ 设置

    def get_setting(self, key: str, default: str = "") -> str:
        with self.read() as conn:
            return repo.get_setting(conn, key, default)

    def set_setting(self, key: str, value: str) -> None:
        with self.session() as conn:
            repo.set_setting(conn, key, value)

    # ------------------------------------------------------------ 诊断

    def doctor(self) -> dict:
        """只报状态与非机密元数据。绝不报值。"""
        info: dict = {
            "vault_dir": str(self.db_path.parent),
            "db_path": str(self.db_path),
            "db_exists": self.db_path.exists(),
            "sqlite_version": sqlite3.sqlite_version,
            "db_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
        }
        gitignore = self.db_path.parent / ".gitignore"
        info["gitignore_ok"] = (
            gitignore.exists()
            and gitignore.read_text(encoding="utf-8") == paths.GITIGNORE_BODY
        )
        info["cloud_sync_marker"] = paths.is_cloud_synced(self.db_path.parent)

        if not info["db_exists"]:
            info["initialized"] = False
            return info

        info["initialized"] = db.is_initialized(self.db_path)
        with self.read() as conn:
            info["schema_version"] = repo.get_meta(conn, "schema_version")
            info["vault_created_at"] = repo.get_meta(conn, "created_at")
            info["secret_count"] = repo.count_secrets(conn)
            info["correction_count"] = len(repo.list_corrections(conn))
            info["audit_rows"] = len(repo.all_audit_rows(conn))
            info["pending_wipes"] = len(repo.due_wipes(conn))
            binding = repo.get_binding(conn)
            info["has_binding_canary"] = binding is not None

        try:
            self.verify_binding()
            info["binding_ok"] = True
            info["binding_error"] = ""
        except errors.KvError as exc:
            info["binding_ok"] = False
            info["binding_error"] = str(exc)
        return info
