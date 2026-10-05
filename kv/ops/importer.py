"""批量摄入：.env 文件、AWS credentials.csv。

**不做任何新解析** —— 每个候选独立走 parse → detect → saveops.commit 的现有接缝。
importer 只负责编排：读文件、选解析器、收集结果、报告去重计数。

同一个 .env 导入两次 → 零条新行并报告去重计数。这是靠 saveops.commit 里的
sha256 去重实现的，importer 不需要自己做任何去重逻辑。

AWS credentials.csv 的两条记录用 pair_id 关联。access_key_id 是标识符，
secret_access_key 才是机密。两者分别存成独立记录，extra_json 里的 pair_id
把它们连起来。kv use 注入时按 key_name 写入，pair 关系只是元数据。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from kv import clock, console
from kv.core import audit as auditlog
from kv.core import masking
from kv.core.vault import VaultStore
from kv.detect import resolve
from kv.detect.corrections import CorrectionSet
from kv.model import Candidate, ImportResult, Verdict
from kv.ops import save as saveops
from kv.parse import awscsv, dotenv, pipeline


def import_file(
    path: Path,
    store: VaultStore,
    *,
    corrections: CorrectionSet | None = None,
    tags: tuple[str, ...] = (),
    force: bool = False,
    actor: str = "cli",
    window_title: str = "",
) -> ImportResult:
    """读一个文件，解析、识别、逐条提交。返回汇总结果。

    格式自动检测：先试 AWS CSV（看表头），再试 .env（看 KEY=value 行数），
    最后退回通用解析。检测失败 → 把整段文本交给 pipeline.parse，让它兜底。
    """
    corrections = corrections or CorrectionSet.empty()
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return ImportResult(errors=("文件为空",))

    source = f"import:{path.name}"
    candidates = _parse_file(text, source)
    if not candidates:
        return ImportResult(errors=("没有识别出凭据候选",))

    return _commit_batch(
        candidates, store, corrections,
        tags=tags, force=force, actor=actor,
        window_title=window_title, source=source,
    )


def import_text(
    text: str,
    store: VaultStore,
    *,
    corrections: CorrectionSet | None = None,
    tags: tuple[str, ...] = (),
    force: bool = False,
    actor: str = "cli",
    source: str = "import:text",
) -> ImportResult:
    """从一段文本导入。和 import_file 一样的逻辑，只是跳过文件读取。"""
    corrections = corrections or CorrectionSet.empty()
    if not text.strip():
        return ImportResult(errors=("文本为空",))

    candidates = _parse_file(text, source)
    if not candidates:
        return ImportResult(errors=("没有识别出凭据候选",))

    return _commit_batch(
        candidates, store, corrections,
        tags=tags, force=force, actor=actor,
        source=source,
    )


def _parse_file(text: str, source: str) -> list[Candidate]:
    """按格式选解析器。返回候选列表（不含 Rejection —— 批量导入静默跳过噪声）。"""
    # AWS CSV：看表头
    if awscsv.looks_like_aws_csv(text):
        return [c for c in awscsv.parse_awscsv(text, source=source)
                if isinstance(c, Candidate)]

    # .env：两行以上 KEY=value
    if dotenv.looks_like_dotenv(text):
        return [c for c in dotenv.parse_dotenv(text, source=source)
                if isinstance(c, Candidate)]

    # 兜底：通用解析链
    result = pipeline.parse(text, source_hint=source)
    return list(result.candidates)


def _commit_batch(
    candidates: list[Candidate],
    store: VaultStore,
    corrections: CorrectionSet,
    *,
    tags: tuple[str, ...],
    force: bool,
    actor: str,
    source: str,
    window_title: str = "",
) -> ImportResult:
    """逐条候选走 detect → commit。收集结果。"""
    counts = {"created": 0, "deduped": 0, "rejected": 0}
    errs: list[str] = []
    for candidate in candidates:
        _commit_one(candidate, store, corrections, tags, force, actor, window_title, counts, errs)
    _audit_batch(store, source, counts, len(candidates))
    return ImportResult(
        created=counts["created"], deduped=counts["deduped"],
        rejected=counts["rejected"], errors=tuple(errs),
    )


def _commit_one(candidate, store, corrections, tags, force, actor, window_title, counts, errs):
    """单条候选的 detect → commit。异常不向上抛 —— 记进 errs，继续下一条。"""
    verdict = resolve.resolve(candidate, corrections, window_title=window_title or None)
    policy = saveops.SavePolicy(
        tags=tags, origin="import", actor=actor,
        force=force or not verdict.trusted,
    )
    try:
        result = saveops.commit(candidate, verdict, store=store, policy=policy)
    except Exception as exc:
        errs.append(f"{candidate.key_name or candidate.span}: {exc}")
        counts["rejected"] += 1
        return
    counts[result.outcome if result.outcome in counts else "rejected"] += 1


def _audit_batch(store, source, counts, total):
    """一条审计行记整批，而不是每条候选一条。"""
    with store.session() as conn:
        auditlog.append(
            conn, "import", "cli",
            detail={"source": source, "created": counts["created"],
                    "deduped": counts["deduped"], "rejected": counts["rejected"],
                    "total_candidates": total},
        )
