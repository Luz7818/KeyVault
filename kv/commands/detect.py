"""detect / selftest —— 检测大脑的对外入口。

`kv detect` **不存储**：文本进、判定出。这是让人在把一把 key 存进库之前先看清
「它认为这是什么、凭什么这么认为、有多确定」。

`kv selftest --golden` 是失败关闭闸门的前半：跑完语料并记下 TABLE_HASH，之后
kv scan 才肯出结论。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from kv import PROGRAM, console, errors
from kv.detect import report, resolve, rules
from kv.detect.corrections import CorrectionSet
from kv.model import Candidate, Rejection, Verdict
from kv.parse import pipeline
from kv.selftest import DEFAULT_CORPUS, GATE_SETTING, gate_message, run_golden


def read_input(args) -> str:
    if getattr(args, "text", ""):
        return args.text
    if getattr(args, "file", ""):
        return Path(args.file).read_text(encoding="utf-8", errors="replace")
    if getattr(args, "stdin", False):
        return sys.stdin.read()
    raise errors.UsageError("要检测什么？给 --text、--file 或 --stdin")


def load_corrections(store) -> CorrectionSet:
    from kv.core import repo

    if store is None:
        return CorrectionSet.empty()
    with store.read() as conn:
        return CorrectionSet.from_rows(repo.list_corrections(conn))


def cmd_detect(args) -> int:
    text = read_input(args)
    result = pipeline.parse(text, source_hint="detect")

    corrections = CorrectionSet.empty()
    if not args.no_vault:
        try:
            from kv.commands.common import open_store

            corrections = load_corrections(open_store(args))
        except errors.KvError:
            pass  # 没有 vault 也能检测 —— detect 是纯函数

    verdicts = [
        resolve.resolve(c, corrections, window_title=args.window_title or None)
        for c in result.candidates
    ]

    if args.json:
        console.echo(json.dumps(
            [_candidate_dict(c, v) for c, v in zip(result.candidates, verdicts)]
            + [_rejection_dict(r) for r in result.rejected],
            ensure_ascii=False, indent=2,
        ))
        return 0

    if not result.candidates and not result.rejected:
        console.echo("没有发现任何像凭据的东西")
        return 0

    for index, (candidate, verdict) in enumerate(zip(result.candidates, verdicts), 1):
        _echo_candidate(index, len(result.candidates), candidate, verdict)
    for rejection in result.rejected:
        console.echo(f"  [拒绝] {rejection.span or '(无位置)'}：{rejection.reason} — {rejection.detail}")

    console.echo("")
    console.echo(
        f"共 {len(result.candidates)} 个候选、{len(result.rejected)} 条拒绝。"
        "detect 不写库；要存用 kv add 或 kv watch"
    )
    ambiguous = sum(1 for v in verdicts if v.confidence == "ambiguous")
    refused = sum(1 for v in verdicts if v.confidence == "none")
    if ambiguous:
        console.warn(f"{ambiguous} 个候选形态歧义 —— 存的时候会标成 openai-compatible，不会猜厂商")
    if refused:
        console.warn(f"{refused} 个候选识别不出平台 —— 存需要 --platform 或 --force")
    return 0


def _echo_candidate(index: int, total: int, candidate: Candidate, verdict: Verdict) -> None:
    preview = candidate.preview(console.mask_char(), *verdict.mask_style)
    console.echo(f"[{index}/{total}] {preview}   {candidate.span}")
    for line in report.describe_long(verdict):
        console.echo(f"        {line}")
    if candidate.key_name:
        console.echo(f"        原键名      {candidate.key_name}")
    if candidate.hostnames:
        console.echo(f"        主机名      {', '.join(candidate.hostnames)}（直接关联）")
    if candidate.context_hostnames:
        console.echo(f"        同块主机名  {', '.join(candidate.context_hostnames)}（弱证据，排在键名与形态之后）")
    if candidate.kind != "token":
        console.echo(f"        类型        {candidate.kind}")
    if candidate.extra:
        console.echo(f"        附加        {json.dumps(candidate.extra, ensure_ascii=False)}")


def _candidate_dict(candidate: Candidate, verdict: Verdict) -> dict:
    """JSON 输出。**不含值** —— detect 是只读的，明文不该出现在它的输出里。"""
    return {
        "type": "candidate",
        "span": candidate.span,
        "source": candidate.source,
        "kind": candidate.kind,
        "key_name": candidate.key_name,
        "hostnames": list(candidate.hostnames),
        "context_hostnames": list(candidate.context_hostnames),
        "extra": candidate.extra,
        "value_len": len(candidate.canonical()),
        "sha256": candidate.sha256(),
        "preview": candidate.preview("*", *verdict.mask_style),
        "platform": verdict.platform,
        "platform_display": rules.display_name(verdict.platform),
        "confidence": verdict.confidence,
        "source_stage": verdict.source,
        "evidence": verdict.evidence,
        "candidates": list(verdict.candidates),
    }


def _rejection_dict(rejection: Rejection) -> dict:
    return {
        "type": "rejected",
        "span": rejection.span,
        "reason": rejection.reason,
        "detail": rejection.detail,
    }


def cmd_selftest(args) -> int:
    if not args.golden:
        console.echo(f"检测表：{len(rules.RULES)} 条规则，指纹 {rules.TABLE_HASH[:16]}")
        console.echo(f"歧义组：{ {g: sorted(m) for g, m in rules.GROUPS.items()} }")
        console.echo("跑语料自检：kv selftest --golden")
        return 0

    corpus = Path(args.corpus) if args.corpus else DEFAULT_CORPUS
    if not corpus.is_dir():
        raise errors.UsageError(f"找不到语料目录：{corpus}")

    result = run_golden(corpus)
    console.echo(f"语料目录  {corpus}")
    console.echo(f"用例数    {result.total}")
    console.echo(f"检测表    {rules.TABLE_HASH[:16]}")

    if result.ok:
        console.ok(f"{result.total} 条全 PASS")
        if not args.no_record:
            _record_gate(args, result.table_hash)
        return 0

    if result.total == 0:
        console.error(f"语料目录里一条用例都没有：{corpus}")
        console.hint(
            "零条用例不算通过 —— 否则闸门会记下「已验证」，而检测器可以从此静默失效。"
            f"确认路径对不对（默认是 {DEFAULT_CORPUS}）"
        )
        return errors.EXIT_GATE

    console.error(f"{len(result.failures)} 条不符：")
    # 只打印用例 id 与期望/实际标签 —— 语料全是合成的，但纪律不该依赖这一点。
    for failure in result.failures:
        console.echo(f"  {failure.case_id}")
        console.echo(f"      {failure.field}: 期望 {failure.expected} / 实际 {failure.actual}")
    return errors.EXIT_GATE


def _record_gate(args, table_hash: str) -> None:
    """把「这份表通过了自检」记进 vault。kv scan 之后会来查它。"""
    try:
        from kv.commands.common import open_store

        store = open_store(args)
    except errors.KvError:
        console.warn("vault 未初始化，跳过记录闸门（kv scan 届时会拒绝运行）")
        return
    store.set_setting(GATE_SETTING, table_hash)
    console.echo(f"已记录闸门：{GATE_SETTING} = {table_hash[:16]}")


def gate_status(store) -> tuple[bool, str]:
    """给 kv scan 用：闸门是否当前。返回 (通过, 消息)。"""
    stored = store.get_setting(GATE_SETTING)
    if not stored:
        return False, "还没跑过 golden 自检，拒绝给出结论。先跑：kv selftest --golden"
    if stored != rules.TABLE_HASH:
        return False, gate_message()
    return True, ""
