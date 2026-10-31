"""侧边栏行命中回归：整行可点、行外不误触、hover 随指针、重绘合帧。

历史 bug：点击/hover 用 tag_bind 挂在文字 item 上——tag 只在文字上有像素，
图标与行的空白区域点不动也悬停不了，用户必须把鼠标精确放到文字上。

两层判据：
  * 命中层（必跑）：Canvas 级 hit-test 契约——行内任意 x（含图标列与
    右侧空白）都导航到该行；行外（品牌区/页脚）不导航；滚动后按视口
    坐标换算（canvasy）仍命中所见行。
  * 帧层（必跑）：_schedule_paint 合帧——同一批事件连发只画一次，
    拖窗/缩放不逐事件全量重绘。
"""

from __future__ import annotations

import sys
import tkinter as tk
import unittest
from unittest import mock


@unittest.skipUnless(sys.platform == "win32", "仅 Windows")
class TestSidebarRowHit(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("无可用显示环境")
        from kv.gui import theme

        theme.setup_style()
        self._listeners = list(theme.LISTENERS)
        self.visited: list[str] = []

    def tearDown(self):
        self.root.destroy()
        # Sidebar 是 app 生命周期组件，不主动摘监听——测试里必须还原，
        # 否则残留的回调会在后续用例的缩放级联里打在已销毁控件上
        from kv.gui import theme

        theme.LISTENERS[:] = self._listeners

    def _sidebar(self, height: int = 600):
        from kv.gui.sidebar import Sidebar

        self.root.geometry(f"300x{height}+0+0")
        bar = Sidebar(self.root, on_navigate=self.visited.append)
        bar.pack(fill="both", expand=True)
        self.root.update()  # Configure → after_idle 合帧 → _row_geo 就位
        return bar

    def _click(self, bar: tk.Canvas, x: int, y: int) -> None:
        bar.event_generate("<Button-1>", x=x, y=y)
        self.root.update()

    def test_click_blank_right_side_of_row_navigates(self):
        bar = self._sidebar()
        y, row_h = bar._row_geo["detect"]
        self._click(bar, 290, y + row_h // 2)  # 行右端空白，远离文字
        self.assertEqual(self.visited, ["detect"])

    def test_click_icon_area_navigates(self):
        bar = self._sidebar()
        y, row_h = bar._row_geo["capture"]
        self._click(bar, 30, y + row_h // 2)  # 图标列，不在文字上
        self.assertEqual(self.visited, ["capture"])

    def test_click_outside_rows_does_not_navigate(self):
        bar = self._sidebar()
        first_y = min(y for y, _ in bar._row_geo.values())
        self._click(bar, 150, first_y // 2)  # 品牌区
        footer_y = max(y + h for y, h in bar._row_geo.values()) + 120
        self._click(bar, 150, footer_y)  # 导航之后的页脚区
        self.assertEqual(self.visited, [])

    def test_hit_row_follows_viewport_after_scroll(self):
        """滚动后点的是「看见的行」：视口坐标经 canvasy 换算回内容坐标。"""
        bar = self._sidebar(height=420)  # 压低视口，让导航区高可滚
        bar.wheel_scroll(1)  # 内容上移一档（unit = 视口高/10）
        self.root.update()
        offset = bar.canvasy(0)
        self.assertGreater(offset, 0)
        y, row_h = bar._row_geo["ingest"]
        visible_y = y + row_h // 2 - offset  # 该行在视口里的位置
        self._click(bar, 150, visible_y)
        self.assertEqual(self.visited, ["ingest"])

    def test_hover_follows_pointer(self):
        bar = self._sidebar()
        y, row_h = bar._row_geo["security"]
        bar.event_generate("<Motion>", x=290, y=y + row_h // 2)
        self.root.update()
        self.assertEqual(bar._hover_key, "security")
        bar.event_generate("<Motion>", x=290, y=60)  # 品牌区：行外
        self.root.update()
        self.assertEqual(bar._hover_key, "")

    def test_paint_coalesces_burst(self):
        """连发三次重绘请求只落一次 _paint——拖窗/缩放合帧的契约。"""
        bar = self._sidebar()
        with mock.patch.object(bar, "_paint", wraps=bar._paint) as spy:
            bar._schedule_paint()
            bar._schedule_paint()
            bar._schedule_paint()
            self.root.update()
        self.assertEqual(spy.call_count, 1)


if __name__ == "__main__":
    unittest.main()
