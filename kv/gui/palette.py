"""Ctrl+K 命令面板：模糊搜索密钥，回车复制到剪贴板。

Raycast 式交互：无边框小窗、输入即过滤、↑↓ 选择、Enter 执行、Esc 关闭。
匹配排序：前缀 > 包含 > 子序列，同分按名字长度短者优先。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from typing import Any

from kv.gui import theme
from kv.gui.controls import RoundEntry


def _score(query: str, name: str) -> int:
    """模糊匹配打分；0 表示不匹配。"""
    q, n = query.lower(), name.lower()
    if n.startswith(q):
        return 100 - len(n) // 10
    if q in n:
        return 80 - len(n) // 10
    # 子序列：字符按序出现即可
    it = iter(n)
    if all(ch in it for ch in q):
        return 60 - len(n) // 10
    return 0


class Palette(tk.Toplevel):
    """命令面板。open_palette(app) 是推荐入口；直接构造需要 parent/app。"""

    PAD_X = 2

    def __init__(self, app) -> None:
        super().__init__(app)
        self._app = app
        self._rows: list[dict[str, Any]] = []
        self._selected = 0
        self.overrideredirect(True)
        self.configure(bg=theme.BORDER_STRONG, bd=1)
        self._input_var = tk.StringVar()
        self._input_var.trace_add("write", lambda *_: self._refresh())
        self._input = RoundEntry(self, textvariable=self._input_var, width=560)
        self._input.pack(fill="x", padx=theme.PAD_SM, pady=theme.PAD_SM)
        self._input.entry.bind("<Up>", lambda e: self._move(-1))
        self._input.entry.bind("<Down>", lambda e: self._move(1))
        self._input.entry.bind("<Return>", lambda e: self._execute())
        self.bind("<Escape>", lambda e: self.close())
        self._input.entry.bind("<Escape>", lambda e: self.close())
        self._list = tk.Listbox(
            self, font=theme.FONT, bg=theme.SURFACE, fg=theme.TEXT,
            selectbackground=theme.TABLE_SELECT_BG, selectforeground=theme.TEXT,
            relief="flat", bd=0, highlightthickness=0, activestyle="none",
        )
        self._list.pack(fill="both", expand=True, padx=theme.PAD_SM, pady=(0, theme.PAD_SM))
        self._list.bind("<Button-1>", self._on_click)
        self._list.bind("<Double-1>", lambda e: self._execute())
        self._refresh()
        self._center()

    # ------------------------------------------------------------ 数据

    def _refresh(self) -> None:
        store = self._app.store
        self._list.delete(0, "end")
        self._rows = []
        if store is None:
            self._list.insert("end", "vault 未初始化——先到设置页初始化")
            return
        query = self._input_var.get().strip()
        scored = []
        for row in store.list_secrets():
            s = _score(query, row.name) if query else 50
            if s:
                scored.append((s, row))
        scored.sort(key=lambda pair: (-pair[0], pair[1].name))
        self._rows = [row for _, row in scored[:9]]
        if not self._rows:
            self._list.insert("end", "没有匹配的密钥")
            return
        for row in self._rows:
            self._list.insert(
                "end", f"  {row.name}    {row.platform}    {row.preview('*')}",
            )
        self._selected = 0
        self._list.selection_set(0)

    def _move(self, delta: int) -> None:
        if not self._rows:
            return
        self._selected = max(0, min(self._selected + delta, len(self._rows) - 1))
        self._list.selection_clear(0, "end")
        self._list.selection_set(self._selected)
        self._list.see(self._selected)

    def _on_click(self, event: tk.Event) -> None:
        index = self._list.nearest(event.y)
        if 0 <= index < len(self._rows):
            self._selected = index
            self._execute()

    def _execute(self) -> None:
        if not self._rows:
            self.close()
            return
        row = self._rows[min(self._selected, len(self._rows) - 1)]
        self.close()
        self._app.copy_secret_by_name(row.name)

    def _center(self) -> None:
        self.update_idletasks()
        parent = self.master
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw = parent.winfo_width()
        w = self.winfo_reqwidth() + 2
        h = self.winfo_reqheight()
        self.geometry(f"{w}x{h}+{px + (pw - w) // 2}+{py + theme.PAD_LG}")

    def close(self) -> None:
        self.grab_release()
        self.destroy()


def open_palette(app) -> None:
    """打开命令面板；vault 未初始化时也能打开（面板内会提示）。"""
    win = Palette(app)
    win.grab_set()
    win._input.entry.focus_set()
