"""GUI 交互层测试：排序指示、搜索占位符、键盘绑定接线。

全部用 withdrawn 的 Tk root（不占桌面、不抢焦点）；不测像素，测契约。
"""

from __future__ import annotations

import sys
import tkinter as tk
import unittest


@unittest.skipUnless(sys.platform == "win32", "仅 Windows")
class _GuiTestCase(unittest.TestCase):
    """withdrawn root + 命名字体：ttk 样式与 Font 都需要 Tk 实例先就位。"""

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("无可用显示环境")
        self.root.withdraw()
        from kv.gui import theme

        theme.setup_style()


class TestSortIndicator(_GuiTestCase):

    def _table(self):
        from kv.gui.widgets import DataTable

        table = DataTable(
            self.root, ("name", "status"), headings=("名称", "状态"),
            widths=(100, 80),
        )
        table.insert_row(("b", "active"))
        table.insert_row(("a", "revoked"))
        return table

    def test_sort_arrow_toggles_and_moves(self):
        table = self._table()
        table._sort_by("name")
        self.assertTrue(table.tree.heading("name", "text").endswith("▲"))
        self.assertEqual(table.tree.heading("status", "text"), "状态")
        table._sort_by("name")  # 同列再点 → 反向
        self.assertTrue(table.tree.heading("name", "text").endswith("▼"))
        table._sort_by("status")  # 换列 → 原列恢复纯文字
        self.assertEqual(table.tree.heading("name", "text"), "名称")
        self.assertTrue(table.tree.heading("status", "text").endswith("▲"))

    def test_sort_actually_moves_rows(self):
        table = self._table()
        table._sort_by("name")
        names = [table.tree.item(k, "values")[0] for k in table.tree.get_children()]
        self.assertEqual(names, ["a", "b"])
        table._sort_by("name")
        names = [table.tree.item(k, "values")[0] for k in table.tree.get_children()]
        self.assertEqual(names, ["b", "a"])


class TestSearchPlaceholder(_GuiTestCase):
    """占位符是叠在 Entry 上的 Label（Entry 窗口项会压过画布项，画不得）。"""

    def test_placeholder_shows_when_empty_and_unfocused(self):
        from kv.gui.controls import RoundEntry

        var = tk.StringVar()
        entry = RoundEntry(self.root, textvariable=var, placeholder="搜索…")
        self.assertEqual(entry._ph_label.cget("text"), "搜索…")
        self.assertEqual(entry._ph_label.winfo_manager(), "place")

    def test_placeholder_hidden_once_var_has_value(self):
        from kv.gui.controls import RoundEntry

        var = tk.StringVar()
        entry = RoundEntry(self.root, textvariable=var, placeholder="搜索…")
        var.set("deep")
        self.assertEqual(entry._ph_label.winfo_manager(), "")

    def test_empty_var_again_restores_placeholder(self):
        from kv.gui.controls import RoundEntry

        var = tk.StringVar()
        entry = RoundEntry(self.root, textvariable=var, placeholder="搜索…")
        var.set("deep")
        var.set("")
        self.assertEqual(entry._ph_label.winfo_manager(), "place")


class TestKeyboardWiring(_GuiTestCase):

    def test_secrets_table_has_keyboard_bindings(self):
        """键盘层是右键菜单同一批方法的另一条到达路径，绑定必须真的在。"""
        from kv.gui.controls import Card
        from kv.gui.pages.secrets import SecretsPage

        class _App:
            store = None

            def set_status(self, *_a, **_k) -> None:
                pass

            def initialize_vault(self) -> bool:
                return False

            def show_page(self, _key: str) -> None:
                pass

        page = SecretsPage(Card(self.root).body, app=_App())
        tree = page._table.tree
        for sequence in ("<Return>", "<Delete>", "<Control-c>", "<F5>"):
            self.assertTrue(tree.bind(sequence), f"缺少 {sequence} 绑定")


if __name__ == "__main__":
    unittest.main()
