"""可复用 GUI 组件。表格、表单对话框、确认框、状态栏、页头、可滚动页。"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, Sequence

from kv.gui import motion, theme
from kv.gui.controls import RoundButton, RoundEntry, SlimScrollbar


def track_wraplength(label: tk.Widget, base: int, *,
                     reserve: int | Callable[[], int] = 0,
                     anchor: tk.Widget | None = None) -> None:
    """换行宽度跟随容器宽度（上限 base 逻辑px）——固定 wraplength 的长文案
    在拉宽窗口或改缩放后不会重排（用户实测「文字没有随页面变化」即此因）。

    reserve 是同一行里其他元素占掉的宽度：数值按逻辑 px 走 sp()，也可以传
    可调用对象实时测量（如并排按钮的请求宽）。anchor 缺省用 label.master，
    容器宽度不跟着内容变的场景（ScrollPage 内容层、fill="x" 的框）才有效。
    """
    target = anchor if anchor is not None else label.master

    def sync(_event=None) -> None:
        avail = target.winfo_width()
        if avail < theme.sp(280):
            return  # 未完成布局（首帧宽=1）不动，保持初始值
        r = reserve() if callable(reserve) else theme.sp(reserve)
        want = min(theme.sp(base), max(avail - r, theme.sp(120)))
        if want > 0 and label.cget("wraplength") != want:
            label.configure(wraplength=want)

    target.bind("<Configure>", sync, add="+")
    sync()


class ScrollPage(ttk.Frame):
    """可滚动页：内容自然高度超过视口时自动出细滚动条。
    固定表单的总请求高度会超过视口——pack 对放不下的内容直接裁掉，
    「设置页保存按钮被切一半」就是这么来的。子控件 pack 进 self.content。

    bg 决定底色体系：整页用默认 CANVAS（灰画布 + Page.* 样式）；放进
    白卡片/标签页时传 SURFACE——底色不配套会在圆角外露色差方块。

    滚轮滚动由 App 全局委派给指针所在页的 wheel_scroll()——页面全部
    place 堆叠，各页自己 bind_all 会串页。
    """

    def __init__(self, parent: tk.Widget, *, padding=None,
                 bg: str = theme.CANVAS) -> None:
        style = "Page.TFrame" if bg == theme.CANVAS else "TFrame"
        super().__init__(parent, style=style,
                         padding=padding if padding is not None else theme.PAD)
        self._canvas = tk.Canvas(
            self, bg=bg, highlightthickness=0, bd=0,
            yscrollincrement=max(round(32 * theme.SCALE), 8),
        )
        self._canvas.pack(side="left", fill="both", expand=True)
        # 滚动条悬浮在画布右缘（place 不参与几何分配）：出现/消失不挤压
        # 内容，内容宽度不再左右跳 8px（pack 时代每回溢出切换就闪一下）
        self._sb = SlimScrollbar(self, self._canvas, bg=bg)
        self._sb_place = dict(in_=self._canvas, relx=1.0, rely=0.0,
                              relheight=1.0, anchor="ne")
        self.content = tk.Frame(self._canvas, bg=bg)
        self._win = self._canvas.create_window(0, 0, window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._sync)
        self._canvas.bind("<Configure>", self._sync)
        self._sync()

    def _sync(self, _event=None) -> None:
        """内容变高/视口变宽时重算滚动区；不超高时收起滚动条并回顶。"""
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        self._canvas.itemconfigure(self._win, width=self._canvas.winfo_width())
        overflow = self.content.winfo_reqheight() > self._canvas.winfo_height() + 4
        if overflow:
            self._sb.place(**self._sb_place)
        else:
            self._sb.place_forget()
            self._canvas.yview_moveto(0)

    def wheel_scroll(self, units: int) -> None:
        self._canvas.yview_scroll(units, "units")


class DataTable(ttk.Frame):
    """带列排序的 Treeview 表格：纯白行 + hover 高亮 + 柔和选中。

    主动放弃斑马纹——斑马与 hover/选中叠加时 tag 优先级互相踩，且现代
    表格语言（Linear/Notion 式）是纯白行靠 hover 提供反馈。
    """

    def __init__(
        self,
        parent: tk.Widget,
        columns: Sequence[str],
        *,
        headings: Sequence[str] | None = None,
        widths: Sequence[int] | None = None,
        rows: int | None = None,
        on_double: Callable[[dict[str, Any]], None] | None = None,
        on_right_click: Callable[[tk.Event, dict[str, Any]], None] | None = None,
    ):
        super().__init__(parent)
        self._columns = list(columns)
        self._on_double = on_double
        self._on_right_click = on_right_click
        self._sort_col: str | None = None
        self._sort_rev = False
        self._hover_item: str | None = None
        self._build(headings or columns, widths)
        # 可滚容器里的表格必须定高（expand 在滚动画布内无效）：
        # rows = 常显行数，更多行由表格自带的细滚动条接管
        if rows:
            self.configure(height=theme.ROW_HEIGHT * (rows + 1) + theme.sp(12))

    def _build(self, headings: Sequence[str], widths: Sequence[int] | None) -> None:
        self.tree = ttk.Treeview(
            self, columns=self._columns, show="headings", selectmode="browse",
        )
        # 细自绘滚动条悬浮在表格右缘（place 不占 grid 位）：溢出与否都不挤
        # 压列宽——pack/grid 时代滚动条一出现整表横向跳一次
        vsb = SlimScrollbar(self, self.tree)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.place(in_=self.tree, relx=1.0, rely=0.0, relheight=1.0, anchor="ne")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        # 空态幽灵提示：表体无行时居中一行灰字（audit 过滤为空等场景），
        # place 悬浮、不参与布局
        self._empty_hint = tk.Label(
            self, text="暂无记录", font=theme.FONT_SUBTITLE,
            bg=theme.SURFACE, fg=theme.TEXT_TERTIARY,
        )
        self._empty_hint.place(in_=self.tree, relx=0.5, rely=0.5, anchor="center")
        self._headings = list(headings)
        # 列宽是**比例**而非定值：声明值只定各列占比，实际宽度在 Configure 时
        # 按视口分摊——定值列宽在高缩放档位必然超出视口且无横向滚动条可救。
        total = sum(widths) if widths else sum([120] * len(self._columns))
        self._ratios = [
            (widths[i] if widths and i < len(widths) else 120) / total
            for i in range(len(self._columns))
        ]
        for i, col in enumerate(self._columns):
            self.tree.heading(col, text=headings[i], command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=theme.sp(120), minwidth=theme.sp(48))
        self.tree.bind("<Configure>", lambda _e: self._fit_columns())
        self.tree.tag_configure("hover", background=theme.HOVER_BG)
        self.tree.bind("<Motion>", self._on_motion)
        self.tree.bind("<Leave>", lambda _e: self._clear_hover())
        # 点选后该行交回选中色——hover 底色不能压住选中底色
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._clear_hover())
        # 选中涟漪：点击反馈在按下那一刻（Treeview 按下即选中），比选中底色
        # 深一档的色 4 帧退回——点击「被听见」的触觉反馈（tag 级动画只动
        # 被选行，不碰全局样式）
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._pulse_selection(), add="+")
        self._pulse_job: str | None = None
        if self._on_double:
            self.tree.bind("<Double-1>", self._handle_double)
        if self._on_right_click:
            self.tree.bind("<Button-3>", self._handle_right_click)

    def clear(self) -> None:
        self._hover_item = None
        self.tree.delete(*self.tree.get_children())
        self._sync_empty_hint()

    def _sync_empty_hint(self) -> None:
        """表体有行时藏幽灵提示（place_forget 不影响布局）。"""
        if self.tree.get_children():
            self._empty_hint.place_forget()
        else:
            self._empty_hint.place(
                in_=self.tree, relx=0.5, rely=0.5, anchor="center",
            )

    def _fit_columns(self) -> None:
        """按声明比例把视口宽分摊给各列——任何缩放档位下列都完整可见。"""
        avail = self.tree.winfo_width() - theme.sp(10)  # 减自绘滚动条
        if avail < theme.sp(120):
            return
        for i, col in enumerate(self._columns):
            self.tree.column(col, width=max(round(avail * self._ratios[i]),
                                            theme.sp(48)))

    def insert_row(self, values: Sequence[Any], *, tags: Sequence[str] = ()) -> str:
        item = self.tree.insert("", "end", values=values, tags=tuple(tags))
        self._sync_empty_hint()
        return item

    def selected_row(self) -> dict[str, Any] | None:
        sel = self.tree.selection()
        if not sel:
            return None
        vals = self.tree.item(sel[0], "values")
        return dict(zip(self._columns, vals))

    def _on_motion(self, event: tk.Event) -> None:
        item = self.tree.identify_row(event.y)
        if item == self._hover_item:
            return
        self._clear_hover()
        if item and item not in self.tree.selection():
            self._hover_item = item
            self.tree.item(item, tags=(*self.tree.item(item, "tags"), "hover"))

    def _clear_hover(self) -> None:
        if not self._hover_item:
            return
        try:
            tags = tuple(t for t in self.tree.item(self._hover_item, "tags") if t != "hover")
            self.tree.item(self._hover_item, tags=tags)
        except tk.TclError:
            pass  # 行已被清空
        self._hover_item = None

    # ------------------------------------------------------------ 选中涟漪

    SEL_PULSE_FROM = "#c9d7ff"  # 比选中底色深一档的按下反馈色

    def _pulse_selection(self) -> None:
        """选中行的涟漪：sel-pulse tag 底色 4 帧从按下色退回选中色。
        连点重定向：取消在飞帧直接从头播，永不叠加。"""
        if self._pulse_job is not None:
            self.after_cancel(self._pulse_job)
            self._pulse_job = None
        sel = self.tree.selection()
        if not sel:
            return
        try:
            for item in sel:
                tags = tuple(t for t in self.tree.item(item, "tags") if t != "sel-pulse")
                self.tree.item(item, tags=(*tags, "sel-pulse"))
        except tk.TclError:
            return
        self._pulse_step = 0
        self._advance_pulse()

    def _advance_pulse(self) -> None:
        t = min(self._pulse_step / 4, 1.0)
        try:
            self.tree.tag_configure("sel-pulse", background=motion.lerp_color(
                self.SEL_PULSE_FROM, theme.TABLE_SELECT_BG, t))
            if t >= 1.0:
                for item in self.tree.selection():
                    tags = tuple(x for x in self.tree.item(item, "tags")
                                 if x != "sel-pulse")
                    self.tree.item(item, tags=tags)
                self._pulse_job = None
                return
        except tk.TclError:
            self._pulse_job = None
            return  # 控件已销毁
        self._pulse_step += 1
        self._pulse_job = self.after(16, self._advance_pulse)

    def _sort_by(self, col: str) -> None:
        self._sort_rev = not self._sort_rev if self._sort_col == col else False
        self._sort_col = col
        items = [(self.tree.set(k, col), k) for k in self.tree.get_children()]
        items.sort(reverse=self._sort_rev)
        for idx, (_, k) in enumerate(items):
            self.tree.move(k, "", idx)
        self._paint_sort_arrow()

    def _paint_sort_arrow(self) -> None:
        """表头排序指示：当前列 ▲/▼，其余恢复纯文字——点了没反馈等于没排序。"""
        for i, col in enumerate(self._columns):
            base = self._headings[i]
            if col == self._sort_col:
                self.tree.heading(col, text=f"{base} {'▼' if self._sort_rev else '▲'}")
            else:
                self.tree.heading(col, text=base)

    def _handle_double(self, _event: tk.Event) -> None:
        row = self.selected_row()
        if row and self._on_double:
            self._on_double(row)

    def _handle_right_click(self, event: tk.Event) -> None:
        item = self.tree.identify_row(event.y)
        if not item:
            return
        self.tree.selection_set(item)
        row = self.selected_row()
        if row and self._on_right_click:
            self._on_right_click(event, row)


class FormDialog(tk.Toplevel):
    """模态表单对话框。fields 是 (label, key, widget_type) 三元组列表。

    与全应用同一设计语言：白纸面 + 圆角输入框 + 主色主按钮。键盘即达：
    Enter 提交、Esc 取消，打开时焦点落首字段并全选——改一个旧值不用先
    清空（重命名/标签编辑的高频路径）。
    """

    def __init__(
        self,
        parent: tk.Widget,
        title: str,
        fields: Sequence[tuple[str, str, str]],
        *,
        initial: dict[str, str] | None = None,
        on_submit: Callable[[dict[str, str]], bool] | None = None,
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.configure(bg=theme.SURFACE)
        try:
            self.grab_set()  # 不可视/withdrawn 父窗上 grab 会失败，不挡表单本身
        except tk.TclError:
            pass
        self._on_submit = on_submit
        self._vars: dict[str, tk.StringVar] = {}
        self._entries: list[tk.Entry] = []
        self._result: dict[str, str] | None = None
        initial = initial or {}
        body = tk.Frame(self, bg=theme.SURFACE,
                        padx=theme.PAD_LG, pady=theme.PAD_LG)
        body.pack(fill="both", expand=True)
        tk.Label(body, text=title, font=theme.FONT_HEADING,
                 bg=theme.SURFACE, fg=theme.TEXT, anchor="w").grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, theme.GAP))
        self._build_fields(body, fields, initial)
        self._build_buttons(body)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.bind("<Escape>", lambda _e: self._on_cancel())
        for entry in self._entries:
            entry.bind("<Return>", lambda _e: self._on_ok())
        self.wait_visibility()
        self._center(parent)
        # 焦点落首字段并全选：编辑场景直接输入即覆盖旧值
        if self._entries:
            self._entries[0].focus_set()
            if self._entries[0].get():
                self._entries[0].select_range(0, "end")

    def _build_fields(
        self, parent: tk.Widget,
        fields: Sequence[tuple[str, str, str]],
        initial: dict[str, str],
    ) -> None:
        for i, (label, key, wtype) in enumerate(fields, start=1):
            tk.Label(parent, text=label, font=theme.FONT, bg=theme.SURFACE,
                     fg=theme.TEXT_SECONDARY, anchor="w").grid(
                row=i, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=initial.get(key, ""))
            self._vars[key] = var
            entry = RoundEntry(parent, textvariable=var, width=340,
                               show="*" if wtype == "password" else "")
            entry.grid(row=i, column=1, sticky="ew", pady=4, padx=(theme.GAP, 0))
            self._entries.append(entry.entry)
        parent.grid_columnconfigure(1, weight=1)

    def _build_buttons(self, parent: tk.Widget) -> None:
        bar = tk.Frame(parent, bg=theme.SURFACE)
        bar.grid(row=len(self._vars) + 1, column=0, columnspan=2,
                 sticky="e", pady=(theme.PAD, 0))
        RoundButton(bar, "取消", self._on_cancel).pack(side="left")
        RoundButton(bar, "确定", self._on_ok, kind="accent").pack(
            side="left", padx=(theme.GAP, 0))

    def _on_ok(self) -> None:
        self._result = {k: v.get() for k, v in self._vars.items()}
        if self._on_submit and not self._on_submit(self._result):
            return
        self.destroy()

    def _on_cancel(self) -> None:
        self._result = None
        self.destroy()

    def _center(self, parent: tk.Widget) -> None:
        self.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_x(), parent.winfo_y()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")

    @property
    def result(self) -> dict[str, str] | None:
        return self._result


class PageHeader(ttk.Frame):
    """页头：图标芯片 + 大标题 + 一行副标题，全应用统一的页面开场。

    icon 传 icons 模块的绘制函数；不传则纯文字（兼容旧调用点）。
    """

    def __init__(self, parent: tk.Widget, title: str, subtitle: str = "",
                 *, draw_icon=None) -> None:
        super().__init__(parent, style="Page.TFrame")
        from kv.gui.paint import round_points

        chip_size = _px(40)
        if draw_icon is not None:
            chip = tk.Canvas(
                self, width=chip_size, height=chip_size, bg=theme.CANVAS,
                highlightthickness=0, bd=0,
            )
            chip.grid(row=0, column=0, rowspan=2, padx=(0, theme.PAD_SM))
            pts = round_points(1, 1, chip_size - 1, chip_size - 1, _px(11))
            chip.create_polygon(*pts, fill=theme.ACCENT_SOFT, outline="", smooth=True)
            draw_icon(chip, chip_size / 2, chip_size / 2, _px(19), theme.ACCENT)
        ttk.Label(self, text=title, style="PageHeading.TLabel").grid(
            row=0, column=1, sticky="w",
        )
        if subtitle:
            ttk.Label(self, text=subtitle, style="PageSub.TLabel").grid(
                row=1, column=1, sticky="w", pady=(2, 0),
            )


def _px(base: int) -> int:
    return max(round(base * theme.SCALE), 1)


class StatusLabel(ttk.Frame):
    """底部状态栏：1px 上描边 + 状态点 + 状态文字。

    状态点把「最后一条消息的性质」编码成颜色（中性灰 / 成功绿 / 错误红），
    滚屏里一眼能扫到结果变了没有。
    """

    def __init__(self, parent: tk.Widget):
        super().__init__(parent, height=theme.STATUS_HEIGHT, style="TFrame")
        self.pack(fill="x", side="bottom")
        self.pack_propagate(False)
        ttk.Separator(self, orient="horizontal").pack(fill="x", side="top")
        self._dot = tk.Canvas(
            self, width=theme.PAD_SM, height=theme.PAD_SM,
            bg=theme.SURFACE, highlightthickness=0, bd=0,
        )
        self._dot.pack(side="left", padx=(theme.PAD, 0), pady=(theme.PAD_SM, 0))
        self._dot.create_oval(
            2, 2, theme.PAD_SM - 2, theme.PAD_SM - 2,
            fill=theme.TEXT_TERTIARY, outline="",
        )
        self._label = ttk.Label(
            self, text="就绪", font=theme.FONT_SMALL, anchor="w",
            foreground=theme.TEXT_SECONDARY,
        )
        self._label.pack(fill="both", expand=True, padx=theme.PAD_SM)

    def set(self, text: str, *, color: str = "") -> None:
        self._label.config(text=text, foreground=color or theme.TEXT_SECONDARY)
        self._dot.delete("all")
        self._dot.create_oval(
            2, 2, theme.PAD_SM - 2, theme.PAD_SM - 2,
            fill=color or theme.TEXT_TERTIARY, outline="",
        )
