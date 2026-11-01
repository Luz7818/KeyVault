"""TOTP GUI 面：活码窗面板 + 复制后的剪贴板倒计时。"""

from __future__ import annotations

import sys
import time
import tkinter as tk
import unittest
from datetime import datetime, timezone

import tests  # noqa: F401
from tests.test_gui_interactions import _GuiTestCase

from kv.ops import totp as totpops


@unittest.skipUnless(sys.platform == "win32", "仅 Windows")
class TestTotpPanel(_GuiTestCase):
    """活码面板：固定时钟下码与文案确定；复制按钮交出当前码。

    点击命中用 winfo_width，withdrawn 下恒为 1——必须 deiconify + update。
    """

    SEED = b"12345678901234567890"
    NOW = datetime.fromtimestamp(59, tz=timezone.utc)  # RFC 向量点 → 287082

    def setUp(self):
        super().setUp()
        self.root.deiconify()

    def _panel(self, on_copy):
        from kv.gui.totp_window import TotpPanel

        panel = TotpPanel(self.root, self.SEED, on_copy=on_copy,
                          now_fn=lambda: self.NOW)
        panel.pack()
        self.root.update()
        return panel

    def test_shows_code_and_remaining(self):
        from kv.gui.button import RoundButton

        panel = self._panel(lambda _code: None)
        self.assertEqual(panel._code_lbl.cget("text"), "287 082")
        remaining = totpops.generate(self.SEED, self.NOW)[1]
        # 环中心是剩余秒数（文字 item 挂 remaining tag）
        texts = [panel._ring.itemcget(i, "text")
                 for i in panel._ring.find_withtag("remaining")]
        self.assertEqual(texts, [str(remaining)])
        self.assertTrue(any(isinstance(w, RoundButton) for w in panel.winfo_children()))

    def test_copy_button_hands_out_current_code(self):
        from kv.gui.button import RoundButton

        seen: list[str] = []
        panel = self._panel(seen.append)
        ev = tk.Event()
        ev.x, ev.y = 10, 10
        btn = next(w for w in panel.winfo_children() if isinstance(w, RoundButton))
        btn._on_down(ev)
        btn._on_up(ev)
        self.assertEqual(seen, ["287082"])


@unittest.skipUnless(sys.platform == "win32", "仅 Windows")
class TestWipeCountdown(_GuiTestCase):
    """复制后的秒级倒计时：状态栏逐秒递减，到点报告擦除时点；重复制重置。"""

    def test_countdown_ticks_down_and_finishes(self):
        from kv.gui.app import App

        app = App(scale=1.0, ui_scale=1.0)
        self.addCleanup(app.destroy)
        app.withdraw()
        app._start_wipe_countdown("demo", ttl=2.2)
        self.root.update()
        deadline = time.monotonic() + 6.0
        seen_short = False
        while time.monotonic() < deadline:
            app.update()
            text = app._status._label.cget("text")
            if "1s 后自动擦除" in text:
                seen_short = True
            if "倒计时结束" in text:
                break
            time.sleep(0.02)
        self.assertTrue(seen_short, "倒计时没有逐秒递减")
        self.assertIn("倒计时结束", app._status._label.cget("text"))

    def test_recopy_resets_countdown(self):
        from kv.gui.app import App

        app = App(scale=1.0, ui_scale=1.0)
        self.addCleanup(app.destroy)
        app.withdraw()
        app._start_wipe_countdown("first", ttl=1.0)
        time.sleep(0.05)
        app._start_wipe_countdown("second", ttl=5.0)  # 重置：旧表必须被取消
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            app.update()
            text = app._status._label.cget("text")
            if "倒计时结束" in text:
                break
            time.sleep(0.02)
        # 旧 1s 表若没被取消，这里会提前出现「结束」
        self.assertNotIn("倒计时结束", app._status._label.cget("text"))
        self.assertIn("second", app._status._label.cget("text"))


if __name__ == "__main__":
    unittest.main()
