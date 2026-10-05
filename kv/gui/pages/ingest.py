"""批量操作页：import/use/rotate/revoke。"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from kv.gui import theme
from kv.gui.controls import Card, RoundButton
from kv.gui.widgets import FormDialog, PageHeader


class IngestPage(ttk.Frame):
    def __init__(self, parent: tk.Widget, *, app) -> None:
        super().__init__(parent, padding=theme.PAD)
        self._app = app
        self._build()

    def _build(self) -> None:
        PageHeader(self, "批量操作", "从 .env / CSV 批量导入，注入或轮换").pack(
            anchor="w", pady=(0, theme.GAP),
        )
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, pady=(theme.GAP, 0))
        self._import_tab = ttk.Frame(nb, padding=theme.PAD)
        self._inject_tab = ttk.Frame(nb, padding=theme.PAD)
        self._rotate_tab = ttk.Frame(nb, padding=theme.PAD)
        nb.add(self._import_tab, text="导入")
        nb.add(self._inject_tab, text="注入 .env")
        nb.add(self._rotate_tab, text="轮换 / 吊销")
        self._build_import()
        self._build_inject()
        self._build_rotate()

    def _build_import(self) -> None:
        bar = ttk.Frame(self._import_tab)
        bar.pack(fill="x")
        self._import_path = tk.StringVar()
        ttk.Entry(bar, textvariable=self._import_path, width=40).pack(side="left", fill="x", expand=True)
        RoundButton(bar, "浏览", self._pick_import).pack(side="left", padx=theme.GAP)
        opts = ttk.Frame(self._import_tab)
        opts.pack(fill="x", pady=(theme.GAP, 0))
        self._import_tags = tk.StringVar()
        ttk.Label(opts, text="标签（逗号分隔）：", style="Sub.TLabel").pack(side="left")
        ttk.Entry(opts, textvariable=self._import_tags, width=20).pack(side="left", padx=theme.GAP)
        self._import_force = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="强制（跳过不可信判定）", variable=self._import_force).pack(side="left", padx=theme.PAD)
        RoundButton(self._import_tab, "导入", self._do_import, kind="accent").pack(pady=theme.PAD, anchor="w")
        result_card = Card(self._import_tab)
        result_card.pack(fill="both", expand=True)
        self._import_result = tk.Text(
            result_card.body, height=8, font=theme.FONT_MONO, state="disabled",
            bg=theme.SURFACE, fg=theme.TEXT,
        )
        self._import_result.pack(fill="both", expand=True, padx=1, pady=1)

    def _build_inject(self) -> None:
        form = ttk.Frame(self._inject_tab)
        form.pack(fill="x")
        ttk.Label(form, text="密钥名称：").grid(row=0, column=0, sticky="w", pady=4)
        self._inject_name = tk.StringVar()
        ttk.Entry(form, textvariable=self._inject_name, width=30).grid(row=0, column=1, sticky="w", padx=theme.GAP)
        ttk.Label(form, text="目标 .env：").grid(row=1, column=0, sticky="w", pady=4)
        inj_bar = ttk.Frame(form)
        inj_bar.grid(row=1, column=1, sticky="ew")
        self._inject_target = tk.StringVar()
        ttk.Entry(inj_bar, textvariable=self._inject_target, width=30).pack(side="left", fill="x", expand=True)
        ttk.Button(inj_bar, text="浏览", command=self._pick_inject_target).pack(side="left", padx=theme.GAP)
        self._inject_gitignore = tk.BooleanVar(value=True)
        ttk.Checkbutton(form, text="自动加 .gitignore", variable=self._inject_gitignore).grid(
            row=2, column=1, sticky="w", pady=4,
        )
        RoundButton(self._inject_tab, "注入", self._do_inject, kind="accent").pack(pady=theme.PAD, anchor="w")
        self._inject_result = ttk.Label(self._inject_tab, text="", font=theme.FONT_SMALL)
        self._inject_result.pack(anchor="w")

    def _build_rotate(self) -> None:
        form = ttk.Frame(self._rotate_tab)
        form.pack(fill="x")
        ttk.Label(form, text="密钥名称：").grid(row=0, column=0, sticky="w", pady=4)
        self._rotate_name = tk.StringVar()
        ttk.Entry(form, textvariable=self._rotate_name, width=30).grid(row=0, column=1, sticky="w", padx=theme.GAP)
        ttk.Label(form, text="新值：").grid(row=1, column=0, sticky="w", pady=4)
        self._rotate_value = tk.StringVar()
        ttk.Entry(form, textvariable=self._rotate_value, width=40, show="*").grid(
            row=1, column=1, sticky="w", padx=theme.GAP,
        )
        btn_bar = ttk.Frame(self._rotate_tab)
        btn_bar.pack(fill="x", pady=theme.PAD)
        RoundButton(btn_bar, "轮换", self._do_rotate, kind="accent").pack(side="left")
        RoundButton(btn_bar, "吊销", self._do_revoke, kind="danger").pack(side="left", padx=theme.GAP)
        self._rotate_result = ttk.Label(self._rotate_tab, text="", font=theme.FONT_SMALL)
        self._rotate_result.pack(anchor="w")

    def _pick_import(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="选择文件",
            filetypes=[("环境文件", "*.env .env"), ("CSV", "*.csv"), ("所有文件", "*.*")],
        )
        if path:
            self._import_path.set(path)

    def _pick_inject_target(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self, title="选择目标 .env", defaultextension=".env",
            filetypes=[("环境文件", "*.env .env"), ("所有文件", "*.*")],
        )
        if path:
            self._inject_target.set(path)

    def _do_import(self) -> None:
        from pathlib import Path

        from kv.ops.importer import import_file

        store = self._app.store
        if store is None:
            messagebox.showwarning("未初始化", "请先初始化 vault。", parent=self)
            return
        path_str = self._import_path.get().strip()
        if not path_str:
            messagebox.showinfo("提示", "请选择文件。", parent=self)
            return
        tags = tuple(t.strip() for t in self._import_tags.get().split(",") if t.strip())
        self._app.set_status("导入中…")
        self._app.run_async(
            import_file, Path(path_str), store,
            tags=tags, force=self._import_force.get(), actor="gui",
            on_done=self._show_import,
        )

    def _show_import(self, result) -> None:
        self._import_result.config(state="normal")
        self._import_result.delete("1.0", "end")
        self._import_result.insert("end", f"新建：{result.created}\n")
        self._import_result.insert("end", f"去重：{result.deduped}\n")
        self._import_result.insert("end", f"拒绝：{result.rejected}\n")
        if result.errors:
            self._import_result.insert("end", f"\n错误：\n")
            for e in result.errors[:10]:
                self._import_result.insert("end", f"  {e}\n")
        self._import_result.config(state="disabled")
        self._app.set_status(
            f"导入完成：{result.created} 新建，{result.deduped} 去重", color=theme.SUCCESS,
        )

    def _do_inject(self) -> None:
        from pathlib import Path

        from kv.ops.inject import inject

        store = self._app.store
        if store is None:
            return
        name = self._inject_name.get().strip()
        target = self._inject_target.get().strip()
        if not name or not target:
            messagebox.showinfo("提示", "请填写密钥名称和目标文件。", parent=self)
            return
        try:
            result = inject(
                store, name, Path(target),
                gitignore=self._inject_gitignore.get(), actor="gui",
            )
        except Exception as exc:
            messagebox.showerror("注入失败", str(exc), parent=self)
            return
        self._inject_result.config(
            text=f"已写入 {result.path}，键名 {result.key_name}，行 {result.line_no}（{result.action}）",
        )
        self._app.set_status("注入完成", color=theme.SUCCESS)

    def _do_rotate(self) -> None:
        from kv.ops.rotate import rotate

        store = self._app.store
        if store is None:
            return
        name = self._rotate_name.get().strip()
        value = self._rotate_value.get()
        if not name or not value:
            messagebox.showinfo("提示", "请填写名称和新值。", parent=self)
            return
        if not messagebox.askyesno("确认", f"轮换 {name}？旧值将标记为 revoked。", parent=self):
            return
        try:
            result = rotate(store, name, value.encode("utf-8"), actor="gui")
        except Exception as exc:
            messagebox.showerror("轮换失败", str(exc), parent=self)
            return
        self._rotate_result.config(text=f"已轮换：{result.name}（旧 {result.old_sha256[:8]} → 新 {result.new_sha256[:8]}）")
        self._app.set_status(f"轮换完成：{name}", color=theme.SUCCESS)

    def _do_revoke(self) -> None:
        from kv.ops.rotate import revoke

        store = self._app.store
        if store is None:
            return
        name = self._rotate_name.get().strip()
        if not name:
            messagebox.showinfo("提示", "请填写名称。", parent=self)
            return
        if not messagebox.askyesno("确认", f"吊销 {name}？", parent=self):
            return
        try:
            revoke(store, name, actor="gui")
        except Exception as exc:
            messagebox.showerror("吊销失败", str(exc), parent=self)
            return
        self._rotate_result.config(text=f"已吊销：{name}")
        self._app.set_status(f"吊销完成：{name}", color=theme.SUCCESS)
