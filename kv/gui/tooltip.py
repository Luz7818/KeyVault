"""悬浮提示：鼠标停驻 600ms 后在控件下方弹出深色小气泡。

attach(widget, text) 一行接入。气泡是 overrideredirect 的 Toplevel——
tkinter 没有原生 tooltip，系统级的ttk.Tooltip 主题在 clam 下观感不可控，
自绘十几行反而完全可控。定位贴控件底边左对齐，超出屏幕右缘时往回收。

连续悬停免延迟：刚关掉一个气泡的 500ms 内再悬停另一个控件，立即弹出——
扫一排按钮逐个看提示时不必每次都等 600ms（首拍等、连拍免，打字机节奏）。
"""

from __future__ import annotations

import time
import tkinter as tk

from kv.gui import theme

_LAST_HIDDEN = 0.0  # 上一个气泡消失的时刻（monotonic 秒）


class Tooltip:
    """悬停气泡。text 可以是字符串或 () -> str（每次悬停时求值）。"""

    DELAY_MS = 600
    CHAIN_WINDOW = 0.5  # 距上个气泡消失小于该秒数 → 免延迟

    def __init__(self, widget: tk.Widget, text) -> None:
        self._widget = widget
        self._text = text
        self._job: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        if time.monotonic() - _LAST_HIDDEN < self.CHAIN_WINDOW:
            self._show()
            return
        self._job = self._widget.after(self.DELAY_MS, self._show)

    def _cancel(self) -> None:
        if self._job is not None:
            self._widget.after_cancel(self._job)
            self._job = None

    def _show(self) -> None:
        self._job = None
        text = self._text() if callable(self._text) else self._text
        if not text:
            return
        self._hide()
        tip = tk.Toplevel(self._widget)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        label = tk.Label(
            tip, text=text, justify="left",
            bg="#2b3242", fg="#f2f4fb",
            font=theme.FONT_SMALL,
            padx=theme.sp(10), pady=theme.sp(6),
        )
        label.pack()
        # 定位：控件底边下方 6px；右缘放不下就整体左移
        x = self._widget.winfo_rootx()
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + theme.sp(6)
        tip.update_idletasks()
        overflow = x + tip.winfo_reqwidth() - self._widget.winfo_screenwidth()
        if overflow > 0:
            x -= overflow + theme.sp(8)
        tip.geometry(f"+{x}+{y}")
        self._tip = tip

    def _hide(self, _event=None) -> None:
        global _LAST_HIDDEN
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None
            _LAST_HIDDEN = time.monotonic()


def attach(widget: tk.Widget, text) -> Tooltip:
    """给控件挂一个悬停提示。"""
    return Tooltip(widget, text)
