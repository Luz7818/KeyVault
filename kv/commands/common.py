"""命令实现共用的胶水：打开 store、定位 vault、读入值、组装 SavePolicy。

这些函数是所有命令模块的公共入口，所以放在这里而不是复制进每一个文件。
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from kv import PROGRAM, console, paths
from kv.core import masking
from kv.core.vault import VaultStore
from kv.detect import resolve
from kv.detect.corrections import CorrectionSet
from kv.errors import KvError, UsageError
from kv.model import Candidate, Verdict
from kv.ops import save as saveops
from kv.parse import pipeline


def db_path(args) -> Path:
    override = getattr(args, "vault_dir", "") or ""
    if override:
        return (Path(override).expanduser() / "vault.db").resolve()
    return paths.vault_db()


def open_store(args) -> VaultStore:
    """打开并校验 vault。未初始化或绑定失效都在这里响亮地失败。"""
    store = VaultStore(db_path(args))
    store.require_initialized()
    store.verify_binding()
    return store


def read_value(args) -> bytes:
    """从 --value / --value-file / --stdin / 隐藏提示读入。绝不回显。"""
    if getattr(args, "value", None):
        console.warn(
            "--value 会把密钥写进 shell 历史和进程列表；"
            "改用 --value-file、--stdin，或不带参数走隐藏提示"
        )
        return args.value.encode("utf-8")
    if getattr(args, "value_file", None):
        return Path(args.value_file).read_bytes()
    if getattr(args, "stdin", False):
        # sys.stdin 不一定有 .buffer（被测试替换过、或某些重定向场景），退回文本读。
        binary = getattr(sys.stdin, "buffer", None)
        return binary.read() if binary is not None else sys.stdin.read().encode("utf-8")
    return console.hidden_input("密钥值（不回显）: ").encode("utf-8")


def policy_from_args(args, origin: str = "manual", actor: str = "cli") -> saveops.SavePolicy:
    return saveops.SavePolicy(
        name=getattr(args, "name", "") or "",
        tags=tuple(getattr(args, "tag", None) or ()),
        note=getattr(args, "note", "") or "",
        source_url=getattr(args, "source_url", "") or "",
        expires_at=getattr(args, "expires_at", None),
        origin=origin,
        actor=actor,
        force=getattr(args, "force", False),
    )


def manual_verdict(args, value: bytes) -> Verdict:
    """显式 --platform：用户拍板，置信度记 manual。"""
    kind = getattr(args, "kind", "") or masking.KIND_TOKEN
    return Verdict(
        platform=args.platform,
        confidence="manual",
        source="correction",
        evidence=f"{PROGRAM} add --platform {args.platform}",
        mask_style=masking.infer_mask_style(value),
        kind=kind,
    )


def detect_for_add(args, value: bytes, corrections=None) -> tuple[Candidate, Verdict]:
    """给 kv add 用：永远返回可用的 (候选, 判定)。

    自动识别只在**恰好一个候选**时生效。多个候选意味着粘进来的是一整段 .env 或
    JSON —— 那时 add 拒绝并列出找到了什么，而不是悄悄只存第一条（悄悄丢弃是
    比报错严重得多的失败）。
    """
    if getattr(args, "platform", ""):
        return candidate_from_value(args, value), manual_verdict(args, value)

    text = value.decode("utf-8", "replace")
    result = pipeline.parse(text, source_hint="add")

    if len(result.candidates) == 1:
        candidate = result.candidates[0]
        overrides = {}
        if getattr(args, "kind", ""):
            overrides["kind"] = args.kind
        if getattr(args, "key_name", ""):
            overrides["key_name"] = args.key_name
        if overrides:
            candidate = replace(candidate, **overrides)
        verdict = resolve.resolve(
            candidate, corrections or CorrectionSet.empty(),
            window_title=getattr(args, "window_title", "") or None,
        )
        return candidate, verdict

    if len(result.candidates) > 1:
        listing = "\n".join(
            f"    {i}. {c.preview(console.mask_char())}"
            f"  键名 {c.key_name or '(无)'}  来自 {c.span}"
            for i, c in enumerate(result.candidates, 1)
        )
        raise UsageError(
            f"这段文本里有 {len(result.candidates)} 个凭据，kv add 一次只存一个",
            hint="先只复制你要存的那一个；或用 kv detect 看清全貌。\n"
                 f"  找到的是：\n{listing}\n"
                 "  批量入库用 kv import（M3 提供）",
        )

    return candidate_from_value(args, value), fallback_verdict(value)


def fallback_verdict(value: bytes) -> Verdict:
    """什么都没认出来。**不猜平台** —— 落盘需要 --platform 或 --force。"""
    return Verdict(
        platform="unknown",
        confidence="none",
        source="none",
        evidence="没有任何规则匹配；用 --platform 指定，或 --force 存成 unknown",
        mask_style=masking.infer_mask_style(value),
        kind="token",
    )


def load_corrections(args) -> CorrectionSet:
    """读用户纠正规则。没有 vault 就返回空集 —— 检测是纯函数，不该要求先建库。"""
    try:
        from kv.core import repo

        store = open_store(args)
    except KvError:
        return CorrectionSet.empty()
    with store.read() as conn:
        return CorrectionSet.from_rows(repo.list_corrections(conn))


def candidate_from_value(args, value: bytes, source: str = "manual") -> Candidate:
    return Candidate(
        value=value,
        kind=getattr(args, "kind", "") or masking.KIND_TOKEN,
        key_name=getattr(args, "key_name", None) or None,
        source=source,
        span=f"{PROGRAM} add",
    )


def row_to_dict(row) -> dict:
    """JSON 输出。**不含 value_blob** —— 明文值只能经 kv reveal 出现在终端上。"""
    return {
        "id": row.id, "name": row.name, "platform": row.platform,
        "confidence": row.confidence, "evidence": row.evidence,
        "detect_source": row.detect_source, "key_name": row.key_name, "kind": row.kind,
        "sha256": row.sha256, "masked": row.preview(console.mask_char()),
        "value_len": row.value_len,
        "status": row.status, "note": row.note, "source_url": row.source_url,
        "origin": row.origin, "extra": row.extra, "created_at": row.created_at,
        "updated_at": row.updated_at, "expires_at": row.expires_at,
        "last_used_at": row.last_used_at, "last_revealed_at": row.last_revealed_at,
        "tags": list(row.tags), "aliases": list(row.aliases),
    }
