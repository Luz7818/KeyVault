"""提交一条记录。

**全项目只有这里 insert 进 secret 表。** 它在一个事务里拥有：
规范化 → 指纹 → 去重 → DPAPI 加密 → 掩码预览 → 指纹历史行 → 审计行。

importer / watch / rotate / transfer 全部经由它。后果是：加一个平台只碰
detect/rules.py；加一个解析器只碰 parse/；改存储 schema 只碰 core/ 加本文件。
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from kv import clock, errors
from kv.core import audit as auditlog
from kv.core import masking, repo
from kv.model import (
    CONFIDENCE_MANUAL,
    STATUS_REVOKED,
    Candidate,
    SaveResult,
    Verdict,
)

# R4：元数据按设计是明文的，而 note 恰恰是用户会粘贴东西的地方 —— 包括机密。
# M1 会把这个判断换成 detect 的规则表；在那之前用一条窄正则兜底。窄是故意的：
# 现有工程曾因白名单过宽把一把 32 位 hex 的真高德 key 一起放过。
_CREDENTIAL_SMELL = re.compile(
    r"(?i)(?:\b(?:sk-[a-z0-9_\-]{16,}|(?:ghp|gho|ghu|ghs|ghr)_[a-z0-9]{20,}"
    r"|github_pat_[a-z0-9_]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{35}"
    r"|glpat-[a-z0-9_\-]{20,}|xox[baprs]-[a-z0-9\-]{10,}|vercel_[a-z0-9]{20,}"
    r"|[0-9a-f]{32})\b"
    r"|\b[a-z][a-z0-9+.\-]*://[^:/@\s]+:[^@\s]+@[^\s]+)"
)


@dataclass(frozen=True)
class SavePolicy:
    name: str = ""
    tags: tuple[str, ...] = ()
    note: str = ""
    source_url: str = ""
    expires_at: str | None = None
    origin: str = "manual"
    actor: str = "cli"
    force: bool = False
    extra: dict = field(default_factory=dict)


def guard_metadata(note: str, source_url: str) -> None:
    """拒绝把看起来像凭据的东西塞进明文的 note / source_url 列。"""
    for label, text in (("note", note), ("source_url", source_url)):
        if text and _CREDENTIAL_SMELL.search(text):
            raise errors.RefusedError(
                f"{label} 里出现了看起来像凭据的内容，拒绝写入",
                hint=f"{label} 是明文可搜索列，任何人读到 vault.db 都能看见。"
                     "要存密钥请用 kv add 建一条正式记录",
            )


def suggest_name(conn: sqlite3.Connection, platform: str, key_name: str | None) -> str:
    """生成一个不冲突的名字。key_name 优先（它通常就是用户想要的标识）。

    下划线转成连字符：记录名是 CLI 句柄，敲起来 `-` 比 `_` 顺手。原始环境变量名
    完整保存在 key_name 列里，kv use 注入 .env 时用的是那个，不是这个名字。
    """
    if key_name:
        base = re.sub(r"[^A-Za-z0-9.-]+", "-", key_name).replace("_", "-").lower()
        base = base.strip("-.")[:48] or platform
    else:
        base = (platform or "secret").lower()
    candidate = base
    suffix = 2
    while repo.get_secret_by_name(conn, candidate) is not None or repo.find_by_alias(conn, candidate):
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _merge_into_existing(
    conn: sqlite3.Connection,
    existing: sqlite3.Row,
    policy: SavePolicy,
    mask_parts: tuple[str, str],
) -> SaveResult:
    """指纹命中已有记录：合并标签、补别名、touch updated_at，绝不插第二行。

    UNIQUE(sha256) 是两个并发 kv watch 进程的竞态护栏 —— IntegrityError 会被
    commit() 捕获并路由回这里。
    """
    secret_id = int(existing["id"])
    added = []
    for tag in policy.tags:
        if tag not in repo.tags_for(conn, secret_id):
            repo.attach_tag(conn, secret_id, tag)
            added.append(tag)

    if policy.name and policy.name.lower() != existing["name"].lower():
        if repo.get_secret_by_name(conn, policy.name) is None and not repo.find_by_alias(conn, policy.name):
            repo.insert_alias(conn, secret_id, policy.name)
            added.append(f"alias:{policy.name}")

    fields: dict = {}
    if policy.note and not existing["note"]:
        fields["note"] = policy.note
    if policy.source_url and not existing["source_url"]:
        fields["source_url"] = policy.source_url
    if policy.expires_at and not existing["expires_at"]:
        fields["expires_at"] = policy.expires_at
    head, tail = mask_parts
    if head and not existing["mask_head"]:
        fields["mask_head"] = head
    if tail and not existing["mask_tail"]:
        fields["mask_tail"] = tail
    if fields:
        repo.update_secret(conn, secret_id, fields)
    repo.touch(conn, secret_id)

    warning = ""
    if existing["status"] == STATUS_REVOKED:
        revoked = repo.list_fingerprints(conn, secret_id)
        when = next((r["revoked_at"] for r in revoked if r["revoked_at"]), "")
        warning = (
            f"这个值在 {when or '某个时点'} 被吊销过 —— 你正在重新添加一把死 key。"
            "确认它真的重新生效了，否则去厂商控制台轮换"
        )

    auditlog.append(
        conn, "dedupe_hit", policy.actor, secret_id=secret_id,
        name_snapshot=existing["name"], sha256_prefix=existing["sha256"][:12],
        detail={"merged": len(added), "revoked_readd": bool(warning)},
    )
    return SaveResult("deduped", secret_id, existing["name"], tuple(added), warning)


def _new_secret_row(
    cand: Candidate,
    verdict: Verdict,
    policy: SavePolicy,
    *,
    name: str,
    kind: str,
    canonical: bytes,
    sha256: str,
    mask_parts: tuple[str, str],
    blob: bytes,
    now: str,
) -> dict:
    return {
        "name": name,
        "platform": verdict.platform,
        # --force 存下一个不可信的判定时，置信度记 manual 而不是 none：
        # 是「人拍板存进来的」，不是「机器没认出来」。半年后看这一列才有意义。
        "confidence": CONFIDENCE_MANUAL if policy.force and not verdict.trusted else verdict.confidence,
        "evidence": verdict.evidence,
        "detect_source": verdict.source,
        "key_name": cand.key_name,
        "kind": kind,
        "value_blob": blob,
        "sha256": sha256,
        "mask_head": mask_parts[0],
        "mask_tail": mask_parts[1],
        "value_len": len(canonical),
        "status": "active",
        "note": policy.note,
        "source_url": policy.source_url,
        "origin": policy.origin,
        "extra_json": repo.canonical_json({**cand.extra, **policy.extra}),
        "created_at": now,
        "updated_at": now,
        "expires_at": policy.expires_at,
        "last_used_at": None,
        "last_revealed_at": None,
    }


def _resolve_name(
    conn: sqlite3.Connection, cand: Candidate, verdict: Verdict, policy: SavePolicy
) -> str:
    if not policy.name:
        return suggest_name(conn, verdict.platform, cand.key_name)
    if repo.get_secret_by_name(conn, policy.name) is not None:
        raise errors.AlreadyExistsError(
            f"已经有一条叫 {policy.name!r} 的记录，而且它不是同一个值",
            hint="换一个名字，或用 kv rotate 换掉那条记录的值",
        )
    return policy.name


def commit(
    cand: Candidate,
    verdict: Verdict,
    *,
    store,
    policy: SavePolicy | None = None,
) -> SaveResult:
    """把一个 Candidate + Verdict 落盘。返回 created / deduped / rejected。"""
    policy = policy or SavePolicy()
    guard_metadata(policy.note, policy.source_url)

    kind = cand.kind or verdict.kind or masking.KIND_TOKEN
    canonical = masking.canonicalize(cand.value, kind)
    if not canonical:
        return SaveResult("rejected", None, "", (), "值为空 —— 空值表示未配置，不该进库")

    if not verdict.trusted and not policy.force:
        raise errors.RefusedError(
            f"识别结果不可信，拒绝落盘：{verdict.evidence or '没有任何规则匹配'}",
            hint="用 --platform 明确指定平台，或加 --force 强行保存",
        )

    sha256 = masking.fingerprint(canonical, kind)
    # 存头尾两段而不是合成好的掩码串：掩码字符于是成为纯显示偏好，
    # 改配置立刻生效，不需要重算全库。
    mask_parts = masking.split_mask(canonical, *verdict.mask_style)
    blob = store.encrypt(canonical)
    now = clock.now_iso()

    with store.session() as conn:
        existing = repo.get_secret_by_sha256(conn, sha256)
        if existing is not None:
            return _merge_into_existing(conn, existing, policy, mask_parts)

        name = _resolve_name(conn, cand, verdict, policy)
        secret_id = repo.insert_secret(conn, row=_new_secret_row(
            cand, verdict, policy, name=name, kind=kind, canonical=canonical,
            sha256=sha256, mask_parts=mask_parts, blob=blob, now=now,
        ))
        repo.insert_fingerprint(
            conn, secret_id=secret_id, sha256=sha256,
            mask_head=mask_parts[0], mask_tail=mask_parts[1],
            value_len=len(canonical), status="active", value_blob=blob,
            first_seen_at=now,
        )
        for tag in policy.tags:
            repo.attach_tag(conn, secret_id, tag)
        auditlog.append(
            conn, "save", policy.actor, secret_id=secret_id, name_snapshot=name,
            sha256_prefix=sha256[:12],
            detail={
                "platform": verdict.platform,
                "confidence": verdict.confidence,
                "len": len(canonical),
                "origin": policy.origin,
                "tags": len(policy.tags),
            },
        )

    return SaveResult("created", secret_id, name, policy.tags)
