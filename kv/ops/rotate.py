"""密钥轮换：换值、保留旧指纹。

**轮换不是删除重建。** 旧 sha256 留在 fingerprint 表里，status='revoked'，
revoked_at 填时间戳。scan 靠它找到躺在 git 历史里的旧 key。

save.py 里的 _merge_into_existing 已经会在重新添加已吊销值时发警告 ——
那条路径不需要改，rotate 只是把「旧值 → 新值」的翻转做在一个事务里。
"""

from __future__ import annotations

from kv import clock, errors
from kv.core import audit as auditlog
from kv.core import masking, repo
from kv.core.vault import VaultStore
from kv.model import RotationResult


def rotate(
    store: VaultStore,
    name: str,
    new_value: bytes,
    *,
    actor: str = "cli",
) -> RotationResult:
    """把 name 对应的记录换成新值。旧指纹保留为 revoked。"""
    row = store.get(name)
    canonical = masking.canonicalize(new_value, row.kind)
    if not canonical:
        raise errors.RefusedError(
            "新值为空 —— 空值表示未配置，不该进库",
            hint="如果要删记录用 kv rm",
        )
    new_sha256 = masking.fingerprint(canonical, row.kind)
    now = clock.now_iso()

    with store.session() as conn:
        _check_no_duplicate(conn, new_sha256, row.id)
        old_fp = repo.get_active_fingerprint(conn, row.id)
        if old_fp is None:
            raise errors.KvError(f"记录 {name!r} 没有活跃的指纹行")
        old_sha256 = old_fp["sha256"]
        if old_sha256 == new_sha256:
            raise errors.KvError("新值和旧值相同，无需轮换")
        _revoke_old(conn, old_sha256, now)
        _install_new(conn, store, row.id, canonical, new_sha256, now)
        auditlog.append(
            conn, "rotate", actor, secret_id=row.id, name_snapshot=row.name,
            sha256_prefix=new_sha256[:12],
            detail={"old_sha256_prefix": old_sha256[:12], "new_len": len(canonical)},
        )
    return RotationResult(
        secret_id=row.id, name=row.name,
        old_sha256=old_sha256, new_sha256=new_sha256, old_revoked_at=now,
    )


def _check_no_duplicate(conn, new_sha256, row_id):
    """新值已经存在于别的记录里 → 拒绝。"""
    existing = repo.get_secret_by_sha256(conn, new_sha256)
    if existing is not None and int(existing["id"]) != row_id:
        raise errors.AlreadyExistsError(
            f"新值和记录 {existing['name']!r} 重复（sha256 前缀 {new_sha256[:12]}）",
            hint="那条记录可能已经是同一把 key 了；kv show 确认一下",
        )


def _revoke_old(conn, old_sha256, now):
    """吊销旧指纹：status → revoked。blob 保留给 scan 用。"""
    repo.revoke_fingerprint(conn, old_sha256, revoked_at=now)


def _install_new(conn, store, secret_id, canonical, new_sha256, now):
    """更新 secret 行 + 插入新指纹行。"""
    mask_parts = masking.split_mask(canonical, repo.get_mask_head_len(conn, secret_id), 4)
    blob = store.protector.protect(canonical)
    repo.update_secret(conn, secret_id, {
        "value_blob": blob, "sha256": new_sha256,
        "mask_head": mask_parts[0], "mask_tail": mask_parts[1],
        "value_len": len(canonical),
    })
    repo.insert_fingerprint(
        conn, secret_id=secret_id, sha256=new_sha256,
        mask_head=mask_parts[0], mask_tail=mask_parts[1],
        value_len=len(canonical), status="active",
        value_blob=blob, first_seen_at=now,
    )


def revoke(
    store: VaultStore,
    name: str,
    *,
    actor: str = "cli",
) -> None:
    """标记一条记录为已吊销。值不变，但 status 翻成 revoked。

    和 rotate 的区别：revoke 是「这把 key 作废了」，rotate 是「换一把新的」。
    revoke 之后，记录还在、值还在，但 list 默认不显示（除非 --status revoked）。
    """
    row = store.get(name)
    now = clock.now_iso()

    with store.session() as conn:
        repo.update_secret(conn, row.id, {"status": "revoked"})
        # 吊销当前活跃指纹
        old_fp = repo.get_active_fingerprint(conn, row.id)
        if old_fp is not None:
            repo.revoke_fingerprint(conn, old_fp["sha256"], revoked_at=now)

        auditlog.append(
            conn, "revoke", actor, secret_id=row.id, name_snapshot=row.name,
            sha256_prefix=row.sha256,
            detail={"previous_status": row.status},
        )
