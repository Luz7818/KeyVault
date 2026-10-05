"""安全页：泄露扫描、审计日志、过期检查、清除。"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from kv.gui import theme
from kv.gui.widgets import DataTable

SCAN_COLS = ("path", "line_no", "name", "platform", "status")
SCAN_HEADS = ("文件", "行号", "密钥", "平台", "状态")
SCAN_WIDTHS = (280, 50, 120, 100, 70)

AUDIT_COLS = ("id", "ts", "event", "actor", "name_snapshot", "sha256_prefix")
AUDIT_HEADS = ("ID", "时间", "事件", "操作者", "记录名", "sha256")
AUDIT_WIDTHS = (40, 150, 100, 60, 120, 100)


class SecurityPage(ttk.Frame):
    def __init__(self, parent: tk.Widget, *, app) -> None:
        super().__init__(parent, padding=theme.PAD)
        self._app = app
        self._build()

    def _build(self) -> None:
        ttk.Label(self, text="安全", font=theme.FONT_HEADING).pack(anchor="w")
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, pady=(theme.GAP, 0))
        self._scan_tab = ttk.Frame(nb, padding=theme.PAD)
        self._audit_tab = ttk.Frame(nb, padding=theme.PAD)
        self._maint_tab = ttk.Frame(nb, padding=theme.PAD)
        nb.add(self._scan_tab, text="泄露扫描")
        nb.add(self._audit_tab, text="审计日志")
        nb.add(self._maint_tab, text="过期 / 清除")
        self._build_scan()
        self._build_audit()
        self._build_maint()

    def _build_scan(self) -> None:
        bar = ttk.Frame(self._scan_tab)
        bar.pack(fill="x")
        self._scan_dir = tk.StringVar(value=".")
        ttk.Entry(bar, textvariable=self._scan_dir, width=40).pack(side="left", fill="x", expand=True)
        ttk.Button(bar, text="浏览", command=self._pick_dir).pack(side="left", padx=theme.GAP)
        ttk.Button(bar, text="扫描", command=self._do_scan).pack(side="left")
        self._scan_table = DataTable(self._scan_tab, SCAN_COLS, headings=SCAN_HEADS, widths=SCAN_WIDTHS)
        self._scan_table.pack(fill="both", expand=True, pady=(theme.GAP, 0))

    def _build_audit(self) -> None:
        bar = ttk.Frame(self._audit_tab)
        bar.pack(fill="x")
        ttk.Button(bar, text="加载日志", command=self._load_audit).pack(side="left")
        ttk.Button(bar, text="校验链", command=self._verify_audit).pack(side="left", padx=theme.GAP)
        self._audit_table = DataTable(self._audit_tab, AUDIT_COLS, headings=AUDIT_HEADS, widths=AUDIT_WIDTHS)
        self._audit_table.pack(fill="both", expand=True, pady=(theme.GAP, 0))

    def _build_maint(self) -> None:
        exp = ttk.LabelFrame(self._maint_tab, text="过期检查", padding=theme.PAD)
        exp.pack(fill="x")
        ttk.Button(exp, text="标记过期记录", command=self._do_expire).pack(side="left")
        self._expire_label = ttk.Label(exp, text="", font=theme.FONT_SMALL)
        self._expire_label.pack(side="left", padx=theme.PAD)
        pur = ttk.LabelFrame(self._maint_tab, text="清除", padding=theme.PAD)
        pur.pack(fill="x", pady=(theme.PAD, 0))
        self._purge_mode = tk.StringVar(value="zero")
        ttk.Radiobutton(pur, text="归零（保留元数据）", variable=self._purge_mode, value="zero").pack(anchor="w")
        ttk.Radiobutton(pur, text="删除（整行移除）", variable=self._purge_mode, value="delete").pack(anchor="w")
        btn_bar = ttk.Frame(pur)
        btn_bar.pack(fill="x", pady=(theme.GAP, 0))
        ttk.Button(btn_bar, text="预览（dry-run）", command=lambda: self._do_purge(dry=True)).pack(side="left")
        ttk.Button(btn_bar, text="执行清除", command=lambda: self._do_purge(dry=False)).pack(side="left", padx=theme.GAP)

    def _pick_dir(self) -> None:
        d = filedialog.askdirectory(parent=self, title="选择扫描目录")
        if d:
            self._scan_dir.set(d)

    def _do_scan(self) -> None:
        from kv.selftest import gate_is_current, gate_message

        store = self._app.store
        if store is None:
            messagebox.showwarning("未初始化", "请先初始化 vault。", parent=self)
            return
        if not gate_is_current(store):
            messagebox.showwarning("Gate 未通过", gate_message(), parent=self)
            return
        target = self._scan_dir.get().strip() or "."
        self._app.set_status(f"扫描 {target} …")
        self._app.run_async(_run_scan, store, target, on_done=self._show_scan)

    def _show_scan(self, result) -> None:
        self._scan_table.clear()
        for hit in result.hits:
            self._scan_table.insert_row((hit.path, str(hit.line_no), hit.name, hit.platform, hit.status))
        n = len(result.hits)
        color = theme.ERROR if n else theme.SUCCESS
        self._app.set_status(
            f"扫描完成：{n} 处泄露，{result.scanned} 个文件", color=color,
        )

    def _load_audit(self) -> None:
        store = self._app.store
        if store is None:
            return
        self._app.set_status("加载审计日志…")
        self._app.run_async(store.list_audit, limit=200, on_done=self._show_audit)

    def _show_audit(self, events: list) -> None:
        self._audit_table.clear()
        for e in events:
            self._audit_table.insert_row((
                str(e["id"]), e["ts"][:19], e["event"], e["actor"],
                e["name_snapshot"] or "", (e["sha256_prefix"] or "")[:12],
            ))
        self._app.set_status(f"{len(events)} 条审计记录")

    def _verify_audit(self) -> None:
        store = self._app.store
        if store is None:
            return
        result = store.verify_audit()
        if result.ok:
            messagebox.showinfo("校验通过", f"审计链完整，{result.count} 条记录。", parent=self)
            self._app.set_status("审计链校验通过", color=theme.SUCCESS)
        else:
            messagebox.showerror("校验失败", f"链在 id={result.broken_at} 处断裂。", parent=self)
            self._app.set_status("审计链断裂", color=theme.ERROR)

    def _do_expire(self) -> None:
        from kv.ops import expiry as expiryops

        store = self._app.store
        if store is None:
            return
        result = expiryops.mark_expired(store)
        self._expire_label.config(text=f"标记了 {result.expired_count} 条")
        self._app.set_status(f"过期检查：{result.expired_count} 条", color=theme.SUCCESS)

    def _do_purge(self, *, dry: bool) -> None:
        from kv.ops import purge as purgeops

        store = self._app.store
        if store is None:
            return
        mode = self._purge_mode.get()
        if not dry and not messagebox.askyesno(
            "确认", f"以 {mode} 模式清除 revoked/expired 记录？", parent=self,
        ):
            return
        if dry:
            self._app.set_status("预览中…")
            self._app.run_async(
                _count_purge_targets, store, on_done=lambda n: self._app.set_status(
                    f"将被清除：{n} 条（dry-run）",
                ),
            )
            return
        self._app.run_async(
            purgeops.purge, store, zero=(mode == "zero"), delete=(mode == "delete"), actor="gui",
            on_done=self._show_purge,
        )

    def _show_purge(self, result) -> None:
        self._app.set_status(
            f"清除完成：归零 {result.zeroed}，删除 {result.deleted}", color=theme.SUCCESS,
        )
        messagebox.showinfo(
            "清除完成", f"归零：{result.zeroed}\n删除：{result.deleted}", parent=self,
        )


def _run_scan(store, target: str) -> Any:
    from kv.ops import scan as scanops

    return scanops.scan_directory(store, target)


def _count_purge_targets(store) -> int:
    from kv.core import repo

    with store.read() as conn:
        revoked = len(repo.list_secrets_by_status(conn, "revoked"))
        expired = len(repo.list_secrets_by_status(conn, "expired"))
    return revoked + expired
