"""主窗口：侧边栏导航 + 内容区页面切换。

侧边栏是**一整块 Canvas**：垂直渐变底、品牌区、分组导航、页脚版本全部
手绘在画布上。用 tk.Label/Frame 拼侧边栏的话，每个不透明控件都会在渐变
上盖出一块色差——手绘是 tkinter 里唯一无色差的深色渐变做法。
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from kv import __version__
from kv.gui import icons, motion, paint, theme
from kv.gui.icon import apply as apply_app_icon
from kv.gui.widgets import StatusLabel

NAV_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    ("密钥", [("secrets", "密钥管理")]),
    ("工具", [("detect", "检测"), ("capture", "捕获"), ("ingest", "批量操作")]),
    ("系统", [("security", "安全"), ("settings", "设置")]),
]


def _px(base: int) -> int:
    return max(round(base * theme.SCALE), 1)


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
        self.after(50, self._drain_queue)

    def _open_palette(self) -> None:
        from kv.gui.palette import open_palette

        open_palette(self)

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
        self._build_sidebar()
        self._content = tk.Frame(self, bg=theme.CANVAS)
        self._content.pack(side="left", fill="both", expand=True)

    # ------------------------------------------------------------ 侧边栏

    def _build_sidebar(self) -> None:
        self._sidebar = tk.Canvas(
            self, width=theme.SIDEBAR_WIDTH, highlightthickness=0, bd=0,
            bg=theme.SIDEBAR_TOP,
        )
        self._sidebar.pack(side="left", fill="y")
        self._indicator_y = motion.Spring(self, self._place_indicator)
        self._indicator_item: str | None = None
        self._sidebar.bind("<Configure>", lambda _e: self._paint_sidebar())
        theme.LISTENERS.append(self._paint_sidebar)

    def _row_look(self, key: str) -> tuple[str, str]:
        """一行的 (底色, 前景色)。active > hover > 静置。"""
        if key == self._current:
            return theme.SIDEBAR_ACTIVE_BG, theme.SIDEBAR_ACTIVE_FG
        if key == self._hover_key:
            return theme.SIDEBAR_ACTIVE_BG, theme.SIDEBAR_HOVER_FG
        return "", theme.SIDEBAR_FG

    def _paint_sidebar(self) -> None:
        c = self._sidebar
        w, h = c.winfo_width(), c.winfo_height()
        if w < 10 or h < 10:
            return
        self._geo = (w, h)
        paint.draw_v_gradient(c, theme.SIDEBAR_TOP, theme.SIDEBAR_BOTTOM, w, h)
        self._row_geo.clear()
        self._paint_brand(w)
        self._paint_nav(w)
        self._paint_footer(w, h)
        self._paint_indicator()

    def _paint_brand(self, w: int) -> None:
        c = self._sidebar
        logo_size = _px(30)
        cy = _px(56)
        icons.draw_logo(c, _px(44), cy, logo_size, "#ffffff", "#7c9bff")
        c.create_text(
            _px(68), cy, text="KeyVault", anchor="w", fill="#ffffff",
            font=theme.FONT_HEADING,
        )
        c.create_text(
            _px(44), cy + _px(24), text="本地密钥保管箱", anchor="w",
            fill=theme.SIDEBAR_MUTED, font=theme.FONT_SUBTITLE,
        )

    def _paint_nav(self, w: int) -> None:
        c = self._sidebar
        y = _px(112)
        inset = _px(10)
        row_h = _px(42)
        icon_size = _px(18)
        for section, entries in NAV_SECTIONS:
            c.create_text(
                _px(44), y + _px(10), text=section, anchor="w",
                fill=theme.SIDEBAR_SECTION, font=theme.FONT_SUBTITLE,
            )
            y += _px(30)
            for key, label in entries:
                bg, fg = self._row_look(key)
                if bg:
                    pts = paint.round_points(inset, y, w - inset, y + row_h - 4, _px(9))
                    c.create_polygon(*pts, fill=bg, outline="", smooth=True)
                icons.ICONS[key](c, _px(34) + icon_size // 2, y + row_h // 2 - 2,
                                 icon_size, fg)
                c.create_text(
                    _px(60), y + row_h // 2 - 2, text=label, anchor="w",
                    fill=fg, font=theme.FONT, tags=(f"row-{key}",),
                )
                self._row_geo[key] = (y, row_h)
                y += row_h
            y += _px(10)
        for key in self._row_geo:
            c.tag_bind(f"row-{key}", "<Button-1>", lambda e, k=key: self.show_page(k))
            c.tag_bind(f"row-{key}", "<Enter>", lambda e, k=key: self._set_hover(k))
            c.tag_bind(f"row-{key}", "<Leave>", lambda e: self._set_hover(""))

    def _paint_footer(self, w: int, h: int) -> None:
        c = self._sidebar
        y = h - _px(58)
        c.create_line(_px(20), y, w - _px(20), y, fill=theme.SIDEBAR_HAIRLINE, width=1)
        c.create_text(
            _px(44), y + _px(16), text=f"kv {__version__}", anchor="w",
            fill=theme.SIDEBAR_MUTED, font=theme.FONT_SUBTITLE,
        )
        c.create_text(
            _px(44), y + _px(36), text="数据不出本机", anchor="w",
            fill=theme.SIDEBAR_SECTION, font=theme.FONT_SUBTITLE,
        )

    def _set_hover(self, key: str) -> None:
        if key == self._hover_key:
            return
        self._hover_key = key
        self._paint_sidebar()

    def _paint_indicator(self) -> None:
        """主色小条落在当前行左侧（整块侧边栏重画后的静态落位）。"""
        geo = self._row_geo.get(self._current)
        if not geo:
            return
        row_y, row_h = geo
        y = self._indicator_y.value
        if abs(y - row_y) > row_h * 1.6:  # 弹簧值已过期（重建后），直接落位
            y = row_y
            self._indicator_y.jump(y)
        self._place_indicator(y)

    def _place_indicator(self, y: float) -> None:
        """画/重画指示条。弹簧飞行时每帧删旧画新，不拖影。"""
        geo = self._row_geo.get(self._current)
        if not geo:
            return
        c = self._sidebar
        if self._indicator_item:
            try:
                c.delete(self._indicator_item)
            except tk.TclError:
                pass
        row_y, row_h = geo
        pts = paint.round_points(
            _px(14), y + _px(9), _px(17), y + row_h - _px(13), _px(2),
        )
        self._indicator_item = c.create_polygon(
            *pts, fill=theme.SIDEBAR_INDICATOR, outline="", smooth=True,
        )

    def show_page(self, key: str) -> None:
        if key not in self._row_geo:
            # 侧边栏还没画好（首帧）：先抬页面（注册已完成），指示条等几何就绪
            self._current = key
            page = self._pages.get(key)
            if page is not None:
                page.tkraise()
                if hasattr(page, "on_show"):
                    page.on_show()
            return
        if key == self._current:
            return
        self._current = key
        self._hover_key = ""
        self._paint_sidebar()
        y, _ = self._row_geo[key]
        self._indicator_y.to(y)
        for k, page in self._pages.items():
            if k == key:
                page.tkraise()
                if hasattr(page, "on_show"):
                    page.on_show()
                self._slide_in(page, k)

    # ------------------------------------------------------------ 页面

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
