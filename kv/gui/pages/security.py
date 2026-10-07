"""安全页：泄露扫描、审计日志、过期检查、清除。"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from kv.gui import theme
from kv.gui.controls import Card, RoundButton, RoundEntry, TabBar
from kv.gui.widgets import DataTable, PageHeader

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
        PageHeader(self, "安全", "泄露扫描、审计溯源、过期清除").pack(
            anchor="w", pady=(0, theme.GAP),
        )
        nb = TabBar(self)
        self._nb = nb
        nb.pack(fill="both", expand=True, pady=(theme.GAP, 0))
        self._scan_tab = nb.add("scan", "泄露扫描")
        self._audit_tab = nb.add("audit", "审计日志")
        self._maint_tab = nb.add("maint", "过期 / 清除")
        self._backup_tab = nb.add("backup", "备份 / 恢复")
        self._build_scan()
        self._build_audit()
        self._build_maint()
        self._build_backup()

    def _build_scan(self) -> None:
        bar = ttk.Frame(self._scan_tab)
        bar.pack(fill="x")
        self._scan_dir = tk.StringVar(value=".")
        RoundEntry(bar, textvariable=self._scan_dir, width=460).pack(
            side="left", fill="x", expand=True,
        )
        RoundButton(bar, "浏览", self._pick_dir).pack(side="left", padx=theme.GAP)
        RoundButton(bar, "扫描", self._do_scan, kind="accent").pack(side="left")
        scan_card = Card(self._scan_tab)
        scan_card.pack(fill="both", expand=True, pady=(theme.GAP, 0))
        self._scan_table = DataTable(
            scan_card.body, SCAN_COLS, headings=SCAN_HEADS, widths=SCAN_WIDTHS,
        )
        self._scan_table.pack(fill="both", expand=True, padx=1, pady=1)

    def _build_audit(self) -> None:
        bar = ttk.Frame(self._audit_tab)
        bar.pack(fill="x")
        RoundButton(bar, "加载日志", self._load_audit).pack(side="left")
        RoundButton(bar, "校验链", self._verify_audit).pack(side="left", padx=theme.GAP)
        audit_card = Card(self._audit_tab)
        audit_card.pack(fill="both", expand=True, pady=(theme.GAP, 0))
        self._audit_table = DataTable(
            audit_card.body, AUDIT_COLS, headings=AUDIT_HEADS, widths=AUDIT_WIDTHS,
        )
        self._audit_table.pack(fill="both", expand=True, padx=1, pady=1)

    def _build_maint(self) -> None:
        exp = ttk.LabelFrame(self._maint_tab, text="过期检查", padding=theme.PAD)
        exp.pack(fill="x")
        RoundButton(exp, "标记过期记录", self._do_expire).pack(side="left")
        RoundButton(exp, "健康报告", self._do_health).pack(side="left", padx=theme.GAP)
        self._expire_label = ttk.Label(exp, text="", font=theme.FONT_SMALL, wraplength=640)
        self._expire_label.pack(side="left", padx=theme.PAD)
        pur = ttk.LabelFrame(self._maint_tab, text="清除", padding=theme.PAD)
        pur.pack(fill="x", pady=(theme.PAD, 0))
        self._purge_mode = tk.StringVar(value="zero")
        ttk.Radiobutton(pur, text="归零（保留元数据）", variable=self._purge_mode, value="zero").pack(anchor="w")
        ttk.Radiobutton(pur, text="删除（整行移除）", variable=self._purge_mode, value="delete").pack(anchor="w")
        btn_bar = ttk.Frame(pur)
        btn_bar.pack(fill="x", pady=(theme.GAP, 0))
        RoundButton(btn_bar, "预览（dry-run）", lambda: self._do_purge(dry=True)).pack(side="left")
        RoundButton(btn_bar, "执行清除", lambda: self._do_purge(dry=False), kind="danger").pack(side="left", padx=theme.GAP)

    def _build_backup(self) -> None:
        ttk.Label(
            self._backup_tab, style="Sub.TLabel",
            text="备份是口令加密的明文导出（.kvb），可在任何 Windows 机器恢复——"
                 "DPAPI 绑定本机，重装系统后本库无法解密，备份是唯一出路。"
                 "口令是唯一凭据：丢失无法恢复，弱口令等于没加密。",
            wraplength=720, justify="left",
        ).pack(anchor="w", pady=(0, theme.GAP))
        exp = ttk.LabelFrame(self._backup_tab, text="导出备份", padding=theme.PAD)
        exp.pack(fill="x")
        pw_row = ttk.Frame(exp)
        pw_row.pack(fill="x")
        ttk.Label(pw_row, text="口令", style="Sub.TLabel").pack(side="left")
        self._bk_pw = tk.StringVar()
        RoundEntry(pw_row, textvariable=self._bk_pw, width=200, show="*").pack(side="left", padx=(theme.GAP, theme.GAP))
        ttk.Label(pw_row, text="确认口令", style="Sub.TLabel").pack(side="left")
        self._bk_pw2 = tk.StringVar()
        RoundEntry(pw_row, textvariable=self._bk_pw2, width=200, show="*").pack(side="left", padx=theme.GAP)
        path_row = ttk.Frame(exp)
        path_row.pack(fill="x", pady=(theme.GAP, 0))
        self._bk_path = tk.StringVar()
        RoundEntry(path_row, textvariable=self._bk_path, width=480).pack(side="left", fill="x", expand=True)
        RoundButton(path_row, "浏览", self._pick_backup_path).pack(side="left", padx=theme.GAP)
        RoundButton(path_row, "导出备份", self._do_backup, kind="accent").pack(side="left")

        res = ttk.LabelFrame(self._backup_tab, text="从备份恢复", padding=theme.PAD)
        res.pack(fill="x", pady=(theme.PAD, 0))
        res_row = ttk.Frame(res)
        res_row.pack(fill="x")
        self._rs_path = tk.StringVar()
        RoundEntry(res_row, textvariable=self._rs_path, width=420).pack(side="left", fill="x", expand=True)
        RoundButton(res_row, "浏览", self._pick_restore_path).pack(side="left", padx=theme.GAP)
        ttk.Label(res_row, text="口令", style="Sub.TLabel").pack(side="left")
        self._rs_pw = tk.StringVar()
        RoundEntry(res_row, textvariable=self._rs_pw, width=180, show="*").pack(side="left", padx=(theme.GAP, theme.GAP))
        RoundButton(res_row, "恢复", self._do_restore, kind="accent").pack(side="left")
        ttk.Label(
            res, style="Muted.TLabel",
            text="恢复把备份里的记录合并进当前库：重名同值自动跳过，不会覆盖已有记录。",
        ).pack(anchor="w", pady=(theme.GAP, 0))

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

    def _do_health(self) -> None:
        from kv.ops.health import health_report

        store = self._app.store
        if store is None:
            return
        report = health_report(store)
        parts = []
        if report.expired_count:
            parts.append(f"已过期 {report.expired_count} 条")
        if report.expiring:
            parts.append(f"30 天内到期 {len(report.expiring)} 条（{'、'.join(report.expiring[:6])}）")
        if report.stale:
            parts.append(f"超过 180 天未轮换 {len(report.stale)} 条（{'、'.join(report.stale[:6])}）")
        text = "；".join(parts) if parts else "一切正常：没有过期、临期或长期未轮换的记录"
        self._expire_label.config(text=text)
        self._app.set_status("健康检查完成", color=theme.SUCCESS)

    def _pick_backup_path(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self, title="备份保存到",
            defaultextension=".kvb", filetypes=[("KeyVault 备份", "*.kvb")],
        )
        if path:
            self._bk_path.set(path)

    def _pick_restore_path(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="选择备份文件",
            filetypes=[("KeyVault 备份", "*.kvb"), ("所有文件", "*.*")],
        )
        if path:
            self._rs_path.set(path)

    def _do_backup(self) -> None:
        from pathlib import Path

        from kv.ops import backup as backupops

        store = self._app.store
        if store is None:
            return
        pw, pw2 = self._bk_pw.get(), self._bk_pw2.get()
        path = self._bk_path.get().strip()
        if not path:
            messagebox.showinfo("提示", "请选择备份保存位置。", parent=self)
            return
        if len(pw) < 8:
            messagebox.showwarning("口令太短", "备份口令至少 8 位——它是唯一防线。", parent=self)
            return
        if pw != pw2:
            messagebox.showwarning("不一致", "两次输入的口令不一致。", parent=self)
            return
        try:
            result = backupops.export_backup(store, Path(path), pw)
        except Exception as exc:
            messagebox.showerror("备份失败", str(exc), parent=self)
            return
        finally:
            self._bk_pw.set("")
            self._bk_pw2.set("")
        self._app.set_status(f"已备份 {result.count} 条", color=theme.SUCCESS)
        messagebox.showinfo(
            "备份完成", f"已加密导出 {result.count} 条到：\n{result.path}\n\n"
            "口令丢失无法恢复，请把口令记在别处。",
            parent=self,
        )

    def _do_restore(self) -> None:
        from pathlib import Path

        from kv.ops import backup as backupops

        store = self._app.store
        if store is None:
            return
        path, pw = self._rs_path.get().strip(), self._rs_pw.get()
        if not path or not pw:
            messagebox.showinfo("提示", "请选择备份文件并输入口令。", parent=self)
            return
        if not messagebox.askyesno(
            "确认恢复", "恢复会把备份中的记录合并进当前库（重名同值跳过）。继续？",
            parent=self,
        ):
            return
        try:
            result = backupops.restore_backup(store, Path(path), pw, actor="gui")
        except Exception as exc:
            messagebox.showerror("恢复失败", str(exc), parent=self)
            return
        finally:
            self._rs_pw.set("")
        self._app.set_status(
            f"恢复完成：新建 {result.created}，跳过 {result.deduped}", color=theme.SUCCESS,
        )
        messagebox.showinfo(
            "恢复完成", f"新建 {result.created} 条，重名同值跳过 {result.deduped} 条。",
            parent=self,
        )

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
