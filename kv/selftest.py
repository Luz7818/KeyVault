"""golden 自检与失败关闭闸门。

检测器静默失效是本设计最贵的失败模式：它不再匹配任何东西，却仍然报告「全仓干净」。
现有工程出过三次这种事（把 (?i) 内联进 git grep -E、给凭据值卡长度下限、
「不是凭据」白名单过宽）。

所以这里做两件事：

1. **跑语料**，任何一条不符就退出码非 0，且只打印用例 id 与期望/实际的平台标签
   —— 绝不打印 fixture 值（语料全是合成的，但纪律不该依赖这一点）。
2. **记下通过时的 TABLE_HASH**。之后 kv scan 会拒绝在「表变了而自检没重跑」的
   情况下运行。这个不对称是故意的：scan 的假阴性静默且昂贵，而保存时的误判
   可见且可用 kv fix 修。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from kv.detect import resolve, rules
from kv.detect.corrections import CorrectionSet
from kv.parse import pipeline

GATE_SETTING = "golden_passed_for"

DEFAULT_CORPUS = Path(__file__).resolve().parent.parent / "tests" / "corpus"

# 合成 fixture 的配方。写死在这里，语料文件就只是可读的占位符，
# 既安全可提交、也可安全打印。
SYNTHETIC = {
    "<48位合成>": "A" * 48,
    "<48>": "A" * 48,
    "<32hex>": "0123456789abcdef" * 2,
    "<40hex>": "0123456789abcdef" * 2 + "01234567",
    "<64hex>": "0123456789abcdef" * 4,
    "<36位字母数字>": ("abcdefghij0123456789" * 2)[:36],
    "<35位字母数字>": ("abcdefghij0123456789" * 2)[:35],
    "<35>": ("abcdefghij0123456789" * 2)[:35],
    "<36>": ("abcdefghij0123456789" * 2)[:36],
    "<20位字母数字>": ("abcdefghij0123456789" * 2)[:20],
    "<20>": ("abcdefghij0123456789" * 2)[:20],
    "<16位大写>": "ABCDEFGHIJKLMNOP",
    "<40位字母数字>": ("abcdefghij0123456789" * 2)[:40],
    "<合成 200 字符 base64>": "MIIEvA" + "A" * 194,
    "<24位>": "aB3xK9mQ2pL7vR1sT4uW6y",
}


def expand(text: str) -> str:
    for placeholder, value in SYNTHETIC.items():
        text = text.replace(placeholder, value)
    return text


@dataclass(frozen=True)
class CaseFailure:
    case_id: str
    field: str
    expected: str
    actual: str


@dataclass
class GoldenResult:
    total: int = 0
    failures: list[CaseFailure] = field(default_factory=list)
    table_hash: str = ""

    @property
    def ok(self) -> bool:
        # 零条用例**不算通过**。一个空语料目录会让闸门记下「已验证」，
        # 而检测器可以从此静默失效却一切显示正常 —— 那正是本模块存在的理由
        # 要防的失败模式。失败必须是响亮的。
        return self.total > 0 and not self.failures


def load_cases(corpus_dir: Path) -> list[dict]:
    cases = []
    for path in sorted(Path(corpus_dir).glob("*.jsonl")):
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                case = json.loads(stripped)
            except ValueError as exc:
                cases.append({"id": f"{path.name}:{number}", "_parse_error": str(exc)})
                continue
            case.setdefault("id", f"{path.name}:{number}")
            case["_file"] = path.name
            cases.append(case)
    return cases


class Check:
    """收集一个用例的不符项。失败信息里只放标签与计数，不放 fixture 值本身。"""

    def __init__(self, case_id: str):
        self.case_id = case_id
        self.failures: list[CaseFailure] = []

    def eq(self, field_name: str, expected, actual) -> None:
        if expected != actual:
            self.failures.append(
                CaseFailure(self.case_id, field_name, str(expected), str(actual))
            )

    def fail(self, field_name: str, expected: str, actual: str) -> None:
        self.failures.append(CaseFailure(self.case_id, field_name, expected, actual))


def evaluate(case: dict) -> list[CaseFailure]:
    """跑一个用例，返回不符项。"""
    if "_parse_error" in case:
        return [CaseFailure(case["id"], "json", "可解析", case["_parse_error"])]

    text = expand(case.get("input", ""))
    result = pipeline.parse(text, source_hint="golden")
    verdicts = [
        resolve.resolve(c, CorrectionSet.empty(), window_title=case.get("window_title"))
        for c in result.candidates
    ]
    check = Check(case["id"])

    if not _check_acceptance(case, result, check):
        return check.failures
    _check_verdict(case, result, verdicts, check)
    _check_structure(case, result, verdicts, check)
    return check.failures


def _check_acceptance(case: dict, result, check: Check) -> bool:
    """expect / expect_reason / expect_count。返回 False 表示后面的检查没意义了。"""
    if "expect" in case:
        check.eq("expect", case["expect"], "accepted" if result.candidates else "rejected")
        if case["expect"] == "rejected":
            if "expect_reason" in case:
                reasons = {r.reason for r in result.rejected}
                if case["expect_reason"] not in reasons:
                    check.fail("expect_reason", case["expect_reason"],
                               ",".join(sorted(reasons)) or "(无拒绝记录)")
            return False
    if "expect_count" in case:
        check.eq("expect_count", case["expect_count"], len(result.candidates))
    return bool(result.candidates)


def _check_verdict(case: dict, result, verdicts, check: Check) -> None:
    """平台、置信度、判定来源 —— 分级优先级的断言都在这里。"""
    if not verdicts:
        return
    first = verdicts[0]
    platforms = [v.platform for v in verdicts]

    if "expect_platforms" in case:
        check.eq("expect_platforms", sorted(case["expect_platforms"]), sorted(platforms))
    if "expect_platform" in case:
        check.eq("expect_platform", case["expect_platform"], first.platform)
    if "expect_confidence" in case:
        check.eq("expect_confidence", case["expect_confidence"], first.confidence)
    if "expect_source" in case:
        check.eq("expect_source", case["expect_source"], first.source)
    if "expect_not" in case and case["expect_not"] in platforms:
        check.fail("expect_not", f"不得出现 {case['expect_not']}", ",".join(platforms))
    if "expect_candidates_min" in case and len(first.candidates) < case["expect_candidates_min"]:
        check.fail("expect_candidates_min", f">={case['expect_candidates_min']}",
                   str(len(first.candidates)))
    if case.get("expect_refused") and first.confidence != "none":
        check.fail("expect_refused", "confidence=none（拒绝落盘）", first.confidence)


def _check_structure(case: dict, result, verdicts, check: Check) -> None:
    """kind / extra / pair —— 结构解析的断言。"""
    if not verdicts:
        return
    first_candidate = result.candidates[0]
    if "expect_kind" in case:
        check.eq("expect_kind", case["expect_kind"], first_candidate.kind)
    if "expect_extra_has" in case and case["expect_extra_has"] not in first_candidate.extra:
        check.fail("expect_extra_has", case["expect_extra_has"],
                   ",".join(sorted(first_candidate.extra)) or "(空)")
    if case.get("expect_pair"):
        pair_ids = {c.extra.get("pair_id") for c in result.candidates if c.extra.get("pair_id")}
        if len(pair_ids) != 1 or len(result.candidates) < 2:
            check.fail("expect_pair", "两条记录共享一个 pair_id",
                       f"{len(result.candidates)} 条 / {len(pair_ids)} 个 pair_id")


def run_golden(corpus_dir: Path | str | None = None) -> GoldenResult:
    directory = Path(corpus_dir) if corpus_dir else DEFAULT_CORPUS
    cases = load_cases(directory)
    result = GoldenResult(total=len(cases), table_hash=rules.TABLE_HASH)
    for case in cases:
        result.failures.extend(evaluate(case))
    return result


def gate_is_current(store) -> bool:
    """kv scan 的前置闸。表变了而自检没重跑 → 拒绝出结论。"""
    return store.get_setting(GATE_SETTING) == rules.TABLE_HASH


def record_gate(store) -> None:
    store.set_setting(GATE_SETTING, rules.TABLE_HASH)


def gate_message() -> str:
    return (
        "检测表在上次 golden 自检之后变过，拒绝给出结论 —— "
        "一个静默失效的检测器会报告「全仓干净」。\n"
        f"       先跑：kv selftest --golden"
    )
