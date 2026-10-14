"""TabBar 回归：初始标签的内容必须在整个构建过程结束后仍可见。

历史 bug：add() 立即 place 内容层，place 堆叠序 = 创建序，后注册的标签
盖住初始页 —— 捕获/批量操作/安全三页首显时高亮第一个标签、显示的却是
最后一个标签的内容。

两层判据：
  * 机制层（必跑）：修复的契约是「每次 add() 都把当前页重新 tkraise」——
    旧代码只在首个 add 时经 on_show_first raise 过一次，之后不再抬，
    数 tkraise 调用就能区分。
  * 屏幕层（可跳过）：winfo_containing 命中测试端到端验证「看见的是初始页」。
    它依赖桌面无遮挡——实测会被系统壳层的全屏 Backstop Window 挡死
    （连 topmost 都在其下），环境不可命中时 skipTest 而不是误报。
"""

from __future__ import annotations

import sys
import tkinter as tk
import unittest
from unittest import mock


@unittest.skipUnless(sys.platform == "win32", "仅 Windows")
class TestTabBarStacking(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("无可用显示环境")
        self.root.geometry("300x200+0+0")

    def tearDown(self):
        self.root.destroy()

    def _spy_raises(self):
        """记录所有 tkraise 调用目标（patch 期间全局生效，测试内安全）。"""
        calls: list[str] = []
        original = tk.Frame.tkraise

        def spy(widget, *args, **kwargs):
            calls.append(str(widget))
            return original(widget, *args, **kwargs)

        patcher = mock.patch.object(tk.Frame, "tkraise", spy)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def _build_four_tabs(self):
        from kv.gui.controls import TabBar

        calls = self._spy_raises()
        bar = TabBar(self.root)
        bar.pack()
        first = bar.add("a", "甲")
        bar.add("b", "乙")
        bar.add("c", "丙")
        bar.add("d", "丁")
        return bar, first, calls

    def test_add_reraises_current_tab_every_time(self):
        """修复契约：每次 add() 都把当前页重新抬顶（旧代码只抬 1 次）。

        add() 返回 inner，被 raise 的是它的父层 outer —— 比较对象是 master。
        每次新增标签都会把新的 outer place 到堆叠顶，所以当前页至少要被
        抬「add 次数」次才守得住可见性（on_show_first 的那次是额外收益）。
        """
        bar, first, calls = self._build_four_tabs()
        outer = first.master
        self.assertEqual(bar.current, "a")
        self.assertGreaterEqual(
            calls.count(str(outer)),
            4,
            f"初始标签应被每次 add() 重新抬顶（至少 4 次），实际 {calls}",
        )

    def test_select_raises_target_frame(self):
        bar, _first, calls = self._build_four_tabs()
        second = bar._items["b"]["frame"]
        calls.clear()
        bar.select("b")
        self.assertEqual(bar.current, "b")
        self.assertEqual(calls, [str(second)])

    def test_screen_hit_test_sees_initial_tab(self):
        """端到端：屏幕同一点命中的必须是初始标签的内容层。"""
        bar, first, _calls = self._build_four_tabs()
        self.root.update()
        # 探针：自己的窗口必须可命中，否则桌面有全屏遮挡层，测试失去意义
        if self.root.winfo_containing(150, 100) is None:
            self.skipTest("桌面被全屏遮挡层覆盖，hit-test 不可用")
        top = self.root.winfo_containing(150, 100)
        self.assertIsNotNone(top)
        self.assertTrue(
            str(top).startswith(str(first)),
            f"屏幕最顶层窗口 {top} 不在初始标签内容层 {first} 里",
        )


if __name__ == "__main__":
    unittest.main()
