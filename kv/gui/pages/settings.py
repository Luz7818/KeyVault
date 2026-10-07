"""设置页：初始化 vault、健康检查、配置项、关于。"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import messagebox, ttk

from kv import __version__
from kv.gui import theme
from kv.gui.controls import Card, RoundButton, RoundEntry
from kv.gui.widgets import PageHeader


class SettingsPage(ttk.Frame):
    def __init__(self, parent: tk.Widget, *, app) -> None:
        super().__init__(parent, padding=theme.PAD_LG)
        self._app = app
        self._build()

    def _build(self) -> None:
        PageHeader(self, "设置", "初始化 vault、健康检查与配置项").pack(
            anchor="w", pady=(0, theme.GAP),
        )
        info_card = Card(self)
        info_card.pack(fill="both", expand=True, pady=(theme.GAP, 0))
        self._info = tk.Text(
            info_card.body, height=16, font=theme.FONT_MONO, state="disabled",
            bg=theme.SURFACE, wrap="word", padx=theme.PAD, pady=theme.PAD,
            fg=theme.TEXT, relief="flat", bd=0, highlightthickness=0,
        )
        self._info.pack(fill="both", expand=True, padx=1, pady=1)
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(theme.PAD, 0))
        RoundButton(bar, "初始化 Vault", self._do_init, kind="accent").pack(side="left", padx=(0, theme.GAP))
        RoundButton(bar, "健康检查", self._do_doctor).pack(side="left", padx=(0, theme.GAP))
        RoundButton(bar, "刷新", self._refresh).pack(side="left", padx=(0, theme.GAP))
        RoundButton(bar, "关于", self._show_about).pack(side="left")
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
            ttk.Label(self._settings_frame, text=key, style="Sub.TLabel").grid(
                row=i, column=0, sticky="w", pady=2,
            )
            var = tk.StringVar(value=store.get_setting(key, ""))
            self._setting_vars[key] = var
            RoundEntry(self._settings_frame, textvariable=var, width=180).grid(
                row=i, column=1, sticky="w", pady=2, padx=(theme.GAP, 0),
            )
        # 界面缩放：字型/间距整体放大的用户开关，重启生效
        row = len(keys)
        ttk.Label(self._settings_frame, text="界面缩放", style="Sub.TLabel").grid(
            row=row, column=0, sticky="w", pady=(theme.PAD_SM, 0),
        )
        self._ui_scale_var = tk.StringVar(value=store.get_setting("ui_scale", "1.0") or "1.0")
        cb = ttk.Combobox(
            self._settings_frame, textvariable=self._ui_scale_var, width=6,
            state="readonly", values=("1.0", "1.1", "1.25", "1.5"),
        )
        cb.grid(row=row, column=1, sticky="w", pady=(theme.PAD_SM, 0), padx=(theme.GAP, 0))
        ttk.Label(self._settings_frame, text="重启后生效", style="Muted.TLabel").grid(
            row=row, column=2, sticky="w", padx=(theme.GAP, 0),
        )
        ttk.Button(self._settings_frame, text="保存", command=self._save_settings).grid(
            row=row + 1, column=0, columnspan=2, sticky="w", pady=(theme.PAD, 0),
        )

    def _save_settings(self) -> None:
        store = self._app.store
        if store is None:
            return
        for key, var in self._setting_vars.items():
            store.set_setting(key, var.get())
        try:
            scale = float(self._ui_scale_var.get())
        except ValueError:
            scale = 1.0
        if 0.8 <= scale <= 1.6:
            store.set_setting("ui_scale", str(scale))
        self._app.set_status("配置已保存（界面缩放重启后生效）", color=theme.SUCCESS)

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
        from kv.paths import vault_db

        if self._app.initialize_vault():
            messagebox.showinfo("成功", f"vault 已创建：{vault_db()}", parent=self)
            self._app.refresh_store()
            self._refresh()

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
