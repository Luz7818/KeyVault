"""捕获页：watch/review/sweep/fix/set-platform/rename/tag/corrections。"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from kv.gui import theme
from kv.gui.controls import Card, RoundButton, TabBar
from kv.gui.widgets import DataTable, FormDialog, PageHeader

CORR_COLS = ("id", "kind", "pattern", "platform")
CORR_HEADS = ("ID", "类型", "模式", "平台")
CORR_WIDTHS = (40, 80, 200, 120)

FIX_COLS = ("name", "platform", "confidence", "preview")
FIX_HEADS = ("名称", "当前平台", "置信度", "预览")
FIX_WIDTHS = (140, 120, 80, 200)


class CapturePage(ttk.Frame):
    def __init__(self, parent: tk.Widget, *, app) -> None:
        super().__init__(parent, padding=theme.PAD)
        self._app = app
        self._watch_stop = threading.Event()
        self._watch_thread: threading.Thread | None = None
        self._build()

    def _build(self) -> None:
        PageHeader(self, "捕获", "剪贴板监听——候选先进审批队列，接受后才入库").pack(
            anchor="w", pady=(0, theme.GAP),
        )
        nb = TabBar(self)
        nb.pack(fill="both", expand=True, pady=(theme.GAP, 0))
        self._nb = nb
        self._watch_tab = nb.add("watch", "剪贴板监控")
        self._review_tab = nb.add("review", "审批队列")
        self._fix_tab = nb.add("fix", "修复未识别")
        self._corr_tab = nb.add("corr", "纠正规则")
        self._build_watch()
        self._build_review()
        self._build_fix()
        self._build_corrections()

    def _build_watch(self) -> None:
        bar = ttk.Frame(self._watch_tab)
        bar.pack(fill="x")
        self._watch_btn = RoundButton(
            bar, "开始监控", self._toggle_watch, kind="accent",
        )
        self._watch_btn.pack(side="left")
        RoundButton(bar, "清扫过期", self._do_sweep).pack(side="left", padx=theme.GAP)
        watch_card = Card(self._watch_tab)
        watch_card.pack(fill="both", expand=True, pady=(theme.GAP, 0))
        self._watch_log = tk.Listbox(
            watch_card.body, font=theme.FONT_SMALL, height=14,
            bg=theme.SURFACE, fg=theme.TEXT,
            selectbackground=theme.TABLE_SELECT_BG, selectforeground=theme.TEXT,
            activestyle="none",
        )
        self._watch_log.pack(fill="both", expand=True, padx=1, pady=1)

    def _build_review(self) -> None:
        bar = ttk.Frame(self._review_tab)
        bar.pack(fill="x")
        RoundButton(bar, "加载待审", self._load_inbox).pack(side="left")
        RoundButton(
            bar, "接受", lambda: self._review_action("accept"), kind="accent",
        ).pack(side="left", padx=theme.GAP)
        RoundButton(bar, "跳过", lambda: self._review_action("skip")).pack(side="left")
        review_card = Card(self._review_tab)
        review_card.pack(fill="both", expand=True, pady=(theme.GAP, 0))
        self._review_list = tk.Listbox(
            review_card.body, font=theme.FONT_SMALL, height=12,
            bg=theme.SURFACE, fg=theme.TEXT,
            selectbackground=theme.TABLE_SELECT_BG, selectforeground=theme.TEXT,
            activestyle="none",
        )
        self._review_list.pack(fill="both", expand=True, padx=1, pady=1)
        self._review_items: list[Any] = []

    def _build_fix(self) -> None:
        bar = ttk.Frame(self._fix_tab)
        bar.pack(fill="x")
        RoundButton(bar, "加载未识别", self._load_unresolved).pack(side="left")
        RoundButton(bar, "设置平台", self._fix_selected, kind="accent").pack(side="left", padx=theme.GAP)
        self._fix_table = DataTable(self._fix_tab, FIX_COLS, headings=FIX_HEADS, widths=FIX_WIDTHS)
        self._fix_table.pack(fill="both", expand=True, pady=(theme.GAP, 0))
        self._fix_rows: list[Any] = []

    def _build_corrections(self) -> None:
        bar = ttk.Frame(self._corr_tab)
        bar.pack(fill="x")
        RoundButton(bar, "加载", self._load_corrections).pack(side="left")
        RoundButton(bar, "删除选中", self._delete_correction, kind="danger").pack(side="left", padx=theme.GAP)
        self._corr_table = DataTable(self._corr_tab, CORR_COLS, headings=CORR_HEADS, widths=CORR_WIDTHS)
        self._corr_table.pack(fill="both", expand=True, pady=(theme.GAP, 0))

    def _toggle_watch(self) -> None:
        if self._watch_thread and self._watch_thread.is_alive():
            self._watch_stop.set()
            self._watch_btn.set_text("开始监控")
            self._log_watch("监控已停止")
            return
        store = self._app.store
        if store is None:
            messagebox.showwarning("未初始化", "请先初始化 vault。", parent=self)
            return
        self._watch_stop.clear()
        self._watch_btn.config(text="停止监控")
        self._log_watch("监控已启动")
        self._watch_thread = threading.Thread(
            target=self._watch_loop, args=(store,), daemon=True,
        )
        self._watch_thread.start()

    def _watch_loop(self, store) -> None:
        from kv.capture.clipboard import Win32Clipboard
        from kv.capture.watch import watch

        backend = Win32Clipboard()
        try:
            watch(
                store, backend, interval=1.0, spool=True, auto=False,
                window_hint=False, stop_event=self._watch_stop,
                on_event=self._log_watch_safe,
            )
        except Exception as exc:
            self._log_watch_safe(f"错误：{exc}")

    def _log_watch_safe(self, msg: str) -> None:
        self.after(0, lambda: self._log_watch(msg))

    def _log_watch(self, msg: str) -> None:
        self._watch_log.insert("end", msg)
        self._watch_log.see("end")

    def _do_sweep(self) -> None:
        from kv.capture.clipboard import Win32Clipboard
        from kv.capture.wipe import sweep_due_wipes

        store = self._app.store
        if store is None:
            return
        swept = sweep_due_wipes(store, Win32Clipboard(), actor="gui")
        self._app.set_status(
            f"清扫了 {len(swept)} 条" if swept else "没有过期未擦的内容",
            color=theme.SUCCESS if swept else "",
        )

    def _load_inbox(self) -> None:
        from kv.ops.review import pending_from_inbox

        store = self._app.store
        if store is None:
            return
        self._review_items = pending_from_inbox(store)
        self._review_list.delete(0, "end")
        for i, item in enumerate(self._review_items):
            v = item.verdict
            self._review_list.insert(
                "end", f"{i + 1}. {v.platform}（{v.confidence}）{item.candidate.preview('*')}",
            )
        self._app.set_status(f"{len(self._review_items)} 条待审")

    def _review_action(self, action: str) -> None:
        sel = self._review_list.curselection()
        if not sel or not self._review_items:
            return
        idx = sel[0]
        item = self._review_items[idx]
        if action == "accept":
            self._accept_item(item)
        self._review_list.delete(idx)
        self._review_items.pop(idx)

    def _accept_item(self, item) -> None:
        from kv.ops import save as saveops

        store = self._app.store
        if store is None:
            return
        policy = saveops.SavePolicy(origin="watch", actor="gui", force=True)
        try:
            saveops.commit(item.candidate, item.verdict, store=store, policy=policy)
            self._app.set_status(f"已接受：{item.verdict.platform}", color=theme.SUCCESS)
        except Exception as exc:
            messagebox.showerror("接受失败", str(exc), parent=self)

    def _load_unresolved(self) -> None:
        from kv.ops.fix import unresolved

        store = self._app.store
        if store is None:
            return
        self._fix_rows = unresolved(store)
        self._fix_table.clear()
        for row in self._fix_rows:
            self._fix_table.insert_row((row.name, row.platform, row.confidence, row.preview("*")))
        self._app.set_status(f"{len(self._fix_rows)} 条未识别")

    def _fix_selected(self) -> None:
        from kv.detect import rules

        row = self._fix_table.selected_row()
        if not row:
            messagebox.showinfo("提示", "请先选中一条记录。", parent=self)
            return
        platforms = list(rules.all_platforms())
        win = tk.Toplevel(self)
        win.title(f"设置平台 — {row['name']}")
        win.geometry("300x400")
        win.transient(self)
        ttk.Label(win, text="选择平台：").pack(padx=12, pady=8, anchor="w")
        lb = tk.Listbox(
            win, font=theme.FONT, bg=theme.SURFACE, fg=theme.TEXT,
            selectbackground=theme.TABLE_SELECT_BG, selectforeground=theme.TEXT,
            activestyle="none",
        )
        theme.flat(lb)
        lb.pack(fill="both", expand=True, padx=12)
        for p in platforms:
            lb.insert("end", p)
        def on_ok() -> None:
            sel = lb.curselection()
            if not sel:
                return
            platform = lb.get(sel[0])
            store = self._app.store
            if store is None:
                return
            try:
                store.set_platform(row["name"], platform, evidence=f"gui set-platform {platform}")
            except Exception as exc:
                messagebox.showerror("失败", str(exc), parent=win)
                return
            win.destroy()
            self._app.set_status(f"已设置：{row['name']} → {platform}", color=theme.SUCCESS)
            self._load_unresolved()
        RoundButton(win, "确定", on_ok, kind="accent").pack(pady=theme.PAD_SM)

    def _load_corrections(self) -> None:
        from kv.ops.fix import list_corrections

        store = self._app.store
        if store is None:
            return
        rows = list_corrections(store)
        self._corr_table.clear()
        for r in rows:
            self._corr_table.insert_row((str(r["id"]), r["kind"], r["pattern"], r["platform"]))
        self._app.set_status(f"{len(rows)} 条纠正规则")

    def _delete_correction(self) -> None:
        from kv.ops.fix import drop_correction

        row = self._corr_table.selected_row()
        if not row:
            return
        store = self._app.store
        if store is None:
            return
        if not messagebox.askyesno("确认", f"删除纠正规则 #{row['id']}？", parent=self):
            return
        drop_correction(store, int(row["id"]))
        self._app.set_status("已删除", color=theme.SUCCESS)
        self._load_corrections()
