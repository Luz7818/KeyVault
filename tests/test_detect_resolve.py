"""detect.resolve —— 分级优先级、歧义诚实、纠正注入。

纯函数测试：不建库、不碰剪切板、不碰 DPAPI。CorrectionSet 直接构造注入，
这正是「detect 不 import core.repo」那条边界换来的好处。
"""

from __future__ import annotations

import re
import unittest

import tests  # noqa: F401

from kv.detect import rules
from kv.detect.corrections import CorrectionSet, group_correction, suggest_correction_scope
from kv.detect.resolve import resolve
from kv.model import Candidate

SK = b"sk-" + b"A" * 48
GHP = b"ghp_" + b"B" * 36
HEX32 = b"0123456789abcdef" * 2


def cand(value=SK, key_name=None, hostnames=(), kind="token") -> Candidate:
    return Candidate(value=value, kind=kind, key_name=key_name, hostnames=tuple(hostnames))


class TestShapeStage(unittest.TestCase):
    def test_unique_shape_is_conclusive(self):
        v = resolve(cand(GHP))
        self.assertEqual((v.platform, v.confidence, v.source), ("github", "medium", "shape"))

    def test_specific_shape_beats_generic_at_same_stage(self):
        """sk-proj- 同时匹配 openai-project(88) 和裸 sk-(30)，specificity 决出胜负。"""
        v = resolve(cand(b"sk-proj-" + b"A" * 40))
        self.assertEqual(v.platform, "openai-project")
        self.assertEqual(v.confidence, "medium")
        self.assertNotIn("openai-compatible", v.candidates)

    def test_bare_sk_is_ambiguous_not_guessed(self):
        v = resolve(cand())
        self.assertEqual(v.platform, "openai-compatible")
        self.assertEqual(v.confidence, "ambiguous")
        self.assertEqual(
            set(v.candidates),
            {"dashscope", "deepseek", "moonshot", "openai", "siliconflow", "zhipu"},
        )
        self.assertIn("无法区分", v.evidence)

    def test_ambiguous_verdict_is_still_trusted(self):
        """ambiguous 可以落盘 —— 它诚实，落进 openai-compatible 而不是猜一个厂商。"""
        self.assertTrue(resolve(cand()).trusted)

    def test_needs_context_shape_refused_without_evidence(self):
        v = resolve(cand(HEX32))
        self.assertEqual(v.platform, "unknown")
        self.assertEqual(v.confidence, "none")
        self.assertFalse(v.trusted)
        self.assertIn("佐证", v.evidence)

    def test_evidence_never_contains_the_value(self):
        for value in (SK, GHP, HEX32, b"sk-proj-" + b"A" * 40):
            with self.subTest(len=len(value)):
                v = resolve(cand(value))
                for field in (v.evidence, v.platform, v.source):
                    self.assertNotIn("A" * 12, field)


class TestPrecedence(unittest.TestCase):
    def test_hostname_beats_keyname(self):
        v = resolve(cand(SK, key_name="OPENAI_API_KEY", hostnames=("api.deepseek.com",)))
        self.assertEqual((v.platform, v.confidence, v.source), ("deepseek", "exact", "hostname"))

    def test_keyname_beats_ambiguous_shape(self):
        v = resolve(cand(SK, key_name="DEEPSEEK_API_KEY"))
        self.assertEqual((v.platform, v.confidence, v.source), ("deepseek", "high", "keyname"))

    def test_keyname_beats_window_title(self):
        """窗口标题是**环境**证据，不能推翻附着在值上的键名。"""
        v = resolve(cand(SK, key_name="OPENAI_API_KEY"), window_title="DeepSeek API开放平台")
        self.assertEqual(v.platform, "openai")
        self.assertEqual(v.source, "keyname")

    def test_shape_beats_window_title(self):
        v = resolve(cand(GHP), window_title="DeepSeek API开放平台")
        self.assertEqual(v.platform, "github")
        self.assertEqual(v.source, "shape")

    def test_window_rescues_ambiguous_shape(self):
        v = resolve(cand(), window_title="DeepSeek API开放平台 - Google Chrome")
        self.assertEqual((v.platform, v.confidence, v.source), ("deepseek", "high", "window"))

    def test_window_rescues_needs_context_shape(self):
        v = resolve(cand(HEX32), window_title="高德开放平台 - 应用管理")
        self.assertEqual((v.platform, v.source), ("amap", "window"))
        self.assertTrue(v.trusted)

    def test_window_cannot_contradict_the_value_shape(self):
        """实测出来的 bug：裸 sk- + 标题含 OpenAI 曾被判成 openai-project，
        只因为后者 specificity 更高，而 sk-proj- 形态根本不符。"""
        v = resolve(cand(), window_title="OpenAI API keys - ChatGPT")
        self.assertEqual(v.platform, "openai")
        self.assertNotEqual(v.platform, "openai-project")

    def test_conflicting_window_title_falls_back_to_ambiguous(self):
        v = resolve(cand(), window_title="DeepSeek 和 OpenAI 的对比评测")
        self.assertEqual(v.platform, "openai-compatible")
        self.assertEqual(v.confidence, "ambiguous")

    def test_window_evidence_does_not_leak_the_title(self):
        title = "内部项目-财务系统-DeepSeek 控制台"
        v = resolve(cand(), window_title=title)
        self.assertNotIn("财务", v.evidence)
        self.assertNotIn(title, v.evidence)

    def test_suffix_host_match(self):
        v = resolve(cand(SK, hostnames=("us-central1-ai.googleapis.com",)))
        self.assertEqual((v.platform, v.source), ("google", "hostname"))

    def test_no_signal_at_all(self):
        v = resolve(cand(b"totally-unrecognized-value-9x"))
        self.assertEqual((v.platform, v.confidence, v.source), ("unknown", "none", "none"))
        self.assertFalse(v.trusted)


def context_cand(value=SK, key_name=None, hostnames=(), context_hostnames=()) -> Candidate:
    return Candidate(value=value, key_name=key_name, hostnames=tuple(hostnames),
                     context_hostnames=tuple(context_hostnames))


class TestContextHostnameStage(unittest.TestCase):
    """块级主机名：弱于键名与形态，强于窗口标题。"""

    def test_rescues_ambiguous_bare_sk(self):
        v = resolve(context_cand(context_hostnames=("api.deepseek.com",)))
        self.assertEqual((v.platform, v.confidence, v.source), ("deepseek", "high", "context"))

    def test_rescues_needs_context_hex32(self):
        v = resolve(context_cand(HEX32, context_hostnames=("restapi.amap.com",)))
        self.assertEqual((v.platform, v.source), ("amap", "context"))
        self.assertTrue(v.trusted)

    def test_loses_to_keyname(self):
        """这是分级存在的理由：块级主机名不许推翻附着在值上的键名。"""
        v = resolve(context_cand(SK, key_name="OPENAI_API_KEY",
                                 context_hostnames=("api.deepseek.com",)))
        self.assertEqual((v.platform, v.source), ("openai", "keyname"))

    def test_loses_to_unique_shape(self):
        v = resolve(context_cand(GHP, context_hostnames=("api.deepseek.com",)))
        self.assertEqual((v.platform, v.source), ("github", "shape"))

    def test_beats_window_title(self):
        """同一块文本里的证据比环境证据更相关。"""
        v = resolve(context_cand(context_hostnames=("api.deepseek.com",)),
                    window_title="OpenAI API keys")
        self.assertEqual((v.platform, v.source), ("deepseek", "context"))

    def test_direct_hostname_beats_context_hostname(self):
        v = resolve(context_cand(SK, hostnames=("api.openai.com",),
                                 context_hostnames=("api.deepseek.com",)))
        self.assertEqual((v.platform, v.source), ("openai", "hostname"))

    def test_conflicting_block_hosts_do_not_guess(self):
        v = resolve(context_cand(
            context_hostnames=("api.deepseek.com", "api.openai.com")))
        self.assertEqual(v.platform, "openai-compatible")
        self.assertEqual(v.confidence, "ambiguous")

    def test_unrecognized_value_with_block_host(self):
        """块里只提到 deepseek，值形态认不出 —— 仍然归给 deepseek。
        走到这一级说明键名和形态都没结论，「这块配置发往哪」是仅剩的证据。"""
        v = resolve(context_cand(b"abc123xyz-unrecognized",
                                 context_hostnames=("api.deepseek.com",)))
        self.assertEqual(v.platform, "deepseek")

    def test_context_correction_still_wins(self):
        corr = CorrectionSet(hostname={"llm.internal.example": "zhipu"})
        v = resolve(context_cand(context_hostnames=("llm.internal.example",)), corr)
        self.assertEqual((v.platform, v.confidence), ("zhipu", "manual"))


class TestCorrections(unittest.TestCase):
    def test_exact_correction_wins_over_everything(self):
        fp = cand().sha256()
        corr = CorrectionSet(exact={fp: ("deepseek", "2026-10-01T00:00:00+08:00")})
        v = resolve(cand(SK, key_name="OPENAI_API_KEY", hostnames=("api.openai.com",)), corr)
        self.assertEqual((v.platform, v.confidence, v.source), ("deepseek", "manual", "correction"))

    def test_keyname_correction_beats_builtin(self):
        corr = CorrectionSet(keyname=((re.compile(r"(?i)^LLM_API_KEY$"), "deepseek"),))
        v = resolve(cand(SK, key_name="LLM_API_KEY"), corr)
        self.assertEqual((v.platform, v.confidence), ("deepseek", "manual"))

    def test_keyname_correction_rescues_unrecognized_key(self):
        corr = CorrectionSet(keyname=((re.compile(r"(?i)^MY_VENDOR_KEY$"), "deepseek"),))
        v = resolve(cand(SK, key_name="MY_VENDOR_KEY"), corr)
        self.assertEqual(v.platform, "deepseek")

    def test_hostname_correction(self):
        corr = CorrectionSet(hostname={"llm.internal.example": "deepseek"})
        v = resolve(cand(SK, hostnames=("llm.internal.example",)), corr)
        self.assertEqual((v.platform, v.confidence), ("deepseek", "manual"))

    def test_window_correction(self):
        corr = CorrectionSet(window=(("内部推理网关", "deepseek"),))
        v = resolve(cand(), corr, window_title="内部推理网关 - 控制台")
        self.assertEqual((v.platform, v.source), ("deepseek", "window"))

    def test_group_default_resolves_ambiguity(self):
        corr = CorrectionSet(group={"openai_compatible": "deepseek"})
        v = resolve(cand(), corr)
        self.assertEqual((v.platform, v.confidence), ("deepseek", "manual"))

    def test_group_default_does_not_affect_unique_shapes(self):
        corr = CorrectionSet(group={"openai_compatible": "deepseek"})
        v = resolve(cand(GHP), corr)
        self.assertEqual(v.platform, "github")

    def test_empty_correction_set_changes_nothing(self):
        baseline = resolve(cand())
        self.assertEqual(resolve(cand(), CorrectionSet.empty()).platform, baseline.platform)

    def test_from_rows_ignores_bad_regex_instead_of_crashing(self):
        rows = [{"kind": "keyname", "pattern": "([unclosed", "platform": "deepseek",
                 "created_at": "t"}]
        corr = CorrectionSet.from_rows(rows)
        self.assertEqual(corr.keyname, ())

    def test_from_rows_reads_all_kinds(self):
        rows = [
            {"kind": "exact", "pattern": "a" * 64, "platform": "openai", "created_at": "t"},
            {"kind": "keyname", "pattern": "(?i)^FOO_KEY$", "platform": "deepseek", "created_at": "t"},
            {"kind": "hostname", "pattern": "H.Example.COM", "platform": "zhipu", "created_at": "t"},
            {"kind": "window", "pattern": "My Console", "platform": "moonshot", "created_at": "t"},
            {"kind": "group", "pattern": "openai_compatible", "platform": "siliconflow", "created_at": "t"},
        ]
        corr = CorrectionSet.from_rows(rows)
        self.assertEqual(corr.exact_platform("a" * 64)[0], "openai")
        self.assertEqual(corr.keyname_hits("FOO_KEY"), ["deepseek"])
        self.assertEqual(corr.hostname_hits(("x.h.example.com",)), ["zhipu"])
        self.assertEqual(corr.window_hits("my console is open"), ["moonshot"])
        self.assertEqual(corr.group_platform("openai_compatible"), "siliconflow")


class TestCorrectionScopeSuggestion(unittest.TestCase):
    def test_prefers_the_narrowest_useful_scope(self):
        c = cand(SK, key_name="DEEPSEEK_API_KEY", hostnames=("api.deepseek.com",))
        kind, pattern, _ = suggest_correction_scope(c, resolve(c))
        self.assertEqual((kind, pattern), ("keyname", "^DEEPSEEK_API_KEY$"))

    def test_falls_back_to_hostname(self):
        c = cand(SK, hostnames=("api.deepseek.com",))
        kind, pattern, _ = suggest_correction_scope(c, resolve(c))
        self.assertEqual((kind, pattern), ("hostname", "api.deepseek.com"))

    def test_group_default_is_never_suggested_silently(self):
        """组级默认是唯一能悄悄给未来每一把 OpenAI key 贴错标签的纠正。"""
        c = cand(SK)
        self.assertIsNone(suggest_correction_scope(c, resolve(c)))
        kind, pattern, warning = group_correction("openai_compatible", "deepseek")
        self.assertEqual((kind, pattern), ("group", "openai_compatible"))
        self.assertIn("个别记录可能是错的", warning)

    def test_pattern_is_regex_escaped(self):
        c = cand(SK, key_name="MY.KEY+NAME")
        _, pattern, _ = suggest_correction_scope(c, resolve(c))
        self.assertEqual(pattern, r"^MY\.KEY\+NAME$")


class TestRuleTableInvariants(unittest.TestCase):
    def test_platform_ids_are_unique(self):
        ids = [r.platform for r in rules.RULES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_exactly_one_ambiguous_shape_group(self):
        ambiguous = {s: m for s, m in rules.SHAPE_GROUPS.items() if len(m) > 1}
        self.assertEqual(len(ambiguous), 1)
        self.assertEqual(
            set(next(iter(ambiguous.values()))),
            {"dashscope", "deepseek", "moonshot", "openai", "siliconflow", "zhipu"},
        )

    def test_every_group_has_a_label_rule(self):
        for gid, label in rules.GROUP_LABEL.items():
            with self.subTest(group=gid):
                self.assertTrue(label)
                self.assertIn(label, rules.BY_PLATFORM)
                self.assertIsNone(rules.BY_PLATFORM[label].shape)

    def test_openai_project_is_not_in_the_ambiguous_group(self):
        self.assertEqual(rules.ambiguous_with("openai-project"), ())
        self.assertEqual(len(rules.ambiguous_with("openai")), 5)

    def test_table_hash_is_stable_and_changes_with_the_table(self):
        self.assertEqual(len(rules.TABLE_HASH), 64)
        self.assertEqual(rules.table_hash(), rules.TABLE_HASH)

    def test_no_shape_imposes_an_arbitrary_length_floor(self):
        """回归：不给凭据值设长度下限。曾经卡过 15 字符，一把真令牌刚好漏网。"""
        short = "Ab3xK9mQ2pL7vR1"  # 15 字符
        self.assertTrue(rules.shape_fits("unknown", short))  # 无 shape 的规则不拦
        for rule in rules.RULES:
            if rule.shape and "{16,}" in rule.shape:
                self.assertIn(rule.platform, {
                    "openai", "deepseek", "siliconflow", "moonshot", "zhipu",
                    "dashscope", "anthropic", "modelscope",
                }, f"{rule.platform} 的 shape 有长度下限，需要厂商格式确实要求它")

    def test_display_names_are_unique(self):
        names = [r.display for r in rules.RULES]
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
