"""可复用 GUI 组件。表格、表单对话框、确认框、状态栏。"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, Sequence

from kv.gui import theme


class DataTable(ttk.Frame):
    """带列排序的 Treeview 表格。"""

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
        self._build(headings or columns, widths)

    def _build(self, headings: Sequence[str], widths: Sequence[int] | None) -> None:
        self.tree = ttk.Treeview(
            self, columns=self._columns, show="headings", selectmode="browse",
        )
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        for i, col in enumerate(self._columns):
            self.tree.heading(col, text=headings[i], command=lambda c=col: self._sort_by(c))
            w = widths[i] if widths and i < len(widths) else 120
            self.tree.column(col, width=w, minwidth=60)
        self.tree.tag_configure("odd", background=theme.TABLE_ALT_BG)
        self.tree.tag_configure("even", background=theme.CONTENT_BG)
        if self._on_double:
            self.tree.bind("<Double-1>", self._handle_double)
        if self._on_right_click:
            self.tree.bind("<Button-3>", self._handle_right_click)

    def clear(self) -> None:
        self.tree.delete(*self.tree.get_children())

    def insert_row(self, values: Sequence[Any], *, tags: Sequence[str] = ()) -> str:
        idx = len(self.tree.get_children())
        row_tags = list(tags) + ["odd" if idx % 2 else "even"]
        return self.tree.insert("", "end", values=values, tags=row_tags)

    def selected_row(self) -> dict[str, Any] | None:
        sel = self.tree.selection()
        if not sel:
            return None
        vals = self.tree.item(sel[0], "values")
        return dict(zip(self._columns, vals))

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
        ttk.Button(bar, text="确定", command=self._on_ok).pack(side="left", padx=theme.GAP)
        ttk.Button(bar, text="取消", command=self._on_cancel).pack(side="left")

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


class StatusLabel(ttk.Frame):
    """底部状态栏。"""

    def __init__(self, parent: tk.Widget):
        super().__init__(parent, height=theme.STATUS_HEIGHT)
        self.pack(fill="x", side="bottom")
        self._label = ttk.Label(self, text="就绪", font=theme.FONT_SMALL, anchor="w")
        self._label.pack(fill="x", padx=theme.PAD, pady=2)

    def set(self, text: str, *, color: str = "") -> None:
        self._label.config(text=text, foreground=color or theme.TEXT_SECONDARY)
