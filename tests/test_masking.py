"""规范化、指纹、掩码 —— 纯函数，无需 Windows。"""

from __future__ import annotations

import unittest

import tests  # noqa: F401

from kv.core import masking


class TestCanonicalize(unittest.TestCase):
    def test_strips_trailing_newline_from_token(self):
        """剪切板复制常带尾部换行；不规范化就会每次复制都产生一条重复行。"""
        self.assertEqual(masking.canonicalize(b"sk-abc123\r\n"), b"sk-abc123")
        self.assertEqual(masking.canonicalize(b"sk-abc123\n"), b"sk-abc123")
        self.assertEqual(masking.canonicalize(b"  sk-abc123 \t\n"), b"sk-abc123")

    def test_preserves_interior_whitespace(self):
        self.assertEqual(masking.canonicalize(b"a b c"), b"a b c")

    def test_is_idempotent(self):
        once = masking.canonicalize(b"sk-abc123\n")
        self.assertEqual(masking.canonicalize(once), once)

    def test_json_key_order_does_not_matter(self):
        """GCP 服务账号被别的工具重新序列化不该产生重复行。"""
        a = masking.canonicalize(b'{"b":1,"a":2}', masking.KIND_JSON)
        b = masking.canonicalize(b'{"a":2,"b":1}', masking.KIND_JSON)
        self.assertEqual(a, b)
        self.assertEqual(a, b'{"a":2,"b":1}')

    def test_json_whitespace_does_not_matter(self):
        a = masking.canonicalize(b'{\n  "a" : 1\n}', masking.KIND_JSON)
        b = masking.canonicalize(b'{"a":1}', masking.KIND_JSON)
        self.assertEqual(a, b)

    def test_json_non_ascii_preserved(self):
        out = masking.canonicalize('{"名字":"值"}'.encode(), masking.KIND_JSON)
        self.assertIn("名字".encode("utf-8"), out)

    def test_json_invalid_falls_back_to_strip(self):
        self.assertEqual(masking.canonicalize(b"{not json", masking.KIND_JSON), b"{not json")

    def test_pem_normalizes_line_endings(self):
        pem = b"-----BEGIN PRIVATE KEY-----\r\nAAA\r\n-----END PRIVATE KEY-----\r\n"
        out = masking.canonicalize(pem, masking.KIND_PEM)
        self.assertNotIn(b"\r", out)
        self.assertTrue(out.endswith(b"\n"))
        self.assertEqual(masking.canonicalize(out, masking.KIND_PEM), out)


class TestFingerprint(unittest.TestCase):
    def test_same_value_different_trailing_newline_same_fingerprint(self):
        self.assertEqual(
            masking.fingerprint(b"sk-abc123"),
            masking.fingerprint(b"sk-abc123\n"),
        )

    def test_different_values_differ(self):
        self.assertNotEqual(
            masking.fingerprint(b"sk-abc123"),
            masking.fingerprint(b"sk-abc124"),
        )

    def test_is_64_hex(self):
        fp = masking.fingerprint(b"anything")
        self.assertEqual(len(fp), 64)
        int(fp, 16)

    def test_idempotent_on_canonical_input(self):
        canonical = masking.canonicalize(b"sk-abc123\n")
        self.assertEqual(masking.fingerprint(canonical), masking.fingerprint(b"sk-abc123"))

    def test_json_kind_fingerprint_ignores_key_order(self):
        self.assertEqual(
            masking.fingerprint(b'{"a":1,"b":2}', masking.KIND_JSON),
            masking.fingerprint(b'{"b":2,"a":1}', masking.KIND_JSON),
        )


class TestMask(unittest.TestCase):
    def test_default_keeps_tail_four(self):
        self.assertEqual(masking.mask(b"sk-abcdefgh1234"), "****1234")

    def test_head_and_tail(self):
        self.assertEqual(masking.mask(b"AKIAIOSFODNN7EXAMPLE", 4, 4), "AKIA****MPLE")

    def test_fully_hidden(self):
        self.assertEqual(masking.mask(b"a-connection-string", 0, 0), "****")

    def test_mask_run_is_fixed_width(self):
        """掩码段不随值长度增长 —— 否则长 key 会把表格撑爆。"""
        short = masking.mask(b"sk-short-value-here")
        long = masking.mask(b"sk-" + b"A" * 200)
        self.assertEqual(len(short), len(long))

    def test_short_value_fully_masked(self):
        """值短到留头留尾就等于泄漏全部时，整个掩掉。"""
        self.assertEqual(masking.mask(b"abc", 0, 4), "****")
        self.assertEqual(masking.mask(b"abcdefgh", 4, 4), "****")

    def test_custom_char(self):
        self.assertEqual(masking.mask(b"sk-abcdefgh1234", char="\u2022"), "\u2022\u2022\u2022\u20221234")

    def test_never_reveals_the_middle(self):
        value = b"sk-TOPSECRET-MIDDLE-VALUE-1234"
        masked = masking.mask(value, 3, 4)
        self.assertNotIn("TOPSECRET", masked)
        self.assertTrue(masked.startswith("sk-"))
        self.assertTrue(masked.endswith("1234"))

    def test_newlines_escaped(self):
        masked = masking.mask(b"line1\nline2-tail", 0, 4)
        self.assertNotIn("\n", masked)


class TestInferMaskStyle(unittest.TestCase):
    def test_sk_prefix_kept(self):
        self.assertEqual(masking.infer_mask_style(b"sk-abcdefgh"), (3, 4))

    def test_ghp_prefix_kept(self):
        self.assertEqual(masking.infer_mask_style(b"ghp_abcdefgh"), (4, 4))

    def test_no_prefix(self):
        self.assertEqual(masking.infer_mask_style(b"0123456789abcdef0123456789abcdef"), (0, 4))

    def test_long_prefix_capped_at_six(self):
        self.assertEqual(masking.infer_mask_style(b"abcdefghijkl-mnop"), (0, 4))

    def test_ignores_trailing_newline(self):
        self.assertEqual(masking.infer_mask_style(b"sk-abcdefgh\n"), (3, 4))

    def test_end_to_end_gives_the_documented_preview(self):
        """计划里 M0 的验收判据就是 sk-****c31f 这个形状。"""
        value = b"sk-" + b"A" * 44 + b"c31f"
        head, tail = masking.infer_mask_style(value)
        self.assertEqual(masking.mask(value, head, tail), "sk-****c31f")


class TestSplitMaskAndPreview(unittest.TestCase):
    """存头尾两段、渲染时再合成 —— 掩码字符于是是纯显示偏好。"""

    def test_split_returns_head_and_tail(self):
        self.assertEqual(masking.split_mask(b"sk-abcdefgh1234", 3, 4), ("sk-", "1234"))

    def test_split_with_no_head(self):
        self.assertEqual(masking.split_mask(b"sk-abcdefgh1234", 0, 4), ("", "1234"))

    def test_split_fully_hidden(self):
        self.assertEqual(masking.split_mask(b"a-connection-string", 0, 0), ("", ""))

    def test_split_short_value_hides_everything(self):
        self.assertEqual(masking.split_mask(b"abc", 0, 4), ("", ""))
        self.assertEqual(masking.split_mask(b"abcdefgh", 4, 4), ("", ""))

    def test_preview_composes(self):
        self.assertEqual(masking.preview("sk-", "1234"), "sk-****1234")
        self.assertEqual(masking.preview("sk-", "1234", "\u2022"), "sk-\u2022\u2022\u2022\u20221234")
        self.assertEqual(masking.preview("", ""), "****")

    def test_preview_run_length_override(self):
        self.assertEqual(masking.preview("a", "z", run=8), "a********z")

    def test_split_then_preview_equals_mask(self):
        for value, style in ((b"sk-abcdefgh1234", (3, 4)), (b"AKIAEXAMPLE1234", (4, 4)),
                             (b"connstring", (0, 0)), (b"ab", (0, 4))):
            head, tail = masking.split_mask(value, *style)
            self.assertEqual(masking.preview(head, tail, "*"), masking.mask(value, *style))

    def test_neutral_characters_survive_the_round_trip(self):
        """头尾两段本身可能含 '*'，所以合成必须靠位置而不是靠替换。"""
        head, tail = masking.split_mask(b"sk-*bcdefgh12*4", 3, 4)
        self.assertEqual(head, "sk-")
        self.assertEqual(tail, "12*4")
        self.assertEqual(masking.preview(head, tail, "*"), "sk-****12*4")


class TestIsEncodable(unittest.TestCase):
    def test_bullet_not_encodable_in_gbk(self):
        """R10：用户给的示例掩码 ghp_••••4f2a 在 cp936 下会崩。这条断言就是那个事实。"""
        self.assertFalse(masking.is_encodable("\u2022", "gbk"))

    def test_asterisk_encodable_everywhere(self):
        for enc in ("gbk", "utf-8", "cp936", "ascii"):
            self.assertTrue(masking.is_encodable("*", enc), enc)

    def test_bullet_encodable_in_utf8(self):
        self.assertTrue(masking.is_encodable("\u2022", "utf-8"))

    def test_chinese_encodable_in_gbk_but_not_ascii(self):
        self.assertTrue(masking.is_encodable("密钥", "gbk"))
        self.assertFalse(masking.is_encodable("密钥", "ascii"))

    def test_unknown_encoding_returns_false(self):
        self.assertFalse(masking.is_encodable("x", "not-a-real-codec"))


if __name__ == "__main__":
    unittest.main()
