"""TOTP 活码窗：右键菜单「TOTP 验证码」打开的大号验证码 + 倒计时环。

TOTP 是对值的解释不是存储类别（schema 的 kind 是封闭枚举），所以入口
是显式的菜单动作：种子由调用方经 reveal 窗口取好传入，本模块不碰库。
每秒重算（RFC 6238 纯函数）；环画剩余比例，剩 5s 内转琥珀——「快过期了」
是状态指示不是装饰。验证码不是机密也不是明文，显示无碍、复制走排除历史
的同一剪切板通道。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from typing import Callable

from kv.gui import motion, theme
from kv.gui.controls import RoundButton


class TotpPanel(tk.Frame):
    """活码面板。now_fn 可注入（测试固定时钟）；销毁即停表。"""

    PERIOD = 30

    def __init__(self, parent, seed: bytes, *, on_copy: Callable[[str], None],
                 now_fn: Callable[[], datetime] | None = None) -> None:
        super().__init__(parent, bg=theme.SURFACE)
        from kv import clock
        from kv.ops import totp as totpops

        self._seed = seed
        self._generate = totpops.generate
        self._now_fn = now_fn or clock.now
        self._code = ""
        self._ring = tk.Canvas(
            self, width=theme.sp(46), height=theme.sp(46),
            bg=theme.SURFACE, highlightthickness=0, bd=0,
        )
        self._ring.pack(side="left", padx=(theme.PAD, theme.GAP), pady=theme.PAD)
        # 验证码用等宽字体放大一档（活码窗是短命表面，不进缩放级联）
        f = tkfont.Font(font=theme.FONT_MONO)
        f.configure(size=-theme.sp(24), weight="bold")
        self._code_lbl = tk.Label(self, text="", font=f, bg=theme.SURFACE,
                                  fg=theme.TEXT, anchor="center")
        self._code_lbl.pack(side="left", expand=True)
        RoundButton(self, "复制验证码", lambda: on_copy(self._code)).pack(
            side="right", padx=(theme.GAP, theme.PAD))
        self._tick()

    def _tick(self) -> None:
        if not self.winfo_exists():
            return
        code, remaining = self._generate(self._seed, self._now_fn())
        self._code = code
        self._code_lbl.config(text=f"{code[:3]} {code[3:]}")
        self._draw_ring(remaining)
        self.after(1000, self._tick)

    def _draw_ring(self, remaining: int) -> None:
        c, size = self._ring, theme.sp(46)
        c.delete("all")
        margin, width = theme.sp(4), theme.sp(4)
        box = (margin, margin, size - margin, size - margin)
        c.create_arc(*box, start=90, extent=359.9, outline=theme.BORDER,
                     width=width, style="arc")
        color = theme.WARNING if remaining <= 5 else theme.ACCENT
        c.create_arc(*box, start=90, extent=359.9 * remaining / self.PERIOD,
                     outline=color, width=width, style="arc")
        small = tkfont.Font(font=theme.FONT_SMALL)
        small.configure(size=-theme.sp(13))
        c.create_text(
            size / 2, size / 2, text=str(remaining), fill=theme.TEXT_SECONDARY,
            font=small, tags=("remaining",),
        )


def open_totp_for_secret(page, store, name: str) -> None:
    """secrets 页菜单入口：经 reveal 取种子（顺手留审计），合法才开窗。

    TOTP 是对值的解释而非存储类别，所以入口是显式的菜单动作——非种子
    记录不该为「看看详情」付一次 reveal。种子非法弹窗说明，不打断浏览。
    """
    from tkinter import messagebox

    from kv.ops import totp as totpops

    try:
        with store.reveal(name, actor="gui") as buf:
            seed = totpops.parse_seed(buf.decode("utf-8", "replace"))
    except totpops.SeedError:
        messagebox.showwarning(
            "不是 TOTP 种子",
            f"「{name}」的值不是 base32 种子。种子是验证器「显示密钥」里那串。",
            parent=page,
        )
        return
    except Exception as exc:
        messagebox.showerror("失败", str(exc), parent=page)
        return
    build_totp_window(page, name, seed)


def build_totp_window(parent: tk.Widget, name: str, seed: bytes) -> None:
    """独立活码窗，居中在 parent 上。种子非法不该走到这里（入口先 parse 过）。"""
    win = tk.Toplevel(parent)
    win.title(f"TOTP — {name}")
    win.resizable(False, False)
    win.transient(parent)
    win.configure(bg=theme.SURFACE)
    win.bind("<Escape>", lambda _e: win.destroy())

    from kv.capture.clipboard import Win32Clipboard

    body = tk.Frame(win, bg=theme.SURFACE)
    body.pack(fill="x")
    feedback = tk.Label(body, text="", font=theme.FONT_SMALL,
                        bg=theme.SURFACE, fg=theme.SUCCESS)
    feedback.pack(anchor="w", padx=theme.PAD)

    def _copy(code: str) -> None:
        written = Win32Clipboard().write(code, exclude_history=True)
        feedback.config(
            text="已复制验证码" if written else "复制失败：剪切板被占用",
            fg=theme.SUCCESS if written else theme.ERROR,
        )

    TotpPanel(body, seed, on_copy=_copy).pack(fill="x")
    tk.Label(
        body, text="验证码 30 秒轮换；复制走剪贴板自动擦除通道", font=theme.FONT_SMALL,
        bg=theme.SURFACE, fg=theme.TEXT_TERTIARY,
    ).pack(anchor="w", padx=theme.PAD, pady=(0, theme.PAD_SM))
    motion.fade_in_window(win)
    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    win.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 3}")
