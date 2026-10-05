"""自绘控件：圆角按钮与圆角卡片。

ttk 的直角描边是"工具感"的主要来源；tkinter 里只有 Canvas 能画圆角。
RoundButton：hover 颜色 8 帧渐变、按下即时变暗（反馈在 pointer-down）、
释放才提交 command。Card：1px 圆角描边的白纸容器，内容放 body。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from typing import Callable

from kv.gui import motion, theme


def _round_points(x0: int, y0: int, x1: int, y1: int, r: int) -> list[tuple[float, float]]:
    """圆角矩形的 polygon 顶点（smooth=True 渲染成圆角）。"""
    r = min(r, (x1 - x0) // 2, (y1 - y0) // 2)
    return [
        (x0 + r, y0), (x1 - r, y0), (x1, y0), (x1, y0 + r),
        (x1, y1 - r), (x1, y1), (x1 - r, y1), (x0 + r, y1),
        (x0, y1), (x0, y1 - r), (x0, y0 + r), (x0, y0),
    ]


class RoundButton(tk.Canvas):
    """圆角按钮。kind: accent（实心主色）/ normal（白底描边）/ danger（红字）。"""

    SCHEMES = {
        "accent": {
            "base": theme.ACCENT, "hover": theme.ACCENT_HOVER,
            "press": theme.ACCENT_ACTIVE, "fg": "#ffffff", "border": None,
        },
        "normal": {
            "base": theme.SURFACE, "hover": theme.HOVER_BG,
            "press": theme.PRESS_BG, "fg": theme.TEXT, "border": theme.BORDER_STRONG,
        },
        "danger": {
            "base": theme.SURFACE, "hover": theme.ERROR_SOFT,
            "press": "#fbd0cc", "fg": theme.ERROR, "border": theme.BORDER_STRONG,
        },
    }

    def __init__(self, parent: tk.Widget, text: str, command: Callable[[], None],
                 *, kind: str = "normal", font=None) -> None:
        self._scheme = self.SCHEMES[kind]
        self._font = font or theme.FONT
        self._text = text
        self._command = command
        self._base = self._scheme["base"]
        self._cur = self._base
        self._pressed = False
        self._job: str | None = None
        self._gen = 0

        measure = tkfont.Font(font=self._font).measure(text)
        width = max(measure + theme.PAD_LG - theme.PAD, theme.CONTROL_HEIGHT)
        height = theme.CONTROL_HEIGHT
        super().__init__(
            parent, width=width, height=height,
            bg=theme.SURFACE, highlightthickness=0, bd=0, cursor="hand2",
        )
        self._draw()
        self.bind("<Enter>", lambda e: self._tint(self._scheme["hover"]))
        self.bind("<Leave>", lambda e: self._leave())
        self.bind("<Button-1>", self._on_down)
        self.bind("<ButtonRelease-1>", self._on_up)

    def set_text(self, text: str) -> None:
        self._text = text
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        points = _round_points(1, 1, w - 2, h - 2, theme.RADIUS)
        border = self._scheme["border"]
        self.create_polygon(
            *points, fill=self._cur, smooth=True,
            outline=border or self._cur, width=1,
        )
        self.create_text(
            w / 2, h / 2, text=self._text, fill=self._scheme["fg"], font=self._font,
        )

    def _tint(self, target: str) -> None:
        if self._pressed:
            return
        self._animate_to(target)

    def _leave(self) -> None:
        self._animate_to(self._base)

    def _animate_to(self, target: str) -> None:
        self._gen += 1
        gen = self._gen
        source = self._cur
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None

        def tick(step: int = 0) -> None:
            if gen != self._gen:  # 期间有新过渡接管
                return
            self._cur = motion.lerp_color(source, target, min(step / 7, 1.0))
            self._draw()
            if step < 7 and self._cur != target:
                self._job = self.after(16, tick, step + 1)

        tick()

    def _on_down(self, _event: tk.Event) -> None:
        self._pressed = True
        self._set_now(self._scheme["press"])  # 反馈在按下那一刻，不等释放

    def _on_up(self, _event: tk.Event) -> None:
        if not self._pressed:
            return
        self._pressed = False
        inside = 0 <= _event.x <= self.winfo_width() and 0 <= _event.y <= self.winfo_height()
        self._set_now(self._scheme["hover"] if inside else self._base)
        if inside and self._command:
            self._command()

    def _set_now(self, color: str) -> None:
        self._gen += 1
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        self._cur = color
        self._draw()


class Card(tk.Canvas):
    """圆角描边卡片：外圈 1px 描边，body 是内容容器（fill 布局跟随卡片）。

    Canvas 不做内容自适应——fill="both" 的场景由父布局分配高度；
    fill="x" 的场景必须传 height，否则 Canvas 落回默认的 10cm 请求高。
    """

    def __init__(self, parent: tk.Widget, *, radius: int | None = None,
                 height: int | None = None, parent_bg: str = theme.SURFACE) -> None:
        super().__init__(
            parent, bg=parent_bg, highlightthickness=0, bd=0,
        )
        if height is not None:
            self.configure(height=height)
        self._radius = radius or theme.RADIUS
        self.body = tk.Frame(self, bg=theme.SURFACE)
        self.body.place(x=1, y=1, relwidth=1.0, width=-2, relheight=1.0, height=-2)
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Map>", lambda _e: self._draw())

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        points = _round_points(1, 1, w - 2, h - 2, self._radius)
        self.create_polygon(
            *points, fill=theme.SURFACE, outline=theme.BORDER_STRONG,
            width=1, smooth=True,
        )
        self.lower("all")  # 描边垫底，不遮内容
