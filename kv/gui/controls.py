"""自绘容器控件：卡片、圆角输入框、标签页、细滚动条。

ttk 的直角描边是"工具感"的主要来源；tkinter 里只有 Canvas 能画圆角。
按钮在 button.py，绘图原语在 paint.py——这里只做容器与输入。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont, ttk
from typing import Callable

from kv.gui import motion, theme
from kv.gui.button import RoundButton  # noqa: F401  re-export：页面仍从 controls 取
from kv.gui.paint import round_points


class Card(tk.Canvas):
    """圆角描边卡片：外圈 1px 描边 + 底部一道浅影线，body 是内容容器。

    卡片默认坐在画布色（CANVAS）上——parent_bg 必须与实际所在处的底色一致，
    否则圆角外露出色差方块。Canvas 的默认请求宽是 ~10cm：fill="x" 的场景必须
    传 height（否则落回默认请求高），横向挤排的卡片还要传 width=1 把请求宽
    让给 pack 分配，否则一行卡片会把窗口撑出横向溢出。
    """

    def __init__(self, parent: tk.Widget, *, radius: int | None = None,
                 height: int | None = None, width: int | None = None,
                 parent_bg: str = theme.CANVAS) -> None:
        super().__init__(
            parent, bg=parent_bg, highlightthickness=0, bd=0,
        )
        if height is not None:
            self.configure(height=height)
        if width is not None:
            self.configure(width=width)
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
        points = round_points(1, 1, w - 2, h - 3, theme.RADIUS)
        # 卡片 = 白纸 + 1px 轮廓 + 底边一道更浅的影线（伪 1px 投影，卡不离地）
        self.create_polygon(
            *points, fill=theme.SURFACE, outline=theme.BORDER_STRONG,
            width=1, smooth=True,
        )
        self.create_line(
            theme.RADIUS, h - 3, w - theme.RADIUS, h - 3,
            fill=theme.BORDER, width=1, capstyle="round",
        )
        self.lower("all")  # 描边垫底，不遮内容


class RoundEntry(tk.Canvas):
    """圆角输入框：Canvas 描边 + 聚焦主色光晕 + 内嵌无边框 Entry。

    placeholder 在「未聚焦且值为空」时画在画布上，不进 Entry 本身——
    所以不会污染 textvariable，搜索框拿它当查询串不会被占位文字干扰。
    """

    def __init__(self, parent: tk.Widget, *, textvariable=None, show: str = "",
                 width: int = 200, font=None, placeholder: str = "",
                 parent_bg: str = theme.SURFACE) -> None:
        super().__init__(
            parent, width=width, height=theme.CONTROL_HEIGHT,
            bg=parent_bg, highlightthickness=0, bd=0,
        )
        self._font = font or theme.FONT
        self._parent_bg = parent_bg
        self._focused = False
        self._radius = theme.RADIUS
        self._placeholder = placeholder
        self._var = textvariable
        self.entry = tk.Entry(
            self, textvariable=textvariable, show=show or "",
            font=self._font, bg=theme.SURFACE, fg=theme.TEXT,
            relief="flat", bd=0, insertbackground=theme.TEXT,
            highlightthickness=0,
        )
        if textvariable is not None:
            textvariable.trace_add("write", lambda *_: self._update_placeholder())
        # 占位符是叠在 Entry 上的 Label——Entry 是窗口项，永远压过画布项，
        # 画在 Canvas 上的占位文字会被不透明 Entry 整个盖住（实测踩过）。
        self._ph_label = tk.Label(
            self.entry, text=placeholder, font=self._font,
            bg=theme.SURFACE, fg=theme.TEXT_TERTIARY,
        )
        self._ph_label.bind("<Button-1>", lambda _e: self.entry.focus_set())
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
        points = round_points(1, 1, w - 2, h - 2, self._radius)
        if self._focused:
            # 聚焦 = 主色描边 + 外圈一圈 ACCENT_SOFT 光晕（画两圈圆角矩形）
            halo = round_points(0, 0, w, h - 1, self._radius + 2)
            self.create_polygon(*halo, fill=theme.ACCENT_SOFT, smooth=True, outline="")
            self.create_polygon(
                *points, fill=theme.SURFACE, smooth=True,
                outline=theme.ACCENT, width=1.4,
            )
        else:
            self.create_polygon(
                *points, fill=theme.SURFACE, smooth=True,
                outline=theme.BORDER_STRONG, width=1,
            )
        if self._placeholder:
            self._update_placeholder()

    def _entry_value(self) -> str:
        """var 的 trace 先于 Entry 内部同步触发——读值必须读 var 不读 entry.get()，
        否则程序化 set 后的第一次重画会拿旧值，占位符闪现。"""
        return self._var.get() if self._var is not None else self.entry.get()

    def _update_placeholder(self) -> None:
        """「未聚焦且值为空」才显示；点击占位符也把焦点交给 Entry。"""
        show = self._placeholder and not self._focused and not self._entry_value()
        if show:
            self._ph_label.place(x=2, y=0, relheight=1.0)
            self._ph_label.lift()
        else:
            self._ph_label.place_forget()


class TabBar(ttk.Frame):
    """自绘标签页：圆角 pill 导航（弹簧滑动 + hover 渐变）+ 堆叠内容区。

    用法模仿 Notebook：bar.add(key, label) 拿到内容 frame，pack 内容进去；
    select(key) 切换。选中 pill 从上一项滑到新项（临界阻尼弹簧），文字色
    同步过渡。pill 是圆角胶囊——放在 Card 白纸里不再需要直角色块。
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
        # place 的堆叠序 = 创建序，后注册的会盖住当前页——每次 add 都把
        # 当前页重新抬到顶，初始页才能在整个构建过程结束后仍然可见
        self._items[self._current]["frame"].tkraise()
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
        """按文字测量重排 hitbox/pill/文字。重排会清掉画布，全部重画。

        层叠从底到顶固定为：hitbox（纸面色，可命中）→ pill → 文字。
        hitbox 曾用 fill=""——透明区域不参与 Canvas 命中，点击会落空。
        """
        self._strip.configure(height=theme.CONTROL_HEIGHT)
        self._strip.delete("all")
        self._pill = None
        font = tkfont.Font(font=theme.FONT)
        x = 0.0
        for key in self._order:
            item = self._items[key]
            w = font.measure(item["label"]) + 2 * theme.BTN_PAD_X
            item["x"], item["w"] = round(x), round(w)
            tag = f"tab-{key}"
            self._strip.create_rectangle(
                x, 0, x + w, theme.CONTROL_HEIGHT,
                fill=theme.SURFACE, outline="", tags=(tag,),
            )
            self._strip.create_text(
                x + w / 2, theme.CONTROL_HEIGHT / 2, text=item["label"],
                fill=theme.TEXT_SECONDARY, font=theme.FONT, tags=(tag, f"label-{key}"),
            )
            self._strip.tag_bind(tag, "<Button-1>", lambda e, k=key: self.select(k))
            self._strip.tag_bind(tag, "<Enter>", lambda e, k=key: self._hover(k, True))
            self._strip.tag_bind(tag, "<Leave>", lambda e, k=key: self._hover(k, False))
            x += w + theme.GAP
        if getattr(self, "_pill_placed", False):
            self._place_pill(self._items[self._current]["x"])
        elif self._items:
            self.on_show_first()

    def _hover(self, key: str, on: bool) -> None:
        """非选中标签的文字色 hover 渐变——与按钮同款反馈语言。"""
        if key == self._current:
            return
        color = theme.TEXT if on else theme.TEXT_SECONDARY
        self._strip.itemconfigure(f"label-{key}", fill=color)

    def _place_pill(self, x: int) -> None:
        """选中底色块（圆角胶囊）：先删旧再画，挂在当前 tab 的 tag 上保证可点击。"""
        self._strip.delete("pill")
        key = self._current
        y0, y1 = 3, theme.CONTROL_HEIGHT - 4
        points = round_points(x, y0, x + self._items[key]["w"], y1, (y1 - y0) // 2)
        self._pill = self._strip.create_polygon(
            *points, fill=theme.ACCENT_SOFT, outline=theme.BORDER,
            width=1, smooth=True, tags=("pill", f"tab-{key}"),
        )
        self._strip.tag_raise("pill")
        for k in self._order:  # 文字永远在 pill 之上
            self._strip.tag_raise(f"label-{k}")
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
        color = "#aeb7c9" if self._hover else "#ccd3e0"
        if self._orient == "vertical":
            y0, y1 = h * self._first, h * (self._first + self._frac)
            if y1 - y0 < 24:
                y0 = min(y0, h - 24)
                y1 = min(max(y0 + 24, y1), h)
            points = round_points(2, round(y0) + 1, w - 1, round(y1) - 1, 3)
        else:
            x0, x1 = w * self._first, w * (self._first + self._frac)
            if x1 - x0 < 24:
                x0 = min(x0, w - 24)
                x1 = min(max(x0 + 24, x1), w)
            points = round_points(round(x0) + 1, 2, round(x1) - 1, h - 1, 3)
        self.create_polygon(*points, fill=color, outline="", smooth=True)
