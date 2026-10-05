"""Verdict → 一行人话。

置信度**到处都要显出来**，绝不藏。半年后翻库时，你必须能一眼分清哪条是确定的、
哪条是它猜的 —— 这正是「歧义要如实报告」这个决定要保护的东西。

标记：= 确定（manual / exact）  + 强（high）  ~ 中（medium）
      ? 歧义（ambiguous）        ! 没认出来（none）
"""

from __future__ import annotations

from kv.detect import rules
from kv.model import CONFIDENCE_AMBIGUOUS, CONFIDENCE_NONE, Verdict

MARKERS = {
    "manual": "=", "exact": "=", "high": "+",
    "medium": "~", "ambiguous": "?", "none": "!",
}

CONFIDENCE_LABELS = {
    "manual": "用户指定", "exact": "确定", "high": "强",
    "medium": "中", "ambiguous": "歧义", "none": "未识别",
}


def marker(verdict: Verdict) -> str:
    return MARKERS.get(verdict.confidence, "?")


def summarize(verdict: Verdict) -> str:
    """一行摘要，如 `deepseek= (主机名 api.deepseek.com)`。"""
    label = rules.display_name(verdict.platform)
    head = f"{verdict.platform}{marker(verdict)}"
    if verdict.confidence == CONFIDENCE_AMBIGUOUS and verdict.candidates:
        return f"{head}  候选：{', '.join(verdict.candidates)}"
    if verdict.evidence:
        return f"{head}  {verdict.evidence}"
    return f"{head}  {label}"


def describe_long(verdict: Verdict) -> list[str]:
    """kv show / kv detect --verbose 用的多行说明。"""
    lines = [
        f"平台        {verdict.platform}（{rules.display_name(verdict.platform)}）",
        f"置信度      {verdict.confidence}（{CONFIDENCE_LABELS.get(verdict.confidence, '?')}）",
        f"判定来源    {verdict.source}",
        f"判定依据    {verdict.evidence or '（无）'}",
    ]
    if verdict.candidates:
        lines.append(f"歧义候选    {', '.join(verdict.candidates)}")
    return lines


def is_refusable(verdict: Verdict) -> bool:
    """这个判定能不能不落 --force 就存。

    ambiguous **可以**存 —— 它诚实地说自己不确定，落进 openai-compatible 而不是
    猜一个厂商。真正要拒绝的是 none：一条未知形态的裸 hex-32 存进去，就是把
    「没识别出来」伪装成「识别成了 unknown 平台」。
    """
    return verdict.confidence == CONFIDENCE_NONE
