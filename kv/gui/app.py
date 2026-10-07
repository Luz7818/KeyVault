"""主窗口：侧边栏导航 + 内容区页面切换。"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from kv.gui import icons, motion, theme
from kv.gui.widgets import StatusLabel

NAV_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    ("密钥", [("secrets", "密钥管理")]),
    ("工具", [("detect", "检测"), ("capture", "捕获"), ("ingest", "批量操作")]),
    ("系统", [("security", "安全"), ("settings", "设置")]),
]


class App(tk.Tk):
    """kv GUI 主窗口。"""

    def __init__(self, *, scale: float = 1.0, ui_scale: float = 1.0) -> None:
        super().__init__()
        theme.init(scale)
        theme.setup_style()
        self._ui_scale = max(0.8, min(ui_scale, 1.6))
        total = scale * self._ui_scale
        self.title("KeyVault")
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
        self.configure(bg=theme.BG)
        self._queue: queue.Queue[tuple[Callable, tuple, dict]] = queue.Queue()
        self._pages: dict[str, tk.Widget] = {}
        self._nav_buttons: dict[str, tk.Button] = {}
        self._nav_icons: dict[str, tuple[tk.Canvas, int]] = {}
        self._page_slides: dict[str, motion.Spring] = {}
        self._current: str = ""
        self._build_layout()
        self._register_pages()
        self.show_page("secrets")
        theme.set_zoom(self._ui_scale)  # 持久的用户缩放落到命名字体上
        self.bind_all("<Control-MouseWheel>", self._on_zoom_wheel)
        self.bind_all("<Control-equal>", lambda e: self._zoom_by(0.05))
        self.bind_all("<Control-minus>", lambda e: self._zoom_by(-0.05))
        self.after(50, self._drain_queue)

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

    def _build_layout(self) -> None:
        # 状态栏必须先 pack——后 pack 的 expand 内容区会把它挤出窗口
        self._status = StatusLabel(self)
        self._sidebar = tk.Frame(self, bg=theme.SIDEBAR_BG, width=theme.SIDEBAR_WIDTH)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)
        self._build_brand()
        tk.Frame(self._sidebar, bg="#232a38", height=1).pack(fill="x", padx=theme.PAD)
        nav = self._build_nav()
        # 指示条：单实例，切换时弹簧滑到目标项旁边——比逐项变色更有"实体感"
        self._indicator = tk.Frame(nav, bg=theme.SIDEBAR_INDICATOR, width=3, height=1)
        self._indicator_y = motion.Spring(self, lambda y: self._place_indicator(y))
        self._content = tk.Frame(self, bg=theme.SURFACE)
        self._content.pack(side="left", fill="both", expand=True)

    def _build_brand(self) -> None:
        brand = tk.Frame(self._sidebar, bg=theme.SIDEBAR_BG)
        brand.pack(fill="x", padx=theme.PAD, pady=(theme.PAD_LG, theme.PAD_LG - 4))
        brand_row = tk.Frame(brand, bg=theme.SIDEBAR_BG)
        brand_row.pack(anchor="w")
        logo_size = round(30 * theme.SCALE)
        logo = tk.Canvas(
            brand_row, width=logo_size, height=logo_size,
            bg=theme.SIDEBAR_BG, highlightthickness=0, bd=0,
        )
        logo.pack(side="left")
        icons.draw_logo(
            logo, logo_size / 2, logo_size / 2, logo_size,
            "#ffffff", "#7c9bff",
        )
        tk.Label(
            brand_row, text="KeyVault", font=theme.FONT_HEADING,
            bg=theme.SIDEBAR_BG, fg="#ffffff", anchor="w",
        ).pack(side="left", padx=(theme.PAD_SM, 0))
        tk.Label(
            brand, text="本地密钥保管箱", font=theme.FONT_SUBTITLE,
            bg=theme.SIDEBAR_BG, fg=theme.SIDEBAR_MUTED, anchor="w",
        ).pack(anchor="w", pady=(2, 0))

    def _build_nav(self) -> tk.Frame:
        nav = tk.Frame(self._sidebar, bg=theme.SIDEBAR_BG)
        nav.pack(fill="x", pady=(theme.PAD_SM, 0))
        icon_size = round(18 * theme.SCALE)
        for section, entries in NAV_SECTIONS:
            tk.Label(
                nav, text=section, font=theme.FONT_SUBTITLE,
                bg=theme.SIDEBAR_BG, fg=theme.SIDEBAR_MUTED, anchor="w",
            ).pack(fill="x", padx=theme.PAD, pady=(theme.PAD, theme.PAD_SM))
            for key, label in entries:
                row = tk.Frame(nav, bg=theme.SIDEBAR_BG)
                row.pack(fill="x")
                icon = tk.Canvas(
                    row, width=icon_size, height=icon_size,
                    bg=theme.SIDEBAR_BG, highlightthickness=0, bd=0,
                )
                icon.pack(side="left", padx=(theme.PAD + 2, 0))
                icons.ICONS[key](icon, icon_size / 2, icon_size / 2, icon_size, theme.SIDEBAR_FG)
                btn = tk.Button(
                    row, text=label, anchor="w",
                    font=theme.FONT, bg=theme.SIDEBAR_BG, fg=theme.SIDEBAR_FG,
                    activebackground=theme.SIDEBAR_ACTIVE_BG,
                    activeforeground=theme.SIDEBAR_ACTIVE_FG,
                    relief="flat", bd=0, padx=theme.PAD_SM, pady=theme.PAD_SM,
                    cursor="hand2", command=lambda k=key: self.show_page(k),
                )
                btn.pack(side="left", fill="x", expand=True)
                btn.bind("<Enter>", lambda e, b=btn: self._nav_tint(b, theme.SIDEBAR_HOVER_FG))
                btn.bind("<Leave>", lambda e, b=btn: self._nav_tint(b, self._nav_rest_color(b)))
                self._nav_buttons[key] = btn
                self._nav_icons[key] = (icon, icon_size)
        return nav

    def _recolor_icon(self, key: str, color: str) -> None:
        icon, size = self._nav_icons[key]
        icon.delete("all")
        icons.ICONS[key](icon, size / 2, size / 2, size, color)

    def _nav_rest_color(self, btn: tk.Button) -> str:
        active = btn is self._nav_buttons.get(self._current)
        return theme.SIDEBAR_ACTIVE_FG if active else theme.SIDEBAR_FG

    def _nav_tint(self, btn: tk.Button, target: str) -> None:
        source = btn.cget("fg")
        gen = getattr(btn, "_tint_gen", 0) + 1
        btn._tint_gen = gen

        def tick(step: int = 0) -> None:
            if btn._tint_gen != gen:
                return
            btn.config(fg=motion.lerp_color(source, target, min(step / 6, 1.0)))
            if step < 6:
                btn.after(16, tick, step + 1)

        tick()

    def _place_indicator(self, y: float) -> None:
        self._indicator.place_configure(y=round(y))

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
        self._current = key
        for k, btn in self._nav_buttons.items():
            active = k == key
            btn.config(
                bg=theme.SIDEBAR_ACTIVE_BG if active else theme.SIDEBAR_BG,
                fg=theme.SIDEBAR_ACTIVE_FG if active else theme.SIDEBAR_FG,
            )
            self._recolor_icon(
                k, theme.SIDEBAR_ACTIVE_FG if active else theme.SIDEBAR_FG,
            )
            # 图标画布底色跟按钮一起染色，高亮块才是完整的一行
            self._nav_icons[k][0].config(
                bg=theme.SIDEBAR_ACTIVE_BG if active else theme.SIDEBAR_BG,
            )
        self._move_indicator(self._nav_buttons[key])
        for k, page in self._pages.items():
            if k == key:
                page.tkraise()
                if hasattr(page, "on_show"):
                    page.on_show()
                self._slide_in(page, k)

    def _move_indicator(self, btn: tk.Button) -> None:
        """指示条滑到目标项；布局没完成（首帧）就等一帧再snap过去。"""
        self._indicator.config(height=btn.winfo_height() - 2 * theme.PAD_SM)
        y = btn.winfo_y() + theme.PAD_SM
        if y <= theme.PAD_SM:
            self.after(30, lambda: self._indicator_y.jump(
                btn.winfo_y() + theme.PAD_SM,
            ))
        else:
            self._indicator_y.to(y)

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
