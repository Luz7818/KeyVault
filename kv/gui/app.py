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
        # 缩放在建页**之前**落位：页面按最终字号一次建对，省掉
        # 「先按 100% 建一遍、set_zoom 再全应用重排一遍」的启动开销
        theme.set_zoom(self._ui_scale)
        self._queue: queue.Queue[tuple[Callable, tuple, dict]] = queue.Queue()
        self._pages: dict[str, tk.Widget] = {}
        self._page_slides: dict[str, motion.Spring] = {}
        self._prev_key: str = ""
        self._current: str = ""
        self._zoom_pending: float | None = None
        self._zoom_job: str | None = None
        self._build_layout()
        self._register_pages()
        self.show_page("secrets")
        self.bind_all("<Control-MouseWheel>", self._on_zoom_wheel)
        self.bind_all("<Control-equal>", lambda e: self._zoom_by(0.05))
        self.bind_all("<Control-minus>", lambda e: self._zoom_by(-0.05))
        self.bind_all("<Control-k>", lambda e: self._open_palette())
        self.bind_all("<Control-f>", self._focus_search)
        self.bind_all("<Key-n>", self._hotkey_add)
        self.bind_all("<F5>", self._hotkey_refresh)
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
            # 顶层页与标签页内嵌的 ScrollPage 同一协议（wheel_scroll）
            if hasattr(w, "wheel_scroll"):
                w.wheel_scroll(-(event.delta // 120))
                return "break"
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

    # ------------------------------------------------------------ 快捷键

    _TEXT_CLASSES = ("Entry", "TEntry", "TCombobox", "Text", "Spinbox", "TSpinbox")

    def _typing_in_field(self, w: tk.Widget | None = None) -> bool:
        """焦点在文本输入控件里时，单字符热键（如 N）必须让位给打字。
        w 可注入（测试用）；缺省取当前焦点。"""
        if w is None:
            w = self.focus_get()
        return w is not None and (
            isinstance(w, (tk.Entry, tk.Text)) or w.winfo_class() in self._TEXT_CLASSES
        )

    def _hotkey_add(self, _event=None) -> str:
        """N = 新增密钥（密钥页工具提示承诺的键位）。打字中不抢。"""
        if self._current != "secrets" or self._typing_in_field():
            return ""
        page = self._pages.get("secrets")
        if page is not None and hasattr(page, "hotkey_add"):
            page.hotkey_add()
            return "break"
        return ""

    def _hotkey_refresh(self, _event=None) -> str:
        """F5 = 重载当前页（有 refresh/on_show 的页面）。"""
        page = self._pages.get(self._current)
        if page is None:
            return ""
        if hasattr(page, "refresh"):
            page.refresh()
        elif hasattr(page, "on_show"):
            page.on_show()
        return "break"

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
        """滚轮缩放**防抖**：连发只累积目标值，停手 140ms 后应用一次。

        set_zoom 的级联（命名字体 + 全部 LISTENERS 重绘 + 写库）单次就要
        几百毫秒，滚轮每 tick 同步跑全量就是「缩放太卡顿」的根因。
        """
        self._zoom_pending = round(min(max(
            (self._zoom_pending if self._zoom_pending is not None else theme.ZOOM)
            + delta, 0.85), 1.6), 2)
        if self._zoom_job is None:
            self._zoom_job = self.after(140, self._apply_zoom)

    def _apply_zoom(self) -> None:
        self._zoom_job = None
        pending, self._zoom_pending = self._zoom_pending, None
        zoom = theme.set_zoom(pending)
        store = self.store
        if store is not None:
            try:
                store.set_setting("ui_scale", str(zoom))  # 一次手势只写一次库
            except Exception:
                pass  # 缩放不因写库失败而中断
        self.set_status(f"界面缩放 {round(zoom * 100)}%（Ctrl+滚轮调整，自动保存）")

    # ------------------------------------------------------------ 布局

    def _build_layout(self) -> None:
        # 状态栏必须先 pack——后 pack 的 expand 内容区会把它挤出窗口
        self._status = StatusLabel(self)
        self._sidebar = Sidebar(self, on_navigate=self.show_page,
                                on_palette=self._open_palette)
        self._sidebar.pack(side="left", fill="y")
        self._content = tk.Frame(self, bg=theme.CANVAS)
        self._content.pack(side="left", fill="both", expand=True)

    def _register_pages(self) -> None:
        """只登记页面类不实例化——页面在首次导航时才构建。

        6 个页面全量预建是几百个 widget 的同步构造，还把每页的自绘控件
        全部挂进 theme.LISTENERS，启动慢、之后每次缩放级联也跟着变重。
        未访问的页面不参与布局也不参与缩放级联。
        """
        from kv.gui.pages.capture import CapturePage
        from kv.gui.pages.detect import DetectPage
        from kv.gui.pages.ingest import IngestPage
        from kv.gui.pages.secrets import SecretsPage
        from kv.gui.pages.security import SecurityPage
        from kv.gui.pages.settings import SettingsPage

        self._registry: dict[str, type] = {
            "secrets": SecretsPage,
            "detect": DetectPage,
            "capture": CapturePage,
            "ingest": IngestPage,
            "security": SecurityPage,
            "settings": SettingsPage,
        }

    def _page(self, key: str) -> tk.Widget:
        """取页面，未构建则当场构建并 place 进内容区（place 堆叠 + tkraise）。"""
        page = self._pages.get(key)
        if page is None:
            page = self._registry[key](self._content, app=self)
            page.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._pages[key] = page
        return page

    def show_page(self, key: str) -> None:
        """切页 = 按需构建 + 侧边栏高亮 + 内容区抬页 + 轻滑入。"""
        if key not in self._registry or key == self._current:
            return
        self._current = key
        self._sidebar.set_current(key)
        page = self._page(key)
        page.tkraise()
        if hasattr(page, "on_show"):
            page.on_show()
        self._slide_in(page, key)

    def _slide_in(self, page: tk.Widget, key: str) -> None:
        """页面过渡：新页从右侧 sp(18) 滑入，旧页同时向左微退 sp(8)——
        新页左缘露出的条带是旧页在退场（视差交叉滑动）。

        tkinter 控件树不支持逐像素透明度（alpha 只对整个 Toplevel 生效），
        「内容淡入」以此替代：观感上是两页互相推移而不是硬切。
        首次显示无动画；弹簧可中断重定向，快速连点不跳变。
        """
        dist = theme.sp(18)
        back = theme.sp(8)
        prev_key = self._prev_key
        if prev_key and prev_key != key:
            prev_page = self._pages.get(prev_key)
            if prev_page is not None:
                self._slide_page(prev_page, prev_key, -back, first=False)
        self._slide_page(page, key, dist, first=True)
        self._prev_key = key

    def _slide_page(self, page: tk.Widget, key: str, target: float, *,
                    first: bool) -> None:
        spring = self._page_slides.get(key)
        if spring is None:
            spring = motion.Spring(
                self, lambda x, p=page: p.place_configure(x=round(x)),
            )
            self._page_slides[key] = spring
            if first:
                spring.jump(0)  # 首显落位即可，没有上一页可视差
                return
        if first:
            page.place_configure(x=round(target))
            spring.jump(target)
            spring.to(0)
        else:
            spring.to(target)  # 退场微退；被再次显示时 jump 重定向回场

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
