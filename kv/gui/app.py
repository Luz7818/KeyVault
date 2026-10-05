"""主窗口：侧边栏导航 + 内容区页面切换。"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from kv.gui import theme
from kv.gui.widgets import StatusLabel

NAV_ITEMS: list[tuple[str, str]] = [
    ("secrets", "密钥管理"),
    ("detect", "检测"),
    ("capture", "捕获"),
    ("ingest", "批量操作"),
    ("security", "安全"),
    ("settings", "设置"),
]


class App(tk.Tk):
    """kv GUI 主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self.title("KeyVault")
        self.geometry("960x640")
        self.minsize(800, 560)
        self.configure(bg=theme.BG)
        self._queue: queue.Queue[tuple[Callable, tuple, dict]] = queue.Queue()
        self._pages: dict[str, tk.Widget] = {}
        self._nav_buttons: dict[str, tk.Button] = {}
        self._current: str = ""
        self._build_layout()
        self._register_pages()
        self.show_page("secrets")
        self.after(50, self._drain_queue)

    def _build_layout(self) -> None:
        self._sidebar = tk.Frame(self, bg=theme.SIDEBAR_BG, width=theme.SIDEBAR_WIDTH)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)
        title = tk.Label(
            self._sidebar, text="KeyVault", font=theme.FONT_HEADING,
            bg=theme.SIDEBAR_BG, fg="#ffffff", pady=theme.PAD_LG,
        )
        title.pack(fill="x")
        for key, label in NAV_ITEMS:
            btn = tk.Button(
                self._sidebar, text=label, anchor="w",
                font=theme.FONT, bg=theme.SIDEBAR_BG, fg=theme.SIDEBAR_FG,
                activebackground=theme.SIDEBAR_ACTIVE_BG,
                activeforeground=theme.SIDEBAR_ACTIVE_FG,
                relief="flat", padx=theme.PAD_LG, pady=theme.PAD,
                cursor="hand2", command=lambda k=key: self.show_page(k),
            )
            btn.pack(fill="x")
            self._nav_buttons[key] = btn
        self._content = tk.Frame(self, bg=theme.CONTENT_BG)
        self._content.pack(side="left", fill="both", expand=True)
        self._status = StatusLabel(self)

    def _register_pages(self) -> None:
        from kv.gui.pages.capture import CapturePage
        from kv.gui.pages.detect import DetectPage
        from kv.gui.pages.ingest import IngestPage
        from kv.gui.pages.secrets import SecretsPage
        from kv.gui.pages.security import SecurityPage
        from kv.gui.pages.settings import SettingsPage

        registry: dict[str, type] = {
            "secrets": SecretsPage,
            "detect": DetectPage,
            "capture": CapturePage,
            "ingest": IngestPage,
            "security": SecurityPage,
            "settings": SettingsPage,
        }
        for key, cls in registry.items():
            page = cls(self._content, app=self)
            page.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._pages[key] = page

    def show_page(self, key: str) -> None:
        if key == self._current:
            return
        for k, btn in self._nav_buttons.items():
            active = k == key
            btn.config(
                bg=theme.SIDEBAR_ACTIVE_BG if active else theme.SIDEBAR_BG,
                fg=theme.SIDEBAR_ACTIVE_FG if active else theme.SIDEBAR_FG,
            )
        for k, page in self._pages.items():
            if k == key:
                page.tkraise()
                if hasattr(page, "on_show"):
                    page.on_show()
        self._current = key

    def set_status(self, text: str, *, color: str = "") -> None:
        self._status.set(text, color=color)

    def run_async(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        **kwargs: Any,
    ) -> None:
        """在后台线程跑 fn，结果通过 after() 回主线程。"""
        def worker() -> None:
            try:
                result = fn(*args, **kwargs)
                self._queue.put((on_done or (lambda _: None), (result,), {}))
            except Exception as exc:
                handler = on_error or self._default_error
                self._queue.put((handler, (exc,), {}))
        threading.Thread(target=worker, daemon=True).start()

    def _drain_queue(self) -> None:
        while not self._queue.empty():
            fn, args, kwargs = self._queue.get_nowait()
            fn(*args, **kwargs)
        self.after(50, self._drain_queue)

    def _default_error(self, exc: Exception) -> None:
        from kv import errors
        if isinstance(exc, errors.KvError):
            msg = str(exc)
            if exc.hint:
                msg += f"\n\n{exc.hint}"
            tk.messagebox.showerror("kv 错误", msg, parent=self)
        else:
            tk.messagebox.showerror("未处理错误", str(exc), parent=self)
        self.set_status(f"错误：{exc}", color=theme.ERROR)

    @property
    def store(self):
        """懒加载 VaultStore。未初始化时返回 None。"""
        if not hasattr(self, "_store"):
            self._store = self._try_open_store()
        return self._store

    def _try_open_store(self):
        from kv.core.vault import VaultStore
        from kv.paths import vault_db
        try:
            s = VaultStore(vault_db())
            s.require_initialized()
            s.verify_binding()
            return s
        except Exception:
            return None

    def refresh_store(self) -> None:
        self._store = self._try_open_store()
