"""可复用 GUI 组件。表格、表单对话框、确认框、状态栏、页头、可滚动页。"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, Sequence

from kv.gui import theme
from kv.gui.controls import RoundButton, SlimScrollbar


class ScrollPage(ttk.Frame):
    """可滚动页：内容自然高度超过视口时自动出细滚动条。

    背景：窗口高度被屏幕上限卡住（min(want, 88% 屏幕)），高 UI 缩放档位下
    固定表单的总请求高度会超过视口——pack 对放不下的内容直接裁掉，
    「设置页保存按钮被切一半」就是这么来的。子控件 pack 进 self.content。

    滚轮滚动由 App 全局委派给指针所在页的 wheel_scroll()——页面全部
    place 堆叠，各页自己 bind_all 会串页。
    """

    def __init__(self, parent: tk.Widget, *, padding=None) -> None:
        super().__init__(parent, style="Page.TFrame",
                         padding=padding if padding is not None else theme.PAD)
        self._canvas = tk.Canvas(
            self, bg=theme.CANVAS, highlightthickness=0, bd=0,
            yscrollincrement=max(round(32 * theme.SCALE), 8),
        )
        self._canvas.pack(side="left", fill="both", expand=True)
        self._sb = SlimScrollbar(self, self._canvas)
        self.content = tk.Frame(self._canvas, bg=theme.CANVAS)
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
            if not self._sb.winfo_manager():
                self._sb.pack(side="right", fill="y", before=self._canvas)
        else:
            self._sb.pack_forget()
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

    def _build(self, headings: Sequence[str], widths: Sequence[int] | None) -> None:
        self.tree = ttk.Treeview(
            self, columns=self._columns, show="headings", selectmode="browse",
        )
        # 细自绘滚动条；横滚动条去掉——列宽由内容决定，横向拖动是老式观感的来源
        vsb = SlimScrollbar(self, self.tree)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
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
        if self._on_double:
            self.tree.bind("<Double-1>", self._handle_double)
        if self._on_right_click:
            self.tree.bind("<Button-3>", self._handle_right_click)

    def clear(self) -> None:
        self._hover_item = None
        self.tree.delete(*self.tree.get_children())

    def _fit_columns(self) -> None:
        """按声明比例把视口宽分摊给各列——任何缩放档位下列都完整可见。"""
        avail = self.tree.winfo_width() - theme.sp(10)  # 减自绘滚动条
        if avail < theme.sp(120):
            return
        for i, col in enumerate(self._columns):
            self.tree.column(col, width=max(round(avail * self._ratios[i]),
                                            theme.sp(48)))

    def insert_row(self, values: Sequence[Any], *, tags: Sequence[str] = ()) -> str:
        return self.tree.insert("", "end", values=values, tags=tuple(tags))

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
    """模态表单对话框。fields 是 (label, key, widget_type) 三元组列表。"""

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
        self.grab_set()
        self._on_submit = on_submit
        self._vars: dict[str, tk.StringVar] = {}
        self._result: dict[str, str] | None = None
        initial = initial or {}
        body = ttk.Frame(self, padding=theme.PAD_LG)
        body.pack(fill="both", expand=True)
        self._build_fields(body, fields, initial)
        self._build_buttons(body)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.wait_visibility()
        self._center(parent)

    def _build_fields(
        self, parent: tk.Widget,
        fields: Sequence[tuple[str, str, str]],
        initial: dict[str, str],
    ) -> None:
        for i, (label, key, wtype) in enumerate(fields):
            ttk.Label(parent, text=label).grid(row=i, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=initial.get(key, ""))
            self._vars[key] = var
            if wtype == "password":
                w = ttk.Entry(parent, textvariable=var, show="*", width=40)
            elif wtype == "text":
                w = ttk.Entry(parent, textvariable=var, width=40)
            else:
                w = ttk.Entry(parent, textvariable=var, width=40)
            w.grid(row=i, column=1, sticky="ew", pady=4, padx=(theme.GAP, 0))
        parent.grid_columnconfigure(1, weight=1)

    def _build_buttons(self, parent: tk.Widget) -> None:
        bar = ttk.Frame(parent)
        bar.grid(row=len(self._vars), column=0, columnspan=2, pady=(theme.PAD, 0))
        RoundButton(bar, "确定", self._on_ok, kind="accent").pack(side="left", padx=theme.GAP)
        RoundButton(bar, "取消", self._on_cancel).pack(side="left")

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
