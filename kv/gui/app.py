"""主窗口：侧边栏导航 + 内容区页面切换。

侧边栏整体在 sidebar.py（一整块渐变 Canvas）；这里只管布局、页面注册
与全局快捷键。
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from kv.gui import motion, theme
from kv.gui.icon import apply as apply_app_icon
from kv.gui.sidebar import Sidebar
from kv.gui.widgets import StatusLabel


class App(tk.Tk):
    """kv GUI 主窗口。"""

    def __init__(self, *, scale: float = 1.0, ui_scale: float = 1.0) -> None:
        super().__init__()
        theme.init(scale)
        theme.setup_style()
        self._ui_scale = max(0.8, min(ui_scale, 1.6))
        total = scale * self._ui_scale
        self.title("KeyVault")
        apply_app_icon(self)
        # 缩放档位再大也不能超出屏幕——窗口默认取「理想尺寸」与屏幕 90% 的较小值
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        want_w, want_h = round(1180 * total), round(780 * total)
        self.geometry(
            f"{min(want_w, round(screen_w * 0.95))}x{min(want_h, round(screen_h * 0.88))}"
        )
        self.minsize(
            min(round(1000 * total), round(screen_w * 0.95)),
            min(round(660 * total), round(screen_h * 0.88)),
        )
        self.configure(bg=theme.CANVAS)
        self._queue: queue.Queue[tuple[Callable, tuple, dict]] = queue.Queue()
        self._pages: dict[str, tk.Widget] = {}
        self._page_slides: dict[str, motion.Spring] = {}
        self._current: str = ""
        self._hover_key: str = ""
        self._row_geo: dict[str, tuple[int, int]] = {}   # key -> (y, h)
        self._build_layout()
        self._register_pages()
        self.show_page("secrets")
        theme.set_zoom(self._ui_scale)  # 持久的用户缩放落到命名字体上
        self.bind_all("<Control-MouseWheel>", self._on_zoom_wheel)
        self.bind_all("<Control-equal>", lambda e: self._zoom_by(0.05))
        self.bind_all("<Control-minus>", lambda e: self._zoom_by(-0.05))
        self.bind_all("<Control-k>", lambda e: self._open_palette())
        self.bind_all("<Control-f>", self._focus_search)
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.after(50, self._drain_queue)

    def _on_wheel(self, event) -> str:
        """滚轮委派：指针所在的页可滚则滚它。

        页面全部 place 堆叠，各页自己 bind_all 会串页——所以只有这里绑。
        Treeview/Text/Listbox 有自己的类绑定滚动，不抢；Ctrl 按下时让位
        给 Ctrl+滚轮缩放。
        """
        if event.state & 0x0004:
            return ""
        w = self.winfo_containing(event.x_root, event.y_root)
        while w is not None and w is not self:
            if w.winfo_class() in ("Treeview", "Text", "Listbox"):
                return ""  # 内建滚动控件自己处理
            if w is self._sidebar:
                self._sidebar.wheel_scroll(-(event.delta // 120))
                return "break"
            if any(w is p for p in self._pages.values()):
                if hasattr(w, "wheel_scroll"):
                    w.wheel_scroll(-(event.delta // 120))
                    return "break"
                return ""
            w = w.master
        return ""

    def _open_palette(self) -> None:
        from kv.gui.palette import open_palette

        open_palette(self)

    def _focus_search(self, _event=None) -> str:
        """Ctrl+F 交给当前页；页面没有搜索框（无 focus_search）就不接。"""
        page = self._pages.get(self._current)
        if page is not None and hasattr(page, "focus_search"):
            page.focus_search()
            return "break"
        return ""

    def copy_secret_by_name(self, name: str) -> bool:
        """复制一条密钥到剪贴板（命令面板与密钥页共用）。"""
        from kv.capture.clipboard import Win32Clipboard
        from kv.capture.wipe import copy_secret

        store = self.store
        if store is None:
            return False
        try:
            result = copy_secret(store, name, Win32Clipboard(), ttl=30, actor="gui")
        except Exception as exc:
            self.set_status(f"复制失败：{exc}", color=theme.ERROR)
            return False
        self.set_status(
            f"已复制 {result.name}，{result.ttl:.0f} 秒后自动擦除", color=theme.SUCCESS,
        )
        return True

    def _on_zoom_wheel(self, event: tk.Event) -> None:
        self._zoom_by(0.05 if event.delta > 0 else -0.05)

    def _zoom_by(self, delta: float) -> None:
        zoom = theme.set_zoom(theme.ZOOM + delta)
        store = self.store
        if store is not None:
            try:
                store.set_setting("ui_scale", str(zoom))
            except Exception:
                pass  # 缩放不因写库失败而中断
        self.set_status(f"界面缩放 {round(zoom * 100)}%（Ctrl+滚轮调整，自动保存）")

    # ------------------------------------------------------------ 布局

    def _build_layout(self) -> None:
        # 状态栏必须先 pack——后 pack 的 expand 内容区会把它挤出窗口
        self._status = StatusLabel(self)
        self._sidebar = Sidebar(self, on_navigate=self.show_page)
        self._sidebar.pack(side="left", fill="y")
        self._content = tk.Frame(self, bg=theme.CANVAS)
        self._content.pack(side="left", fill="both", expand=True)

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
        """切页 = 侧边栏高亮 + 内容区抬页 + 轻滑入。"""
        if key == self._current:
            return
        self._current = key
        self._sidebar.set_current(key)
        for k, page in self._pages.items():
            if k == key:
                page.tkraise()
                if hasattr(page, "on_show"):
                    page.on_show()
                self._slide_in(page, k)

    def _slide_in(self, page: tk.Widget, key: str) -> None:
        """新页面从右侧 18px 轻滑入（临界阻尼弹簧）。首次切换无动画。"""
        spring = self._page_slides.get(key)
        if spring is None:
            spring = motion.Spring(self, lambda x: page.place_configure(x=round(x)))
            self._page_slides[key] = spring
            spring.jump(0)
            return
        page.place_configure(x=18)
        spring.jump(18)
        spring.to(0)

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
        try:
            while not self._queue.empty():
                fn, args, kwargs = self._queue.get_nowait()
                fn(*args, **kwargs)
            self.after(50, self._drain_queue)
        except tk.TclError:
            pass  # 窗口已销毁，排队回调随窗口终止

    def _default_error(self, exc: Exception) -> None:
        from tkinter import messagebox

        from kv import errors
        if isinstance(exc, errors.KvError):
            msg = str(exc)
            if exc.hint:
                msg += f"\n\n{exc.hint}"
            messagebox.showerror("kv 错误", msg, parent=self)
        else:
            messagebox.showerror("未处理错误", str(exc), parent=self)
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

    def initialize_vault(self) -> bool:
        """公共初始化流程（设置页与空态引导共用）：建库 + 刷新。失败弹窗。"""
        from tkinter import messagebox

        from kv.core import db
        from kv.crypto.dpapi import DpapiProtector
        from kv.paths import vault_db

        target = vault_db()
        if db.is_initialized(target):
            self.refresh_store()
            return True
        try:
            protector = DpapiProtector()
            db.initialize(
                target,
                canary_blob=protector.protect(b"kv-binding-canary"),
                canary_text="kv-binding-canary",
            )
        except Exception as exc:
            messagebox.showerror("初始化失败", str(exc), parent=self)
            return False
        self.refresh_store()
        self.set_status(f"vault 已创建：{target}", color=theme.SUCCESS)
        return True
