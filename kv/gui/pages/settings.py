"""设置页：初始化 vault、健康检查、配置项、关于。"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import messagebox, ttk

from kv import __version__
from kv.gui import theme


class SettingsPage(ttk.Frame):
    def __init__(self, parent: tk.Widget, *, app) -> None:
        super().__init__(parent, padding=theme.PAD_LG)
        self._app = app
        self._build()

    def _build(self) -> None:
        ttk.Label(self, text="设置", font=theme.FONT_HEADING).pack(anchor="w")
        self._info = tk.Text(
            self, height=16, font=theme.FONT_MONO, state="disabled",
            bg=theme.TABLE_HEADER_BG, relief="solid", borderwidth=1,
            wrap="word", padx=theme.PAD, pady=theme.PAD,
        )
        self._info.pack(fill="both", expand=True, pady=(theme.PAD, 0))
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(theme.PAD, 0))
        ttk.Button(bar, text="初始化 Vault", command=self._do_init).pack(side="left", padx=(0, theme.GAP))
        ttk.Button(bar, text="健康检查", command=self._do_doctor).pack(side="left", padx=(0, theme.GAP))
        ttk.Button(bar, text="刷新", command=self._refresh).pack(side="left", padx=(0, theme.GAP))
        ttk.Button(bar, text="关于", command=self._show_about).pack(side="left")
        self._settings_frame = ttk.LabelFrame(self, text="配置项", padding=theme.PAD)
        self._settings_frame.pack(fill="x", pady=(theme.PAD, 0))
        self._setting_vars: dict[str, tk.StringVar] = {}
        self._build_settings()

    def _build_settings(self) -> None:
        for w in self._settings_frame.winfo_children():
            w.destroy()
        self._setting_vars.clear()
        store = self._app.store
        if store is None:
            ttk.Label(self._settings_frame, text="vault 未初始化").pack()
            return
        keys = ["mask_char"]
        for i, key in enumerate(keys):
            ttk.Label(self._settings_frame, text=key).grid(row=i, column=0, sticky="w", pady=2)
            var = tk.StringVar(value=store.get_setting(key, ""))
            self._setting_vars[key] = var
            ttk.Entry(self._settings_frame, textvariable=var, width=20).grid(
                row=i, column=1, sticky="w", pady=2, padx=(theme.GAP, 0),
            )
        ttk.Button(self._settings_frame, text="保存", command=self._save_settings).grid(
            row=len(keys), column=0, columnspan=2, sticky="w", pady=(theme.PAD, 0),
        )

    def _save_settings(self) -> None:
        store = self._app.store
        if store is None:
            return
        for key, var in self._setting_vars.items():
            store.set_setting(key, var.get())
        self._app.set_status("配置已保存", color=theme.SUCCESS)

    def on_show(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        store = self._app.store
        self._info.config(state="normal")
        self._info.delete("1.0", "end")
        if store is None:
            self._info.insert("end", "vault 未初始化。\n点击「初始化 Vault」创建。")
        else:
            info = store.doctor()
            for k, v in info.items():
                self._info.insert("end", f"{k}: {v}\n")
        self._info.config(state="disabled")
        self._build_settings()

    def _do_init(self) -> None:
        from kv.core import db
        from kv.crypto.dpapi import DpapiProtector
        from kv.paths import vault_db

        target = vault_db()
        if db.is_initialized(target):
            messagebox.showinfo("已初始化", f"vault 已存在：{target}", parent=self)
            self._app.refresh_store()
            self._refresh()
            return
        protector = DpapiProtector()
        canary_text = "kv-binding-canary"
        canary_blob = protector.protect(canary_text.encode("utf-8"))
        try:
            db.initialize(target, canary_blob=canary_blob, canary_text=canary_text)
        except Exception as exc:
            messagebox.showerror("初始化失败", str(exc), parent=self)
            return
        self._app.refresh_store()
        self._refresh()
        self._app.set_status(f"vault 已创建：{target}", color=theme.SUCCESS)
        messagebox.showinfo("成功", f"vault 已创建：{target}", parent=self)

    def _do_doctor(self) -> None:
        store = self._app.store
        if store is None:
            messagebox.showwarning("未初始化", "vault 未初始化，请先初始化。", parent=self)
            return
        self._app.set_status("正在检查…")
        self._app.run_async(store.doctor, on_done=self._show_doctor)

    def _show_doctor(self, info: dict) -> None:
        self._info.config(state="normal")
        self._info.delete("1.0", "end")
        for k, v in info.items():
            self._info.insert("end", f"{k}: {v}\n")
        self._info.config(state="disabled")
        ok = info.get("binding_ok", False)
        self._app.set_status(
            "健康检查完成" if ok else "健康检查发现问题",
            color=theme.SUCCESS if ok else theme.WARNING,
        )

    def _show_about(self) -> None:
        from kv.paths import vault_db

        text = (
            f"KeyVault v{__version__}\n\n"
            f"Python {sys.version.split()[0]}\n"
            f"Vault: {vault_db()}\n\n"
            "本地优先的密钥保管工具。\n"
            "Windows DPAPI 加密，SQLite 存储。"
        )
        messagebox.showinfo("关于", text, parent=self)
