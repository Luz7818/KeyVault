"""控制台层 —— 无需 Windows。

重点是 R10：仓库路径是中文、控制台 CP 是 936，而掩码字符 '•' 在 gbk 下不可编码。
"""

from __future__ import annotations

import io
import sys
import unittest

import tests  # noqa: F401

from kv import console


class TestSetupStdio(unittest.TestCase):
    def test_idempotent_on_real_streams(self):
        console.setup_stdio()
        console.setup_stdio()
        self.assertEqual(sys.stdout.encoding.lower().replace("-", ""), "utf8")
        self.assertEqual(sys.stderr.encoding.lower().replace("-", ""), "utf8")

    def test_survives_streams_without_reconfigure(self):
        """测试框架常把 stdout 换成没有 reconfigure 的对象，不能因此崩。"""
        original = sys.stdout
        sys.stdout = io.StringIO()
        try:
            console.setup_stdio()
        finally:
            sys.stdout = original

    def test_chinese_survives_reconfigure(self):
        buf = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="replace")
        original = sys.stdout
        sys.stdout = buf
        try:
            console.setup_stdio()
            console.echo("东南大学 · 密钥 · 高德")
        finally:
            sys.stdout = original
        buf.flush()
        self.assertIn("东南大学", buf.buffer.getvalue().decode("utf-8"))


class TestMaskCharProbe(unittest.TestCase):
    def tearDown(self):
        console.init_mask_char("*")

    def test_falls_back_to_asterisk_when_preferred_unencodable(self):
        """把 stdout 伪装成 gbk 编码，'•' 必须回退成 '*'。"""
        buf = io.TextIOWrapper(io.BytesIO(), encoding="gbk", errors="strict")
        original = sys.stdout
        sys.stdout = buf
        try:
            chosen = console.init_mask_char("\u2022")
        finally:
            sys.stdout = original
        self.assertEqual(chosen, "*")
        self.assertEqual(console.mask_char(), "*")

    def test_keeps_bullet_when_encoding_supports_it(self):
        buf = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        original = sys.stdout
        sys.stdout = buf
        try:
            chosen = console.init_mask_char("\u2022")
        finally:
            sys.stdout = original
        self.assertEqual(chosen, "\u2022")

    def test_default_is_asterisk(self):
        self.assertEqual(console.DEFAULT_MASK_CHAR, "*")
        self.assertEqual(console.init_mask_char("*"), "*")

    def test_masking_with_resolved_char_does_not_raise_under_gbk(self):
        """端到端复现 R10：在 gbk 流上打印掩码不得抛 UnicodeEncodeError。"""
        from kv.core.masking import mask

        buf = io.TextIOWrapper(io.BytesIO(), encoding="gbk", errors="strict")
        original = sys.stdout
        sys.stdout = buf
        try:
            chosen = console.init_mask_char("\u2022")
            buf.write(mask(b"ghp_abcdefghijklmnop4f2a", 4, 4, char=chosen))
            buf.flush()
        finally:
            sys.stdout = original
        self.assertIn("ghp_", buf.buffer.getvalue().decode("gbk"))


class TestPrintTable(unittest.TestCase):
    def capture(self, rows, headers=None):
        buf = io.StringIO()
        original = sys.stdout
        sys.stdout = buf
        try:
            console.print_table(rows, headers)
        finally:
            sys.stdout = original
        return buf.getvalue()

    def test_headers_and_separator(self):
        out = self.capture([["a", "b"]], ["名称", "平台"])
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("名称", lines[0])
        self.assertTrue(set(lines[1]) <= {"-", " "}, lines[1])
        self.assertIn("名称", lines[0])

    def test_columns_aligned(self):
        out = self.capture([["short", "x"], ["a-much-longer-cell", "y"]], ["A", "B"])
        body = [line for line in out.splitlines() if line.strip()][1:]
        self.assertEqual(len({line.index("x") for line in body if "x" in line}), 1)

    def test_cjk_cell_aligns_with_ascii_cell(self):
        """中文单元格占 2 列、len() 只算 1 —— 按字符索引对齐会歪，按显示列宽才对。"""
        out = self.capture([["名称甲", "x"], ["abc", "y"]])
        rows = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(rows), 2)
        col_x = console.display_width(rows[0][: rows[0].index("x")])
        col_y = console.display_width(rows[1][: rows[1].index("y")])
        self.assertEqual(col_x, col_y)
        self.assertNotEqual(rows[0].index("x"), rows[1].index("y"), "字符索引确实不同，这正是要修正的原因")

    def test_cjk_headers_do_not_shift_body_columns(self):
        out = self.capture(
            [["deepseek-main", "x"], ["gh", "y"]],
            ["名称", "标记"],
        )
        body = [line for line in out.splitlines() if line.strip()][2:]
        col_x = console.display_width(body[0][: body[0].index("x")])
        col_y = console.display_width(body[1][: body[1].index("y")])
        self.assertEqual(col_x, col_y)

    def test_empty_input_prints_nothing(self):
        self.assertEqual(self.capture([], None), "")

    def test_no_headers(self):
        out = self.capture([["a", "b"]])
        self.assertEqual(len(out.strip().splitlines()), 1)

    def test_ragged_rows_are_padded_not_fatal(self):
        """表格渲染器不该因为调用方少给一列就把整个命令炸掉 —— 尤其是在一个
        安全工具里，炸掉的命令会让人以为库坏了。"""
        out = self.capture([["a", "b", "c"], ["d"]], ["H1", "H2", "H3"])
        lines = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(lines), 4)  # 表头 + 分隔线 + 两行数据
        self.assertIn("d", lines[3])

    def test_row_wider_than_header(self):
        out = self.capture([["a", "b", "c"]], ["H1"])
        self.assertIn("c", out)


class TestDisplayWidth(unittest.TestCase):
    def test_ascii_counts_one_each(self):
        self.assertEqual(console.display_width("abc"), 3)

    def test_cjk_counts_two_each(self):
        self.assertEqual(console.display_width("名称"), 4)

    def test_mixed(self):
        self.assertEqual(console.display_width("名a"), 3)

    def test_ambiguous_width_counts_one(self):
        """U+00B7 与 U+2022 的 east_asian_width 都是 'A'（Ambiguous）：CJK 终端渲染成
        2 列、西方终端 1 列。按惯例一律算 1 —— 算 2 会在西方终端上过度补空格。"""
        for ch in ("\u00b7", "\u2022"):
            with self.subTest(char=hex(ord(ch))):
                self.assertEqual(console.display_width(ch), 1)

    def test_fullwidth_form_counts_two(self):
        self.assertEqual(console.display_width("\uff21"), 2)  # Ａ FULLWIDTH LATIN CAPITAL A

    def test_pad_reaches_target_width(self):
        self.assertEqual(console.display_width(console.pad("名称", 7)), 7)

    def test_pad_never_truncates(self):
        self.assertEqual(console.pad("abcdef", 2), "abcdef")


if __name__ == "__main__":
    unittest.main()
