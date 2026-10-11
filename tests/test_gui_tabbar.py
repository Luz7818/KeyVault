"""TabBar 回归：初始标签的内容必须在整个构建过程结束后仍可见。

历史 bug：add() 立即 place 内容层，place 堆叠序 = 创建序，后注册的标签
盖住初始页 —— 捕获/批量操作/安全三页首显时高亮第一个标签、显示的却是
最后一个标签的内容。

判据用 winfo containing：屏幕同一点上，最顶层窗口必须落在初始标签的
内容层里。测试窗口用 -alpha 0 隐形，不干扰桌面。
"""

from __future__ import annotations

import sys
import tkinter as tk
import unittest


@unittest.skipUnless(sys.platform == "win32", "仅 Windows")
class TestTabBarStacking(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("无可用显示环境")
        try:
            self.root.attributes("-alpha", 0.02)  # 肉眼不可见；alpha=0 不参与命中测试
            self.root.attributes("-topmost", True)  # 钉在最上，(150,100) 必然落在本窗口
        except tk.TclError:
            pass
        self.root.geometry("300x200+0+0")

    def tearDown(self):
        self.root.destroy()

    def _topmost_window(self):
        """等窗口真的映射后再问 (150,100) 处的最顶层窗口。

        全量套件里时序更抖：映射是异步的，update 一次可能不够。
        有界事件泵轮询，杜绝偶发 None。
        """
        self.root.deiconify()
        for _ in range(25):
            self.root.update()
            top = self.root.winfo_containing(150, 100)
            if top is not None:
                return top
            self.root.after(20, lambda: None)
            self.root.update()
        return None

    def test_initial_tab_content_stays_on_top_after_all_adds(self):
        from kv.gui.controls import TabBar

        bar = TabBar(self.root)
        bar.pack(fill="both", expand=True)
        first = bar.add("a", "甲")
        bar.add("b", "乙")
        bar.add("c", "丙")
        bar.add("d", "丁")
        top = self._topmost_window()
        self.assertIsNotNone(top)
        self.assertTrue(
            str(top).startswith(str(first)),
            f"屏幕最顶层窗口 {top} 不在初始标签内容层 {first} 里",
        )

    def test_select_switches_visible_content(self):
        from kv.gui.controls import TabBar

        bar = TabBar(self.root)
        bar.pack(fill="both", expand=True)
        bar.add("a", "甲")
        second = bar.add("b", "乙")
        bar.select("b")
        top = self._topmost_window()
        self.assertIsNotNone(top)
        self.assertTrue(
            str(top).startswith(str(second)),
            f"切到乙后最顶层窗口 {top} 不在乙的内容层 {second} 里",
        )


if __name__ == "__main__":
    unittest.main()
