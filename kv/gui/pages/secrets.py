"""密钥管理页：list/add/show/reveal/rm/copy/rotate/revoke/rename/tag。"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from kv.gui import theme
from kv.gui.controls import Card, RoundButton, RoundEntry
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
        card = Card(self)
        card.pack(fill="both", expand=True, pady=(theme.GAP, 0))
        self._table = DataTable(
            card.body, COLUMNS, headings=HEADINGS, widths=WIDTHS,
            on_double=lambda r: self._on_show(r["name"]),
            on_right_click=self._on_right_click,
        )
        for status, color in (
            ("active", theme.SUCCESS), ("revoked", theme.ERROR), ("expired", theme.WARNING),
        ):
            self._table.tree.tag_configure(f"st-{status}", foreground=color)
        self._table.pack(fill="both", expand=True, padx=1, pady=1)
        self._build_menu()

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
        self._table.clear()
        if store is None:
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
        for row in self._rows:
            created = (row.created_at or "")[:16].replace("T", " ")
            self._table.insert_row((
                row.name, row.platform, row.status,
                row.preview("*"), created,
            ), tags=(f"st-{row.status}",))
        self._app.set_status(f"{len(self._rows)} 条记录")

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
        win = tk.Toplevel(self)
        win.title(f"详情 — {row.name}")
        win.geometry("520x480")
        win.transient(self)
        txt = tk.Text(
            win, font=theme.FONT_MONO, wrap="word", padx=12, pady=12,
            bg=theme.SURFACE, fg=theme.TEXT,
        )
        theme.flat(txt)
        txt.pack(fill="both", expand=True, padx=theme.PAD, pady=(theme.PAD, 0))
        _fill_detail(txt, row, fps)
        txt.config(state="disabled")
        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=8)

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
        from kv.capture.clipboard import Win32Clipboard
        from kv.capture.wipe import copy_secret

        store = self._app.store
        if store is None:
            return
        try:
            result = copy_secret(store, name, Win32Clipboard(), ttl=30, actor="gui")
        except Exception as exc:
            messagebox.showerror("复制失败", str(exc), parent=self)
            return
        self._app.set_status(
            f"已复制 {result.name}，{result.ttl:.0f} 秒后自动擦除", color=theme.SUCCESS,
        )

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


def _fill_detail(txt: tk.Text, row, fps: list) -> None:
    fields = [
        ("名称", row.name), ("平台", row.platform), ("状态", row.status),
        ("置信度", row.confidence), ("证据", row.evidence),
        ("sha256", row.sha256), ("值长度", str(row.value_len)),
        ("预览", row.preview("*")), ("键名", row.key_name or ""),
        ("种类", row.kind), ("备注", row.note), ("来源", row.origin),
        ("创建", row.created_at or ""), ("更新", row.updated_at or ""),
        ("过期", row.expires_at or ""), ("最后使用", row.last_used_at or ""),
        ("标签", ", ".join(row.tags)), ("别名", ", ".join(row.aliases)),
    ]
    for label, value in fields:
        txt.insert("end", f"{label}: {value}\n")
    txt.insert("end", f"\n指纹历史（{len(fps)} 条）:\n")
    for fp in fps:
        txt.insert("end", f"  {fp['sha256'][:12]}  {fp['status']}  {fp['first_seen_at']}\n")
