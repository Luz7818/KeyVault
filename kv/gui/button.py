"""圆角按钮：hover 颜色 8 帧渐变、按下即时变暗（反馈在 pointer-down）、
释放才提交 command。从 controls.py 拆出——按钮的自绘逻辑自成一体，
controls.py 留给容器类控件。

kind: accent（实心主色）/ normal（白底描边）/ danger（红字）。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from typing import Callable

from kv.gui import motion, theme
from kv.gui.paint import round_points


class RoundButton(tk.Canvas):
    """圆角按钮。accent 实心主色带深色描边（立体感），normal/danger 白底描边。"""

    SCHEMES = {
        "accent": {
            "base": theme.ACCENT, "hover": theme.ACCENT_HOVER,
            "press": theme.ACCENT_ACTIVE, "fg": "#ffffff",
            "border": theme.ACCENT_ACTIVE,
        },
        "normal": {
            "base": theme.SURFACE, "hover": theme.HOVER_BG,
            "press": theme.PRESS_BG, "fg": theme.TEXT,
            "border": theme.BORDER_STRONG,
        },
        "danger": {
            "base": theme.SURFACE, "hover": theme.ERROR_SOFT,
            "press": "#fbd0cc", "fg": theme.ERROR,
            "border": theme.BORDER_STRONG,
        },
    }

    def __init__(self, parent: tk.Widget, text: str, command: Callable[[], None],
                 *, kind: str = "normal", font=None,
                 parent_bg: str = theme.SURFACE) -> None:
        self._scheme = self.SCHEMES[kind]
        self._font = font or theme.FONT
        self._text = text
        self._command = command
        self._base = self._scheme["base"]
        self._cur = self._base
        self._pressed = False
        self._job: str | None = None
        self._gen = 0

        f = tkfont.Font(font=self._font)
        width = max(f.measure(text) + 2 * theme.BTN_PAD_X, theme.CONTROL_HEIGHT)
        height = max(theme.CONTROL_HEIGHT, f.metrics("linespace") + 2 * theme.BTN_PAD_Y)
        # bg 是圆角外四角露出的底色，必须与按钮实际坐落的背景一致
        super().__init__(
            parent, width=width, height=height,
            bg=parent_bg, highlightthickness=0, bd=0, cursor="hand2",
        )
        self._draw()
        self.bind("<Enter>", lambda e: self._tint(self._scheme["hover"]))
        self.bind("<Leave>", lambda e: self._leave())
        self.bind("<Button-1>", self._on_down)
        self.bind("<ButtonRelease-1>", self._on_up)
        theme.LISTENERS.append(self._on_font_changed)

    def _measure_size(self) -> None:
        """按钮尺寸 = 文字 + 独立内边距；高度至少 CONTROL_HEIGHT，够放文字。"""
        f = tkfont.Font(font=self._font)
        measure = f.measure(self._text)
        linespace = f.metrics("linespace")
        width = max(measure + 2 * theme.BTN_PAD_X, theme.CONTROL_HEIGHT)
        height = max(theme.CONTROL_HEIGHT, linespace + 2 * theme.BTN_PAD_Y)
        self.configure(width=width, height=height)

    def _on_font_changed(self) -> None:
        """命名字体缩放后重测宽高并重绘，文字不贴边不溢出。"""
        self._measure_size()
        self._draw()

    def destroy(self) -> None:
        if self._on_font_changed in theme.LISTENERS:
            theme.LISTENERS.remove(self._on_font_changed)
        super().destroy()

    def set_text(self, text: str) -> None:
        self._text = text
        self._draw()

    def set_command(self, command: Callable[[], None]) -> None:
        self._command = command

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        points = round_points(1, 1, w - 2, h - 2, theme.RADIUS)
        self.create_polygon(
            *points, fill=self._cur, smooth=True,
            outline=self._scheme["border"], width=1,
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
