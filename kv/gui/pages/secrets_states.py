"""密钥页的状态卡：空态引导与搜索无匹配——两张「库里没有可显示的东西」的卡。

从 secrets.py 拆出（页面 460 行预算）：卡片的构建与文案在这里，
何时显示哪张（visibility 的 pack 切换）留在页面里——那是刷新逻辑的一部分。
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from kv.gui import icons, theme
from kv.gui.controls import Card, RoundButton

EMPTY_TEXTS = {
    "uninitialized": (
        "还没有创建密钥库",
        "密钥库用 Windows DPAPI 加密，只属于当前 Windows 账户，数据不出本机。",
        "初始化 Vault", "看看设置页", "了解捕获",
    ),
    "empty": (
        "库里还没有密钥",
        "手动添加一条，或开启剪贴板监控——复制到密钥时会自动识别并进入审批队列。",
        "添加密钥", "去设置页", "开启捕获",
    ),
}


def _px(base: int) -> int:
    return max(round(base * theme.SCALE), 1)


class StateCards:
    """空态卡 + 无匹配卡。show_empty/show_no_match 填文案，显示切换归页面。"""

    def __init__(self, parent: tk.Widget, *, on_add: Callable[[], None],
                 on_init: Callable[[], None], go_capture: Callable[[], None],
                 go_settings: Callable[[], None],
                 clear_filters: Callable[[], None]) -> None:
        self._actions = {
            "add": on_add, "init": on_init,
            "capture": go_capture, "settings": go_settings,
        }
        self._clear_filters = clear_filters
        self._build_empty(parent)
        self._build_no_match(parent)

    # ------------------------------------------------------------ 空态

    def _build_empty(self, parent: tk.Widget) -> None:
        self.empty = Card(parent)
        body = self.empty.body
        self._art = tk.Canvas(
            body, width=_px(72), height=_px(72), bg=theme.SURFACE,
            highlightthickness=0, bd=0,
        )
        self._art.pack(pady=(theme.PAD_LG, theme.PAD_SM))
        self._empty_title = tk.Label(
            body, text="", font=theme.FONT_HEADING, bg=theme.SURFACE, fg=theme.TEXT,
        )
        self._empty_title.pack(anchor="center")
        self._empty_hint = tk.Label(
            body, text="", font=theme.FONT, bg=theme.SURFACE,
            fg=theme.TEXT_SECONDARY, justify="center",
        )
        self._empty_hint.pack(anchor="center", pady=(theme.PAD_SM, 0))
        row = tk.Frame(body, bg=theme.SURFACE)
        row.pack(anchor="center", pady=theme.PAD)
        self._btn1 = RoundButton(row, "", lambda: None, kind="accent")
        self._btn1.pack(side="left")
        self._btn2 = RoundButton(row, "", lambda: None)
        self._btn2.pack(side="left", padx=(theme.GAP, 0))
        self._btn3 = RoundButton(row, "", lambda: None)
        self._btn3.pack(side="left", padx=(theme.GAP, 0))

    def _draw_art(self) -> None:
        """空态插图：主色浅底圆 + 钥匙线稿。"""
        c = self._art
        c.delete("all")
        s = _px(72)
        c.create_oval(_px(4), _px(4), s - _px(4), s - _px(4),
                      fill=theme.ACCENT_SOFT, outline="")
        icons.draw_key(c, s / 2, s / 2, _px(30), theme.ACCENT)

    def show_empty(self, mode: str) -> None:
        """uninitialized → 一键建库；empty → 添加或去捕获。"""
        title, hint, b1, b2, b3 = EMPTY_TEXTS[mode]
        self._draw_art()
        self._empty_title.config(text=title)
        self._empty_hint.config(text=hint)
        primary = "init" if mode == "uninitialized" else "add"
        self._btn1.set_text(b1)
        self._btn1.set_command(self._actions[primary])
        self._btn2.set_text(b2)
        self._btn2.set_command(self._actions["settings"])
        self._btn3.set_text(b3)
        self._btn3.set_command(self._actions["capture"])

    # ------------------------------------------------------------ 无匹配

    def _build_no_match(self, parent: tk.Widget) -> None:
        self.no_match = Card(parent)
        body = self.no_match.body
        tk.Label(
            body, text="没有匹配的记录", font=theme.FONT_HEADING,
            bg=theme.SURFACE, fg=theme.TEXT,
        ).pack(pady=(theme.PAD_LG, theme.PAD_SM))
        self._nomatch_hint = tk.Label(
            body, text="", font=theme.FONT, bg=theme.SURFACE,
            fg=theme.TEXT_SECONDARY,
        )
        self._nomatch_hint.pack(pady=(0, theme.PAD))
        RoundButton(
            body, "清除搜索", self._clear_filters, kind="accent",
        ).pack(pady=(0, theme.PAD_LG))

    def show_no_match(self, query: str, status: str) -> None:
        """说明是筛选条件落空（不是库空），给一键清除的出路。"""
        what = []
        if query:
            what.append(f"「{query}」")
        if status:
            what.append(f"状态 {status}")
        self._nomatch_hint.config(text="没有符合 " + " + ".join(what) + " 的记录。")
