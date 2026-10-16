"""侧边栏：一整块 Canvas——垂直渐变底、品牌区、分组导航、页脚、指示条。

用 tk.Label/Frame 拼侧边栏的话，每个不透明控件都会在渐变上盖出一块色差
——手绘是 tkinter 里唯一无色差的深色渐变做法。

排版网格（_grid）单源：品牌/分组标题/导航行/页脚全部从同一套列位取值，
分组标题对齐图标列、文字列从图标列推导——三套左对齐互相打架是「散」的
主要来源。全部尺寸走 theme.sp（DPI×UI 缩放联动）：用户放大字体时图标、
间距、栏宽一起长，否则出现「图标贴字」。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from typing import Callable

from kv import __version__
from kv.gui import icons, motion, paint, theme

NAV_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    ("密钥", [("secrets", "密钥管理")]),
    ("工具", [("detect", "检测"), ("capture", "捕获"), ("ingest", "批量操作")]),
    ("系统", [("security", "安全"), ("settings", "设置")]),
]


def _px(base: int) -> int:
    return theme.sp(base)


class Sidebar(tk.Canvas):
    """深色渐变侧边栏。on_navigate(key) 在点击导航行时回调。"""

    def __init__(self, parent: tk.Widget, *, on_navigate: Callable[[str], None]) -> None:
        super().__init__(
            parent, width=theme.SIDEBAR_WIDTH, highlightthickness=0, bd=0,
            bg=theme.SIDEBAR_TOP,
        )
        self._on_navigate = on_navigate
        self._current = ""
        self._hover_key = ""
        self._hover_bg: str | None = None
        self._row_text: dict[str, str] = {}
        self._row_geo: dict[str, tuple[int, int]] = {}
        self._indicator_y = motion.Spring(self, self._place_indicator)
        self._indicator_item: str | None = None
        self.bind("<Configure>", lambda _e: self._paint())
        theme.LISTENERS.append(self._sync_width)
        theme.LISTENERS.append(self._paint)
        self._sync_width()

    # ------------------------------------------------------------ 对外

    @property
    def current(self) -> str:
        return self._current

    def set_current(self, key: str) -> None:
        """高亮 key 行并把指示条滑过去；侧边栏未画好时只记住，画好后落位。"""
        self._current = key
        self._hover_key = ""
        if key in self._row_geo:
            self._paint()
            self._indicator_y.to(self._row_geo[key][0])

    def wheel_scroll(self, units: int) -> None:
        """高缩放档位下导航总高会超过视口——滚轮滚侧边栏本身。"""
        self.yview_scroll(units, "units")

    # ------------------------------------------------------------ 绘制

    def _sync_width(self) -> None:
        desired = _px(252)
        if abs(self.winfo_reqwidth() - desired) > 2:
            self.configure(width=desired)

    def _grid(self) -> dict:
        icon_size = _px(18)
        icon_x = _px(26)
        return {
            "margin": _px(16),
            "inset": _px(10),
            "icon_size": icon_size,
            "icon_x": icon_x,
            "icon_cx": icon_x + icon_size // 2,
            "text_x": icon_x + icon_size + _px(16),
            "row_h": _px(42),
            "section_h": _px(32),
            "group_gap": _px(12),
        }

    def _paint(self) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10 or h < 10:
            return
        content_h = max(h, self.winfo_reqheight())
        paint.draw_v_gradient(self, theme.SIDEBAR_TOP, theme.SIDEBAR_BOTTOM,
                              w, content_h, steps=140)
        self._row_geo.clear()
        self._row_text.clear()
        self._hover_bg = None
        self._hover_key = ""
        self._paint_brand()
        nav_end = self._paint_nav(w)
        # 页脚锚在「视口底」与「导航结束」两者更靠下的位置：高缩放下导航
        # 总高超过视口时，页脚跟着内容走而不是叠在导航行上——侧边栏可滚。
        footer_y = max(h - _px(78), nav_end + _px(16))
        self._paint_footer(w, footer_y)
        self._paint_indicator()
        content_h = max(h, footer_y + _px(72))
        self.configure(scrollregion=(0, 0, w, content_h))
        if content_h <= h:
            self.yview_moveto(0)  # 收回溢出后把视图回正

    def _paint_brand(self) -> None:
        g = self._grid()
        logo_size = _px(34)
        cy = _px(56)
        logo_cx = g["margin"] + logo_size // 2
        icons.draw_logo(self, logo_cx, cy, logo_size, "#ffffff", "#7c9bff")
        title_x = g["margin"] + logo_size + _px(12)
        self.create_text(
            title_x, cy, text="KeyVault", anchor="w", fill="#ffffff",
            font=theme.FONT_HEADING,
        )
        # 副标题位置按字体真实行高度量排（标题底 + 间距 + 副标题半高）——
        # 固定 24px 偏移在高缩放下小于标题行高的半，两行字直接叠在一起
        f_title = tkfont.Font(font=theme.FONT_HEADING)
        f_sub = tkfont.Font(font=theme.FONT_SUBTITLE)
        sub_cy = (cy + f_title.metrics("linespace") // 2
                  + _px(8) + f_sub.metrics("linespace") // 2)
        # 副标题与标题左对齐（曾对齐到 logo 左缘，视觉上歪向一侧）
        self.create_text(
            title_x, sub_cy, text="本地密钥保管箱", anchor="w",
            fill=theme.SIDEBAR_MUTED, font=theme.FONT_SUBTITLE,
        )

    def _row_look(self, key: str) -> tuple[str, str]:
        """一行的 (底色, 前景色)。active > hover > 静置。"""
        if key == self._current:
            return theme.SIDEBAR_ACTIVE_BG, theme.SIDEBAR_ACTIVE_FG
        if key == self._hover_key:
            return theme.SIDEBAR_ACTIVE_BG, theme.SIDEBAR_HOVER_FG
        return "", theme.SIDEBAR_FG

    def _paint_nav(self, w: int) -> int:
        """画分组导航，返回导航区的结束 y（页脚锚定用）。"""
        g = self._grid()
        y = _px(120)
        for section, entries in NAV_SECTIONS:
            self.create_text(
                g["icon_x"], y + _px(10), text=section, anchor="w",
                fill=theme.SIDEBAR_SECTION, font=theme.FONT_SUBTITLE,
            )
            y += g["section_h"]
            for key, label in entries:
                bg, fg = self._row_look(key)
                if bg:
                    pts = paint.round_points(
                        g["inset"], y, w - g["inset"], y + g["row_h"] - 4, _px(9),
                    )
                    self.create_polygon(*pts, fill=bg, outline="", smooth=True)
                icons.ICONS[key](self, g["icon_cx"], y + g["row_h"] // 2 - 2,
                                 g["icon_size"], fg)
                txt_id = self.create_text(
                    g["text_x"], y + g["row_h"] // 2 - 2, text=label, anchor="w",
                    fill=fg, font=theme.FONT, tags=(f"row-{key}",),
                )
                self._row_text[key] = txt_id
                self._row_geo[key] = (y, g["row_h"])
                y += g["row_h"]
            y += g["group_gap"]
        for key in self._row_geo:
            self.tag_bind(f"row-{key}", "<Button-1>",
                          lambda _e, k=key: self._on_navigate(k))
            self.tag_bind(f"row-{key}", "<Enter>", lambda _e, k=key: self._set_hover(k))
            self.tag_bind(f"row-{key}", "<Leave>", lambda _e: self._set_hover(""))
        return y

    def _paint_footer(self, w: int, y: int) -> None:
        """在 y（视口底与导航底取更靠下者）画页脚：分隔线 + Ctrl+K 提示 + 版本。"""
        g = self._grid()
        x = g["icon_x"]
        self.create_line(_px(16), y, w - _px(16), y, fill=theme.SIDEBAR_HAIRLINE, width=1)
        # Ctrl+K 提示：描边胶囊 + 键位文字，可点的视觉暗示
        label = "Ctrl"
        f = tkfont.Font(font=theme.FONT_SMALL)
        lw = f.measure(label)
        self.create_rectangle(
            x, y + _px(12), x + lw + _px(12), y + _px(30),
            outline=theme.SIDEBAR_MUTED, width=1,
        )
        self.create_text(
            x + lw / 2 + _px(6), y + _px(21), text=label,
            fill=theme.SIDEBAR_MUTED, font=theme.FONT_SMALL,
        )
        self.create_text(
            x + lw + _px(20), y + _px(21), text="+ K  快速复制密钥",
            anchor="w", fill=theme.SIDEBAR_MUTED, font=theme.FONT_SMALL,
        )
        self.create_text(
            x, y + _px(46), text=f"kv {__version__} · 数据不出本机",
            anchor="w", fill=theme.SIDEBAR_SECTION, font=theme.FONT_SUBTITLE,
        )

    def _set_hover(self, key: str) -> None:
        """hover 走 O(1) 局部更新——整块重画每次要重铺 140 条渐变带，
        鼠标扫过导航列就是一次全量重绘（卡顿源之一）。"""
        if key == self._hover_key:
            return
        old, self._hover_key = self._hover_key, key
        if old and old != self._current and old in self._row_geo:
            self._restore_row(old)
        if key and key != self._current and key in self._row_geo:
            self._apply_hover(key)

    def _restore_row(self, key: str) -> None:
        """撤掉 hover 底色块，该行文字回到静置色。"""
        if self._hover_bg:
            try:
                self.delete(self._hover_bg)
            except tk.TclError:
                pass
            self._hover_bg = None
        if key in self._row_text:
            self.itemconfigure(self._row_text[key], fill=theme.SIDEBAR_FG)

    def _apply_hover(self, key: str) -> None:
        """画 hover 底色块（垫到渐变上、文字下）并把该行文字提亮。"""
        g = self._grid()
        w = self.winfo_width()
        y, row_h = self._row_geo[key]
        pts = paint.round_points(
            g["inset"], y, w - g["inset"], y + row_h - 4, _px(9),
        )
        self._hover_bg = self.create_polygon(*pts, fill=theme.SIDEBAR_ACTIVE_BG,
                                             outline="", smooth=True)
        # 色块垫在渐变上，但该行文字必须浮在色块上
        if key in self._row_text:
            self.tag_raise(self._row_text[key], self._hover_bg)
        if key in self._row_text:
            self.itemconfigure(self._row_text[key], fill=theme.SIDEBAR_HOVER_FG)

    def _paint_indicator(self) -> None:
        """主色小条落当前行。整块重画（hover/缩放）一律静态落位——弹簧值
        可能是旧几何的，落错行比丢一次动画更糟；动画只在 set_current 播。"""
        geo = self._row_geo.get(self._current)
        if not geo:
            return
        self._indicator_y.jump(geo[0])  # jump 内部会调 _place_indicator

    def _place_indicator(self, y: float) -> None:
        """画/重画指示条。弹簧飞行时每帧删旧画新，不拖影。"""
        geo = self._row_geo.get(self._current)
        if not geo:
            return
        if self._indicator_item:
            try:
                self.delete(self._indicator_item)
            except tk.TclError:
                pass
        row_y, row_h = geo
        pts = paint.round_points(
            _px(14), y + _px(9), _px(17), y + row_h - _px(13), _px(2),
        )
        self._indicator_item = self.create_polygon(
            *pts, fill=theme.SIDEBAR_INDICATOR, outline="", smooth=True,
        )
