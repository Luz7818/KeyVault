"""golden 语料跑批与失败关闭闸门。

闸门的意义：检测器静默失效是本设计最贵的失败模式 —— 它不再匹配任何东西，
却仍然报告「全仓干净」。TABLE_HASH 让「表变了而自检没重跑」变成一个硬停，
而不是一个运行时惊喜。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401

from kv import selftest
from kv.detect import rules
from kv.selftest import (
    DEFAULT_CORPUS,
    GATE_SETTING,
    CaseFailure,
    expand,
    gate_is_current,
    load_cases,
    record_gate,
    run_golden,
)


class _FakeStore:
    """只为闸门测试存在的存储替身。"""

    def __init__(self):
        self.settings: dict[str, str] = {}

    def get_setting(self, key, default=""):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value


class TestExpand(unittest.TestCase):
    def test_all_placeholders_expand(self):
        for placeholder in selftest.SYNTHETIC:
            with self.subTest(placeholder=placeholder):
                self.assertNotIn(placeholder, expand(placeholder))

    def test_expansion_lengths_match_their_names(self):
        self.assertEqual(len(expand("<48位合成>")), 48)
        self.assertEqual(len(expand("<32hex>")), 32)
        self.assertEqual(len(expand("<40hex>")), 40)
        self.assertEqual(len(expand("<64hex>")), 64)
        self.assertEqual(len(expand("<36位字母数字>")), 36)
        self.assertEqual(len(expand("<35位字母数字>")), 35)
        self.assertEqual(len(expand("<合成 200 字符 base64>")), 200)

    def test_hex_placeholders_are_really_hex(self):
        for placeholder in ("<32hex>", "<40hex>", "<64hex>"):
            with self.subTest(placeholder=placeholder):
                int(expand(placeholder), 16)

    def test_multiple_placeholders_in_one_string(self):
        out = expand("A=<32hex> B=<48位合成>")
        self.assertIn("0123456789abcdef" * 2, out)
        self.assertIn("A" * 48, out)

    def test_expansion_is_deterministic(self):
        self.assertEqual(expand("<32hex>"), expand("<32hex>"))


class TestCorpus(unittest.TestCase):
    def test_shipped_corpus_exists_and_is_sized(self):
        self.assertTrue(DEFAULT_CORPUS.is_dir())
        cases = load_cases(DEFAULT_CORPUS)
        self.assertGreaterEqual(len(cases), 90)

    def test_every_case_has_an_id_and_input(self):
        for case in load_cases(DEFAULT_CORPUS):
            with self.subTest(case=case.get("id")):
                self.assertIn("id", case)
                self.assertIn("input", case)
                self.assertNotIn("_parse_error", case)

    def test_case_ids_are_unique(self):
        ids = [c["id"] for c in load_cases(DEFAULT_CORPUS)]
        self.assertEqual(len(ids), len(set(ids)), "有重复的用例 id")

    def test_all_four_files_present(self):
        names = {p.name for p in DEFAULT_CORPUS.glob("*.jsonl")}
        self.assertEqual(
            names,
            {"01-shape.jsonl", "02-context.jsonl", "03-negative.jsonl", "04-structures.jsonl"},
        )

    def test_corpus_contains_no_real_looking_secret(self):
        """语料要能安全提交、安全打印。占位符展开前的文件里不该有长随机串。"""
        for path in DEFAULT_CORPUS.glob("*.jsonl"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=path.name):
                self.assertNotIn("A" * 20, text)
                self.assertNotIn("sk-ant-api03-", text.replace("sk-ant-api03-<48位合成>", ""))

    def test_negative_cases_are_first_class(self):
        """反例必须占相当比重 —— 只测正例的检测器会在放过真密钥时仍然全绿。"""
        cases = load_cases(DEFAULT_CORPUS)
        negatives = [c for c in cases if c.get("expect") == "rejected" or c.get("expect_refused")]
        self.assertGreaterEqual(len(negatives), len(cases) // 4)

    def test_three_known_bad_heuristics_each_have_a_regression_case(self):
        cases = {c["id"] for c in load_cases(DEFAULT_CORPUS)}
        self.assertIn("short-15-char-token-accepted", cases)          # 不设长度下限
        self.assertIn("bare-hex32-must-not-be-guessed", cases)        # 白名单要窄
        self.assertIn("deny-list-must-not-kill-aws-access-key-id", cases)


class TestGoldenRun(unittest.TestCase):
    def test_shipped_corpus_passes(self):
        result = run_golden(DEFAULT_CORPUS)
        self.assertTrue(result.ok, [f"{f.case_id}:{f.field}={f.actual}" for f in result.failures])
        self.assertGreaterEqual(result.total, 90)
        self.assertEqual(result.table_hash, rules.TABLE_HASH)

    def test_empty_corpus_does_not_pass(self):
        """零条用例**不算通过**。否则一个空目录就能让闸门记下「已验证」，
        而检测器从此静默失效却一切显示正常 —— 那正是本模块要防的失败模式。"""
        with tempfile.TemporaryDirectory() as tmp:
            result = run_golden(Path(tmp))
        self.assertEqual(result.total, 0)
        self.assertFalse(result.ok)

    def test_missing_corpus_dir_does_not_pass(self):
        result = run_golden(Path(tempfile.gettempdir()) / "kv-no-such-corpus-dir")
        self.assertFalse(result.ok)

    def test_broken_expectation_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp)
            (corpus / "bad.jsonl").write_text(
                '{"id":"deliberately-wrong","input":"ghp_' + "B" * 36 + '",'
                '"expect_platform":"deepseek"}\n',
                encoding="utf-8",
            )
            result = run_golden(corpus)
        self.assertFalse(result.ok)
        self.assertEqual(result.failures[0].case_id, "deliberately-wrong")
        self.assertEqual(result.failures[0].field, "expect_platform")
        self.assertEqual(result.failures[0].expected, "deepseek")
        self.assertEqual(result.failures[0].actual, "github")

    def test_malformed_json_line_is_reported_not_crashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp)
            (corpus / "bad.jsonl").write_text("{not json\n", encoding="utf-8")
            result = run_golden(corpus)
        self.assertFalse(result.ok)
        self.assertEqual(result.failures[0].field, "json")

    def test_comment_and_blank_lines_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp)
            (corpus / "c.jsonl").write_text(
                "# 这是注释\n\n{\"id\":\"ok\",\"input\":\"ghp_" + "B" * 36 + "\","
                "\"expect_platform\":\"github\"}\n",
                encoding="utf-8",
            )
            result = run_golden(corpus)
        self.assertTrue(result.ok, result.failures)
        self.assertEqual(result.total, 1)

    def test_failure_messages_do_not_echo_fixture_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp)
            value = "sk-" + "Z" * 48
            (corpus / "x.jsonl").write_text(
                f'{{"id":"leak-check","input":"{value}","expect_platform":"deepseek"}}\n',
                encoding="utf-8",
            )
            result = run_golden(corpus)
        rendered = " ".join(f"{f.case_id} {f.field} {f.expected} {f.actual}" for f in result.failures)
        self.assertNotIn("Z" * 20, rendered)
        self.assertNotIn(value, rendered)


class TestFailClosedGate(unittest.TestCase):
    def test_gate_closed_before_first_golden_run(self):
        store = _FakeStore()
        self.assertFalse(gate_is_current(store))

    def test_gate_opens_after_recording(self):
        store = _FakeStore()
        record_gate(store)
        self.assertTrue(gate_is_current(store))
        self.assertEqual(store.get_setting(GATE_SETTING), rules.TABLE_HASH)

    def test_gate_closes_when_the_table_changes(self):
        """这就是闸门存在的理由：表变了而自检没重跑，scan 必须拒绝出结论。"""
        store = _FakeStore()
        record_gate(store)
        store.set_setting(GATE_SETTING, "0" * 64)  # 模拟规则表被改过
        self.assertFalse(gate_is_current(store))

    def test_gate_message_tells_you_what_to_run(self):
        message = selftest.gate_message()
        self.assertIn("selftest --golden", message)
        self.assertIn("拒绝", message)

    def test_case_failure_carries_no_value(self):
        failure = CaseFailure("id", "field", "expected", "actual")
        self.assertEqual(failure.case_id, "id")
        self.assertNotIn("sk-", str(failure))


if __name__ == "__main__":
    unittest.main()
