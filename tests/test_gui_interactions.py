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

    def tearDown(self):
        # 必须销毁：泄漏的 root 会一直占住 tk._default_root，后续测试里
        # 无 master 的 StringVar/Font 全落到旧解释器上，与当前控件跨 interp
        # 失联（var 永远读不到值、选区失效——FormDialog 测试曾因此全挂）
        try:
            self.root.destroy()
        except tk.TclError:
            pass  # addCleanup 等场景下子件可能已连带销毁


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


class TestWraplengthTracking(_GuiTestCase):
    """换行宽度跟随容器：固定 wraplength 的长文案在拉宽窗口/改缩放后不重排。

    依赖真实布局宽度——withdrawn 窗口永不映射、容器宽恒为 1，必须 deiconify。
    """

    def setUp(self):
        super().setUp()
        self.root.deiconify()

    def test_wraplength_follows_container_width(self):
        from kv.gui.widgets import track_wraplength

        holder = tk.Frame(self.root)
        holder.pack(fill="x")
        label = tk.Label(holder, text="字" * 80, wraplength=400)
        label.pack(anchor="w")
        track_wraplength(label, 720)
        self.root.geometry("500x200")
        self.root.update()
        # ±2px：WM 映射时序里最后一个 Configure 可能带调整前一瞬的宽
        self.assertLessEqual(abs(label.cget("wraplength") - min(720, 500)), 2)
        self.root.geometry("1200x200")
        self.root.update()
        self.assertEqual(label.cget("wraplength"), 720)  # 宽窗口回到 base 上限
        # 窄于守护下限（280 逻辑px）时不动，保持已生效值
        self.root.geometry("200x200")
        self.root.update()
        self.assertEqual(label.cget("wraplength"), 720)

    def test_reserve_keeps_room_for_row_neighbors(self):
        from kv.gui.widgets import track_wraplength

        holder = tk.Frame(self.root)
        holder.pack(fill="x")
        btn = tk.Canvas(holder, width=100, height=30, highlightthickness=0)
        btn.pack(side="left")
        label = tk.Label(holder, text="字" * 80, wraplength=400)
        label.pack(side="left")
        track_wraplength(label, 720, reserve=120)
        self.root.geometry("500x200")
        self.root.update()
        self.assertLessEqual(abs(label.cget("wraplength") - (500 - 120)), 2)


class TestRoundButtonDisabled(_GuiTestCase):
    """禁用态：置灰无反馈、点击不提交——长操作忙碌期防连点的契约。"""

    def setUp(self):
        super().setUp()
        self.root.deiconify()  # 点击命中用 winfo_width，withdrawn 下恒为 1

    def test_disabled_button_ignores_click(self):
        from kv.gui.button import RoundButton

        calls: list[str] = []
        btn = RoundButton(self.root, "扫描", lambda: calls.append("x"), kind="accent")
        btn.pack()
        self.root.update()  # 点击命中判定用 winfo_width，须先 flush 布局
        btn.set_enabled(False)
        self.assertFalse(btn.enabled)
        ev = tk.Event(); ev.x, ev.y = 10, 10  # 任意“按钮内部”坐标
        btn._on_down(ev)
        btn._on_up(ev)
        self.assertEqual(calls, [])
        btn.set_enabled(True)
        btn._on_down(ev)
        btn._on_up(ev)
        self.assertEqual(calls, ["x"])


class TestDataTableEmptyHint(_GuiTestCase):
    """表格空态：无行时居中幽灵提示，有行时消失——place 悬浮不影响布局。"""

    def _table(self):
        from kv.gui.widgets import DataTable

        return DataTable(self.root, ("name", "status"), headings=("名称", "状态"))

    def test_ghost_shown_when_empty_and_hidden_with_rows(self):
        table = self._table()
        self.assertEqual(table._empty_hint.winfo_manager(), "place")
        table.insert_row(("a", "active"))
        self.assertEqual(table._empty_hint.winfo_manager(), "")
        table.clear()
        self.assertEqual(table._empty_hint.winfo_manager(), "place")


class TestFormDialogKeys(_GuiTestCase):
    """对话框键盘即达：Enter 提交、Esc 取消、打开时首字段聚焦并全选。

    wait_visibility 依赖窗口真实可映射——withdrawn root 下永不 Map 会挂住，
    必须 deiconify。
    """

    def setUp(self):
        super().setUp()
        self.root.deiconify()

    def _dialog(self, on_submit=None, initial=None):
        from kv.gui.widgets import FormDialog

        fields = [("名称", "name", "text"), ("值", "value", "password")]
        dlg = FormDialog(self.root, "添加密钥", fields,
                         initial=initial, on_submit=on_submit)

        def _cleanup():
            try:
                dlg.destroy()
            except tk.TclError:
                pass  # root 销毁时对话框已连带销毁

        self.addCleanup(_cleanup)
        return dlg

    def test_enter_esc_wiring_and_submit_logic(self):
        """接线：每个输入框都绑 Return、对话框绑 Escape；提交逻辑收齐 vars。"""
        seen: list[dict | None] = []
        dlg = self._dialog(on_submit=lambda data: seen.append(data) or True)
        for entry in dlg._entries:
            self.assertTrue(entry.bind("<Return>"), "输入框缺 Enter 提交绑定")
        self.assertTrue(dlg.bind("<Escape>"), "对话框缺 Esc 取消绑定")
        dlg._entries[0].insert(0, "ds")
        dlg._entries[1].insert(0, "sk-test")
        dlg._on_ok()  # Return 绑定的处理方法（事件派发时序在 CI 不可靠，单测逻辑）
        self.assertEqual(seen, [{"name": "ds", "value": "sk-test"}])
        self.assertFalse(dlg.winfo_exists())

    def test_escape_cancel_leaves_result_none(self):
        seen: list[dict | None] = []
        dlg = self._dialog(on_submit=lambda data: seen.append(data) or True)
        dlg._on_cancel()  # Escape 绑定的处理方法
        self.assertIsNone(dlg._result)
        self.assertEqual(seen, [])

    def test_initial_value_preselected(self):
        """编辑场景：打开即全选旧值，直接输入就覆盖。"""
        dlg = self._dialog(initial={"name": "old-name"})
        self.root.update()
        self.assertTrue(dlg._entries[0].selection_present())


class TestSelectionPulse(_GuiTestCase):
    """选中涟漪：点击行的 sel-pulse tag 从按下色退回选中色后退场。

    帧时钟注入 1ms + 完成态轮询（tag 退场即过）——固定 sleep 预算对动画
    是结构性 flaky：套件高载时 Tcl timer 派发延迟会吃掉 wall-clock 余量。
    """

    def test_pulse_tag_lifecycle(self):
        import time

        from kv.gui.widgets import DataTable

        table = DataTable(self.root, ("name", "status"), headings=("名称", "状态"))
        table.PULSE_FRAME_MS = 1  # 注入：收敛只由动画完成态决定
        table.pack()
        table.insert_row(("a", "active"))
        self.root.update()
        item = table.tree.get_children()[0]
        table.tree.selection_set(item)  # Treeview 按下即选中并触发 <<TreeviewSelect>>
        self.root.update()
        self.assertIn("sel-pulse", table.tree.item(item, "tags"))
        deadline = time.monotonic() + 2.0  # 超时只是兜底，正常 4 帧 ≈ 毫秒级
        while "sel-pulse" in table.tree.item(item, "tags") and time.monotonic() < deadline:
            time.sleep(0.01)
            self.root.update()
        self.assertNotIn("sel-pulse", table.tree.item(item, "tags"))


class TestPageParallax(_GuiTestCase):
    """页面过渡视差：新页滑入时旧页弹簧目标为向左 sp(8)。"""

    def test_outgoing_page_retargets_back(self):
        from kv.gui import theme
        from kv.gui.app import App

        app = App(scale=1.0, ui_scale=1.0)
        self.addCleanup(app.destroy)
        app.withdraw()
        app.show_page("secrets")  # 首显无动画
        app.show_page("security")
        app.update_idletasks()
        self.assertEqual(app._prev_key, "security")
        # security 是新页（滑入落位 0），secrets 是旧页（视差退场 -sp(8)）
        self.assertEqual(app._page_slides["security"]._target, 0)
        self.assertEqual(app._page_slides["secrets"]._target, -theme.sp(8))
        # 快速切回 secrets：spring 重定向回场，不残留负位置目标
        app.show_page("secrets")
        self.assertEqual(app._page_slides["secrets"]._target, 0)


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
        for sequence in ("<Return>", "<Delete>", "<Control-c>"):
            self.assertTrue(tree.bind(sequence), f"缺少 {sequence} 绑定")
        self.assertFalse(tree.bind("<F5>"), "F5 已上收 App 全局，树级绑定会双触发")

    def test_app_hotkeys_wired(self):
        """App 级快捷键：N 新增（打字中让位）、F5 刷新——承诺过的键位必须真的在。"""
        from kv.gui.app import App

        app = App(scale=1.0, ui_scale=1.0)
        self.addCleanup(app.destroy)
        app.withdraw()
        # 回调已注册到 all bindtag（存在即接线成功）
        self.assertTrue(app.bind_all("<Key-n>"))
        self.assertTrue(app.bind_all("<F5>"))
        # N 在文本框里打字时必须让位（withdrawn 窗口 focus 不落子件，注入焦点控件）
        app.show_page("secrets")
        app.update_idletasks()
        page = app._pages["secrets"]
        self.assertTrue(
            app._typing_in_field(page._search_entry.entry), "搜索框打字中，N 应让位",
        )
        self.assertFalse(
            app._typing_in_field(page._table.tree), "焦点在表格时 N 应可用",
        )


if __name__ == "__main__":
    unittest.main()
