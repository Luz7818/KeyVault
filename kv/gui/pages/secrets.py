"""密钥管理页：list/add/show/reveal/rm/copy/rotate/revoke/rename/tag。"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from kv.gui import theme
from kv.gui.controls import Card, RoundButton, RoundEntry
from kv.gui.detail_window import build_detail_window
from kv.gui.widgets import DataTable, FormDialog, PageHeader

COLUMNS = ("name", "platform", "status", "preview", "created_at")
HEADINGS = ("名称", "平台", "状态", "预览", "创建时间")
WIDTHS = (155, 120, 80, 205, 165)


class SecretsPage(ttk.Frame):
    def __init__(self, parent: tk.Widget, *, app) -> None:
        super().__init__(parent, padding=theme.PAD)
        self._app = app
        self._rows: list[Any] = []
        self._build()

    def _build(self) -> None:
        self._header = PageHeader(self, "密钥管理", "存取、轮换、审计——所有数据不出本机")
        self._header.pack(anchor="w", pady=(0, theme.GAP))
        self._build_toolbar()
        self._build_empty_state()
        card = Card(self)
        card.pack(fill="both", expand=True, pady=(theme.GAP, 0))
        self._table = DataTable(
            card.body, COLUMNS, headings=HEADINGS, widths=WIDTHS,
            on_double=lambda r: self._on_show(r["name"]),
            on_right_click=self._on_right_click,
        )
        for status, color in (
            ("active", theme.SUCCESS), ("revoked", theme.ERROR),
            ("expired", theme.WARNING), ("expiring", "#b45309"),
        ):
            self._table.tree.tag_configure(f"st-{status}", foreground=color)
        self._table.pack(fill="both", expand=True, padx=1, pady=1)
        self._build_menu()

    def _build_empty_state(self) -> None:
        """空态引导卡：未初始化 → 一键建库；空库 → 添加或去捕获。"""
        self._empty = Card(self)
        body = self._empty.body
        self._empty_title = tk.Label(
            body, text="", font=theme.FONT_HEADING, bg=theme.SURFACE, fg=theme.TEXT,
        )
        self._empty_title.pack(anchor="w", padx=theme.PAD, pady=(theme.PAD_LG, 0))
        self._empty_hint = tk.Label(
            body, text="", font=theme.FONT, bg=theme.SURFACE,
            fg=theme.TEXT_SECONDARY, justify="left",
        )
        self._empty_hint.pack(anchor="w", padx=theme.PAD, pady=(theme.PAD_SM, 0))
        row = tk.Frame(body, bg=theme.SURFACE)
        row.pack(anchor="w", padx=theme.PAD, pady=theme.PAD)
        self._empty_btn1 = RoundButton(row, "", self._on_add, kind="accent")
        self._empty_btn1.pack(side="left")
        self._empty_btn2 = RoundButton(row, "", lambda: self._app.show_page("capture"))
        self._empty_btn2.pack(side="left", padx=(theme.GAP, 0))
        self._empty_btn3 = RoundButton(row, "", lambda: self._app.show_page("settings"))
        self._empty_btn3.pack(side="left", padx=(theme.GAP, 0))

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self.refresh())
        RoundEntry(bar, textvariable=self._search, width=260).pack(side="left")
        ttk.Label(bar, text="搜索", style="Sub.TLabel").pack(side="left", padx=(8, theme.PAD))
        self._status_filter = tk.StringVar(value="")
        cb = ttk.Combobox(
            bar, textvariable=self._status_filter, width=8, state="readonly",
            values=("", "active", "revoked", "expired"),
        )
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda _: self.refresh())
        ttk.Label(bar, text="状态", style="Sub.TLabel").pack(side="left", padx=(8, theme.PAD))
        RoundButton(bar, "添加", self._on_add, kind="accent").pack(side="right")
        RoundButton(bar, "刷新", self.refresh).pack(side="right", padx=(0, theme.GAP))

    def _build_menu(self) -> None:
        self._menu = tk.Menu(self, tearoff=0)
        for label, cmd in (
            ("查看详情", lambda: self._on_show(self._sel_name())),
            ("显示明文", lambda: self._on_reveal(self._sel_name())),
            ("复制到剪贴板", lambda: self._on_copy(self._sel_name())),
            ("轮换", lambda: self._on_rotate(self._sel_name())),
            ("吊销", lambda: self._on_revoke(self._sel_name())),
            ("重命名", lambda: self._on_rename(self._sel_name())),
            ("标签", lambda: self._on_tag(self._sel_name())),
            ("删除", lambda: self._on_delete(self._sel_name())),
        ):
            self._menu.add_command(label=label, command=cmd)

    def _on_right_click(self, event: tk.Event, _row: dict) -> None:
        self._menu.tk_popup(event.x_root, event.y_root)

    def _sel_name(self) -> str:
        row = self._table.selected_row()
        return row["name"] if row else ""

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        store = self._app.store
        if store is None:
            self._show_empty("uninitialized")
            self._app.set_status("vault 未初始化", color=theme.WARNING)
            return
        query = self._search.get().strip()
        status = self._status_filter.get()
        filters: dict[str, Any] = {}
        if query:
            filters["query"] = query
        if status:
            filters["status"] = status
        self._rows = store.list_secrets(**filters)
        if not self._rows and not query and not status:
            self._show_empty("empty")
            self._app.set_status("0 条记录")
            return
        self._show_table()
        from kv.ops.health import health_report

        expiring = set(health_report(store).expiring)
        for row in self._rows:
            created = (row.created_at or "")[:16].replace("T", " ")
            if row.status == "expired":
                tag = "st-expired"
            elif row.status == "active" and row.name in expiring:
                tag = "st-expiring"   # 30 天内到期：整行橙色提醒
            else:
                tag = f"st-{row.status}"
            self._table.insert_row((
                row.name, row.platform, row.status,
                row.preview("*"), created,
            ), tags=(tag,))
        self._app.set_status(f"{len(self._rows)} 条记录")

    def _show_empty(self, mode: str) -> None:
        """空态引导：mode = uninitialized / empty。"""
        texts = {
            "uninitialized": (
                "还没有创建密钥库",
                "密钥库用 Windows DPAPI 加密，只属于当前 Windows 账户，数据不出本机。",
                "初始化 Vault", "看看设置页", "了解捕获",
            ),
            "empty": (
                "库里还没有密钥",
                "手动添加一条，或开启剪贴板监控——复制到密钥时会自动识别并进入审批队列。",
                "添加密钥", "去设置页", "开启捕获",
            ),
        }
        title, hint, b1, b2, b3 = texts[mode]
        self._empty_title.config(text=title)
        self._empty_hint.config(text=hint)
        self._empty_btn1.set_text(b1)
        self._empty_btn2.set_text(b2)
        self._empty_btn3.set_text(b3)
        if mode == "uninitialized":
            self._empty_btn1.set_command(self._app.initialize_vault)
            self._empty_btn2.set_command(lambda: self._app.show_page("settings"))
            self._empty_btn3.set_command(self._go_capture)
        else:
            self._empty_btn1.set_command(self._on_add)
            self._empty_btn2.set_command(lambda: self._app.show_page("settings"))
            self._empty_btn3.set_command(self._go_capture)
        self._empty.pack(fill="both", expand=True, pady=(theme.GAP, 0))
        # 隐藏表格所在的父容器（Card）：pack_forget 只对顶层 pack 生效
        self._table.master.master.pack_forget()

    def _go_capture(self) -> None:
        self._app.show_page("capture")

    def _show_table(self) -> None:
        if not self._table.master.master.winfo_manager():
            self._table.master.master.pack(fill="both", expand=True, pady=(theme.GAP, 0))
        self._empty.pack_forget()

    def _on_add(self) -> None:
        fields = [
            ("名称", "name", "text"), ("值", "value", "password"),
            ("平台（可选）", "platform", "text"), ("标签（逗号分隔）", "tags", "text"),
            ("备注", "note", "text"), ("过期时间", "expires_at", "text"),
        ]
        dlg = FormDialog(self, "添加密钥", fields, on_submit=self._do_add)
        self.wait_window(dlg)
        if dlg.result:
            self.refresh()

    def _do_add(self, data: dict[str, str]) -> bool:
        from kv.core import masking
        from kv.detect import resolve
        from kv.detect.corrections import CorrectionSet
        from kv.model import Candidate, Verdict
        from kv.ops import save as saveops
        from kv.parse import pipeline

        store = self._app.store
        if store is None:
            messagebox.showwarning("未初始化", "请先在设置页初始化 vault。", parent=self)
            return False
        value = data["value"].encode("utf-8")
        if not value.strip():
            messagebox.showwarning("值为空", "密钥值不能为空。", parent=self)
            return False
        name = data["name"].strip()
        platform = data["platform"].strip()
        tags = tuple(t.strip() for t in data["tags"].split(",") if t.strip())
        if platform:
            cand = Candidate(value=value, kind=masking.KIND_TOKEN, source="gui", span="gui add")
            verdict = Verdict(
                platform=platform, confidence="manual", source="correction",
                evidence=f"gui add --platform {platform}",
                mask_style=masking.infer_mask_style(value), kind=masking.KIND_TOKEN,
            )
        else:
            text = value.decode("utf-8", "replace")
            result = pipeline.parse(text, source_hint="gui")
            if len(result.candidates) == 1:
                cand = result.candidates[0]
                verdict = resolve.resolve(cand, CorrectionSet.empty())
            else:
                cand = Candidate(value=value, kind=masking.KIND_TOKEN, source="gui", span="gui add")
                verdict = Verdict(
                    platform="unknown", confidence="none", source="none",
                    evidence="没有任何规则匹配", mask_style=masking.infer_mask_style(value),
                    kind="token",
                )
        policy = saveops.SavePolicy(
            name=name, tags=tags, note=data["note"].strip(),
            expires_at=data["expires_at"].strip() or None,
            origin="gui", actor="gui", force=True,
        )
        try:
            res = saveops.commit(cand, verdict, store=store, policy=policy)
        except Exception as exc:
            messagebox.showerror("添加失败", str(exc), parent=self)
            return False
        self._app.set_status(f"已添加：{res.name}（{res.outcome}）", color=theme.SUCCESS)
        return True

    def _on_show(self, name: str) -> None:
        if not name:
            return
        store = self._app.store
        if store is None:
            return
        row = store.get(name)
        fps = store.fingerprints_of(row.id)
        history = store.list_audit(secret_id=row.id, limit=15)
        build_detail_window(row, fps, history)

    def _on_reveal(self, name: str) -> None:
        if not name:
            return
        if not messagebox.askyesno("确认", f"显示 {name} 的明文值？", parent=self):
            return
        store = self._app.store
        if store is None:
            return
        try:
            with store.reveal(name, actor="gui") as buf:
                text = buf.decode("utf-8", "replace")
        except Exception as exc:
            messagebox.showerror("失败", str(exc), parent=self)
            return
        win = tk.Toplevel(self)
        win.title(f"明文 — {name}")
        win.geometry("480x120")
        win.transient(self)
        entry = ttk.Entry(win, font=theme.FONT_MONO, width=60)
        entry.pack(padx=12, pady=12, fill="x")
        entry.insert(0, text)
        entry.select_range(0, "end")
        del text
        ttk.Button(win, text="关闭", command=win.destroy).pack()
        win.protocol("WM_DELETE_WINDOW", win.destroy)

    def _on_copy(self, name: str) -> None:
        if not name:
            return
        self._app.copy_secret_by_name(name)

    def _on_rotate(self, name: str) -> None:
        if not name:
            return
        fields = [("新值", "value", "password")]
        dlg = FormDialog(self, f"轮换 — {name}", fields)
        self.wait_window(dlg)
        if not dlg.result:
            return
        from kv.ops import rotate as rotateops

        store = self._app.store
        if store is None:
            return
        try:
            res = rotateops.rotate(store, name, dlg.result["value"].encode("utf-8"))
        except Exception as exc:
            messagebox.showerror("轮换失败", str(exc), parent=self)
            return
        self._app.set_status(f"已轮换：{res.name}", color=theme.SUCCESS)
        self.refresh()

    def _on_revoke(self, name: str) -> None:
        if not name:
            return
        if not messagebox.askyesno("确认", f"吊销 {name}？", parent=self):
            return
        from kv.ops import rotate as rotateops

        store = self._app.store
        if store is None:
            return
        try:
            rotateops.revoke(store, name)
        except Exception as exc:
            messagebox.showerror("吊销失败", str(exc), parent=self)
            return
        self._app.set_status(f"已吊销：{name}", color=theme.SUCCESS)
        self.refresh()

    def _on_rename(self, name: str) -> None:
        if not name:
            return
        fields = [("新名称", "new_name", "text")]
        dlg = FormDialog(self, f"重命名 — {name}", fields, initial={"new_name": name})
        self.wait_window(dlg)
        if not dlg.result:
            return
        store = self._app.store
        if store is None:
            return
        try:
            store.rename(name, dlg.result["new_name"])
        except Exception as exc:
            messagebox.showerror("重命名失败", str(exc), parent=self)
            return
        self._app.set_status(f"已重命名：{name} → {dlg.result['new_name']}", color=theme.SUCCESS)
        self.refresh()

    def _on_tag(self, name: str) -> None:
        if not name:
            return
        store = self._app.store
        if store is None:
            return
        row = store.get(name)
        fields = [("标签（逗号分隔）", "tags", "text")]
        dlg = FormDialog(self, f"标签 — {name}", fields, initial={"tags": ", ".join(row.tags)})
        self.wait_window(dlg)
        if not dlg.result:
            return
        new_tags = {t.strip() for t in dlg.result["tags"].split(",") if t.strip()}
        old_tags = set(row.tags)
        try:
            with store.session() as conn:
                for t in new_tags - old_tags:
                    store.add_tag(conn, row.id, t)
                for t in old_tags - new_tags:
                    store.remove_tag(conn, row.id, t)
        except Exception as exc:
            messagebox.showerror("标签失败", str(exc), parent=self)
            return
        self._app.set_status(f"已更新标签：{name}", color=theme.SUCCESS)
        self.refresh()

    def _on_delete(self, name: str) -> None:
        if not name:
            return
        if not messagebox.askyesno("确认删除", f"删除 {name}？审计记录会保留。", parent=self):
            return
        store = self._app.store
        if store is None:
            return
        try:
            store.delete(name)
        except Exception as exc:
            messagebox.showerror("删除失败", str(exc), parent=self)
            return
        self._app.set_status(f"已删除：{name}", color=theme.SUCCESS)
        self.refresh()
