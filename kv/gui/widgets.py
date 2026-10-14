"""可复用 GUI 组件。表格、表单对话框、确认框、状态栏、页头。"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, Sequence

from kv.gui import theme
from kv.gui.controls import RoundButton, SlimScrollbar


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
        for i, col in enumerate(self._columns):
            self.tree.heading(col, text=headings[i], command=lambda c=col: self._sort_by(c))
            w = widths[i] if widths and i < len(widths) else 120
            self.tree.column(col, width=w, minwidth=60)
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
    """页头：大标题 + 一行副标题，全应用统一的页面开场。坐在画布色上。"""

    def __init__(self, parent: tk.Widget, title: str, subtitle: str = "") -> None:
        super().__init__(parent, style="Page.TFrame")
        ttk.Label(self, text=title, style="PageHeading.TLabel").pack(anchor="w")
        if subtitle:
            ttk.Label(self, text=subtitle, style="PageSub.TLabel").pack(anchor="w", pady=(2, 0))


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
