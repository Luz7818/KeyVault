"""自绘控件：圆角按钮与圆角卡片。

ttk 的直角描边是"工具感"的主要来源；tkinter 里只有 Canvas 能画圆角。
RoundButton：hover 颜色 8 帧渐变、按下即时变暗（反馈在 pointer-down）、
释放才提交 command。Card：1px 圆角描边的白纸容器，内容放 body。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont, ttk
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

        f = tkfont.Font(font=self._font)
        width = max(f.measure(text) + 2 * theme.BTN_PAD_X, theme.CONTROL_HEIGHT)
        height = max(theme.CONTROL_HEIGHT, f.metrics("linespace") + 2 * theme.BTN_PAD_Y)
        super().__init__(
            parent, width=width, height=height,
            bg=theme.SURFACE, highlightthickness=0, bd=0, cursor="hand2",
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
        points = _round_points(1, 1, w - 2, h - 2, theme.RADIUS)
        self.create_polygon(
            *points, fill=theme.SURFACE, outline=theme.BORDER_STRONG,
            width=1, smooth=True,
        )
        self.lower("all")  # 描边垫底，不遮内容


class RoundEntry(tk.Canvas):
    """圆角输入框：Canvas 描边（聚焦变主色）+ 内嵌无边框 Entry。"""

    def __init__(self, parent: tk.Widget, *, textvariable=None, show: str = "",
                 width: int = 200, font=None) -> None:
        super().__init__(
            parent, width=width, height=theme.CONTROL_HEIGHT,
            bg=theme.SURFACE, highlightthickness=0, bd=0,
        )
        self._font = font or theme.FONT
        self._focused = False
        self._radius = theme.RADIUS
        self.entry = tk.Entry(
            self, textvariable=textvariable, show=show or "",
            font=self._font, bg=theme.SURFACE, fg=theme.TEXT,
            relief="flat", bd=0, insertbackground=theme.TEXT,
            highlightthickness=0,
        )
        self._place_entry()
        self.entry.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.entry.bind("<FocusOut>", lambda e: self._set_focus(False))
        self.bind("<Configure>", lambda _e: self._draw())
        self._draw()
        theme.LISTENERS.append(self._on_font_changed)

    def _place_entry(self) -> None:
        padx = theme.BTN_PAD_X - 2
        # 上下各留 2px 露出圆角描边——Entry 全高会把描边上下边盖掉
        self.entry.place(
            x=padx, y=2, relwidth=1.0, width=-2 * padx, relheight=1.0, height=-4,
        )

    def _on_font_changed(self) -> None:
        self.configure(height=theme.CONTROL_HEIGHT)
        self._place_entry()
        self._draw()

    def destroy(self) -> None:
        if self._on_font_changed in theme.LISTENERS:
            theme.LISTENERS.remove(self._on_font_changed)
        super().destroy()

    def _set_focus(self, on: bool) -> None:
        self._focused = on
        self._draw()
        if on:
            self.entry.focus_set()

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        points = _round_points(1, 1, w - 2, h - 2, self._radius)
        self.create_polygon(
            *points, fill=theme.SURFACE, smooth=True,
            outline=theme.ACCENT if self._focused else theme.BORDER_STRONG,
            width=1,
        )


class TabBar(ttk.Frame):
    """自绘标签页：pill 导航条（选中底色弹簧滑动）+ 堆叠内容区。

    用法模仿 Notebook：bar.add(key, label) 拿到内容 frame，pack 内容进去；
    select(key) 切换。选中 pill 的底色块从上一项滑到新项（临界阻尼弹簧），
    文字色同步过渡——这是替代 ttk.Notebook 方角 tab 的关键组件。
    """

    def __init__(self, parent: tk.Widget, *, on_change: Callable[[str], None] | None = None) -> None:
        super().__init__(parent, style="TFrame")
        self._items: dict[str, dict] = {}
        self._order: list[str] = []
        self._current = ""
        self._on_change = on_change
        self._pill: str | None = None
        self._pill_x = motion.Spring(self, lambda x: self._place_pill(round(x)))

        self._strip = tk.Canvas(
            self, height=theme.CONTROL_HEIGHT, bg=theme.SURFACE, highlightthickness=0, bd=0,
        )
        self._strip.pack(fill="x")
        self._body = tk.Frame(self, bg=theme.SURFACE)
        self._body.pack(fill="both", expand=True)
        self._strip.bind("<Configure>", lambda _e: self._relayout())
        theme.LISTENERS.append(self._relayout)

    def destroy(self) -> None:
        if self._relayout in theme.LISTENERS:
            theme.LISTENERS.remove(self._relayout)
        super().destroy()
    def add(self, key: str, label: str) -> tk.Frame:
        """注册一个标签，返回它的内容层（已带内边距，直接 pack 内容）。"""
        outer = tk.Frame(self._body, bg=theme.SURFACE)
        # 内容页全部 place 堆叠，切换靠 tkraise——与主窗口页面同一手法
        outer.place(in_=self._body, x=0, y=0, relwidth=1.0, relheight=1.0)
        inner = tk.Frame(outer, bg=theme.SURFACE)
        inner.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.PAD)
        self._items[key] = {"label": label, "frame": outer, "x": 0, "w": 0}
        self._order.append(key)
        if not self._current:
            self._current = key
        self._relayout()
        return inner

    def select(self, key: str) -> None:
        if key not in self._items:
            return
        if key == self._current:
            self._items[key]["frame"].tkraise()
            return
        self._current = key
        self._items[key]["frame"].tkraise()
        self._repaint()
        self._pill_x.to(self._items[key]["x"])
        if self._on_change:
            self._on_change(key)

    @property
    def current(self) -> str:
        return self._current

    def on_show_first(self) -> None:
        """首帧布局完成后调用：pill 直接落在初始项上，不播动画。"""
        key = self._current
        self._pill_placed = True
        self._items[key]["frame"].tkraise()
        self._place_pill(self._items[key]["x"])
        self._repaint()

    def _relayout(self) -> None:
        """按文字测量重排 pill 与 hitbox。重排会清掉画布，全部重画。"""
        self._strip.configure(height=theme.CONTROL_HEIGHT)
        self._strip.delete("all")
        self._pill = None
        font = tkfont.Font(font=theme.FONT)
        x = 0.0
        for key in self._order:
            item = self._items[key]
            w = font.measure(item["label"]) + 2 * theme.BTN_PAD_X
            item["x"], item["w"] = round(x), round(w)
            hit = self._strip.create_rectangle(
                x, 0, x + w, theme.CONTROL_HEIGHT, fill="", outline="", tags=(f"hit-{key}",),
            )
            self._strip.create_text(
                x + w / 2, theme.CONTROL_HEIGHT / 2, text=item["label"],
                fill=theme.TEXT_SECONDARY, font=theme.FONT, tags=(f"label-{key}",),
            )
            self._strip.tag_bind(hit, "<Button-1>", lambda e, k=key: self.select(k))
            x += w + theme.GAP
        if getattr(self, "_pill_placed", False):
            self._place_pill(self._items[self._current]["x"])
        elif self._items:
            self.on_show_first()

    def _place_pill(self, x: int) -> None:
        self._pill = self._strip.create_rectangle(
            x, 2, x + self._items[self._current]["w"], theme.CONTROL_HEIGHT - 4,
            fill=theme.ACCENT_SOFT, outline="", tags=("pill",),
        )
        self._strip.lower("pill")
        self._repaint()

    def _repaint(self) -> None:
        for key in self._order:
            active = key == self._current
            color = theme.ACCENT if active else theme.TEXT_SECONDARY
            self._strip.itemconfigure(f"label-{key}", fill=color)


class SlimScrollbar(tk.Canvas):
    """细圆角滚动条：6px 滑块，hover 加深，替换系统滚动条。"""

    def __init__(self, parent: tk.Widget, target, *, orient: str = "vertical") -> None:
        self._orient = orient
        self._target = target
        thickness = max(round(8 * theme.SCALE), 6)
        if orient == "vertical":
            super().__init__(parent, width=thickness, bg=theme.SURFACE, highlightthickness=0, bd=0)
        else:
            super().__init__(parent, height=thickness, bg=theme.SURFACE, highlightthickness=0, bd=0)
        self._first = 0.0
        self._frac = 1.0
        self._hover = False
        self._drag_from: float | None = None
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", self._on_jump)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag_from", None))
        self.bind("<MouseWheel>", self._on_wheel)
        target.configure(yscrollcommand=self._on_view)

    def _set_hover(self, on: bool) -> None:
        self._hover = on
        self._draw()

    def _on_view(self, first: str, last: str) -> None:
        self._first = float(first)
        self._frac = max(float(last) - float(first), 0.02)
        self._draw()

    def _on_wheel(self, event: tk.Event) -> None:
        self._target.yview_scroll(-1 * (event.delta // 120), "units")

    def _on_jump(self, event: tk.Event) -> None:
        if self._orient == "vertical":
            frac = event.y / max(self.winfo_height(), 1)
        else:
            frac = event.x / max(self.winfo_width(), 1)
        self._drag_from = frac
        self._target.yview_moveto(max(frac - self._frac / 2, 0.0))

    def _on_drag(self, event: tk.Event) -> None:
        if self._drag_from is None:
            return
        if self._orient == "vertical":
            frac = event.y / max(self.winfo_height(), 1)
        else:
            frac = event.x / max(self.winfo_width(), 1)
        self._target.yview_moveto(max(frac - self._frac / 2, 0.0))

    def _draw(self) -> None:
        self.delete("all")
        w = self.winfo_width() or 1
        h = self.winfo_height() or 1
        color = "#b8c0cf" if self._hover else "#d3d9e3"
        if self._orient == "vertical":
            y0, y1 = h * self._first, h * (self._first + self._frac)
            if y1 - y0 < 24:
                y0 = min(y0, h - 24)
                y1 = min(max(y0 + 24, y1), h)
            points = _round_points(2, round(y0) + 1, w - 2, round(y1) - 1, 3)
        else:
            x0, x1 = w * self._first, w * (self._first + self._frac)
            if x1 - x0 < 24:
                x0 = min(x0, w - 24)
                x1 = min(max(x0 + 24, x1), w)
            points = _round_points(round(x0) + 1, 2, round(x1) - 1, h - 2, 3)
        self.create_polygon(*points, fill=color, outline="", smooth=True)
