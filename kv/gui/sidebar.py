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

    def __init__(self, parent: tk.Widget, *, on_navigate: Callable[[str], None],
                 on_palette: Callable[[], None] | None = None) -> None:
        super().__init__(
            parent, width=theme.SIDEBAR_WIDTH, highlightthickness=0, bd=0,
            bg=theme.SIDEBAR_TOP,
        )
        self._on_navigate = on_navigate
        self._on_palette = on_palette
        self._current = ""
        self._hover_key = ""
        self._cursor = "arrow"
        self._hover_bg: str | None = None
        self._hover_gen = 0
        self._row_text: dict[str, str] = {}
        self._row_geo: dict[str, tuple[int, int]] = {}
        self._row_base: dict[str, str] = {}   # 各行渐变底色（hover 淡入的起点）
        self._indicator_y = motion.Spring(self, self._place_indicator)
        self._indicator_item: str | None = None
        self._paint_job: str | None = None
        # 行命中是 Canvas 级 hit-test（_hit_row），不是 tag_bind——tag 只能挂
        # 在文字 item 上，图标与行的空白区域会点不动。Configure/缩放的重绘
        # 全部经 _schedule_paint 合帧：拖窗时每帧至多画一次，不逐事件全量重绘。
        self.bind("<Configure>", lambda _e: self._schedule_paint())
        theme.LISTENERS.append(self._schedule_paint)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _e: (self._set_hover(""), self._apply_cursor("arrow")))
        self.bind("<Destroy>", lambda _e: self._detach_listeners())
        self._sync_width()

    def _detach_listeners(self) -> None:
        """销毁时摘掉全局缩放监听——否则级联会打在已销毁控件上。"""
        for cb in (self._schedule_paint,):
            if cb in theme.LISTENERS:
                theme.LISTENERS.remove(cb)

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

    def _schedule_paint(self) -> None:
        """重绘请求合帧：同一批事件（Configure 连发 / 缩放级联）只画一次。"""
        if self._paint_job is None:
            self._paint_job = self.after_idle(self._do_paint)

    def _do_paint(self) -> None:
        self._paint_job = None
        try:
            self._sync_width()
            self._paint()
        except tk.TclError:
            pass  # 窗口已销毁，排队回调随窗口终止

    def _sync_width(self) -> None:
        desired = _px(252)
        if abs(self.winfo_reqwidth() - desired) > 2:
            self.configure(width=desired)

    def _grid(self) -> dict:
        icon_size = _px(20)
        icon_x = _px(26)
        row_h = _px(42)
        row_gap = _px(4)  # 药丸底部留白：必须随缩放，否则高缩放下行槽与内容错位
        return {
            "margin": _px(16),
            "inset": _px(10),
            "icon_size": icon_size,
            "icon_x": icon_x,
            "icon_cx": icon_x + icon_size // 2,
            "text_x": icon_x + icon_size + _px(16),
            "row_h": row_h,
            "row_gap": row_gap,
            "row_cy": (row_h - row_gap) / 2,  # 药丸中心 = 图标与文字的垂直居中基准
            "section_h": _px(32),
            "group_gap": _px(12),
        }

    def _paint(self) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10 or h < 10:
            return
        content_h = max(h, self.winfo_reqheight())
        # 64 带：两端色差只有 ~20/255，带间差 <0.35，再密肉眼不可辨——
        # 渐变是全量重绘里 item 数的大头，带数直接决定拖窗/缩放的每帧成本
        paint.draw_v_gradient(self, theme.SIDEBAR_TOP, theme.SIDEBAR_BOTTOM,
                              w, content_h, steps=64)
        self._row_geo.clear()
        self._row_text.clear()
        self._row_base.clear()
        self._hover_bg = None
        self._hover_key = ""
        self._hover_gen += 1  # 整块重画杀掉在飞的 hover 过渡
        self._paint_brand()
        nav_end = self._paint_nav(w, content_h)
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
        # 行高盒相接即可——linespace 自带内边距，字形之间仍留有呼吸空隙
        sub_cy = cy + f_title.metrics("linespace") // 2 + f_sub.metrics("linespace") // 2
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

    def _paint_nav(self, w: int, content_h: int) -> int:
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
                        g["inset"], y, w - g["inset"], y + g["row_h"] - g["row_gap"],
                        _px(9),
                    )
                    self.create_polygon(*pts, fill=bg, outline="", smooth=True)
                # 该行中心的渐变底色：hover 淡入从「和背景无缝」起步
                self._row_base[key] = motion.lerp_color(
                    theme.SIDEBAR_TOP, theme.SIDEBAR_BOTTOM,
                    min((y + g["row_cy"]) / max(content_h, 1), 1.0),
                )
                cy = round(y + g["row_cy"])  # 图标与文字都落药丸中心——任何缩放档位居中
                n0 = len(self.find_all())
                icons.ICONS[key](self, g["icon_cx"], cy, g["icon_size"], fg)
                for iid in range(n0 + 1, len(self.find_all()) + 1):
                    self.addtag(f"icon-{key}", "withtag", iid)  # hover 时要把图标抬到色块上
                txt_id = self.create_text(
                    g["text_x"], cy, text=label, anchor="w",
                    fill=fg, font=theme.FONT,
                )
                self._row_text[key] = txt_id
                self._row_geo[key] = (y, g["row_h"])
                y += g["row_h"]
            y += g["group_gap"]
        return y

    # ------------------------------------------------------------ 行命中

    def _hit_row(self, y: float) -> str:
        """内容坐标 y 落在哪一行（覆盖图标与整条空白，不只文字）。"""
        for key, (row_y, row_h) in self._row_geo.items():
            if row_y <= y < row_y + row_h:
                return key
        return ""

    def _on_click(self, event: tk.Event) -> None:
        key = self._hit_row(self.canvasy(event.y))
        if key:
            self._on_navigate(key)

    def _on_motion(self, event: tk.Event) -> None:
        key = self._hit_row(self.canvasy(event.y))
        over_hint = bool(self._on_palette) and "palette-hint" in self.gettags("current")
        self._set_hover(key)
        self._apply_cursor("hand2" if (key or over_hint) else "arrow")

    def _apply_cursor(self, want: str) -> None:
        """光标单源：值不变不动 Tk（Motion 是最高频事件）。"""
        if want != self._cursor:
            self._cursor = want
            self.configure(cursor=want)

    def _paint_footer(self, w: int, y: int) -> None:
        """在 y（视口底与导航底取更靠下者）画页脚：分隔线 + Ctrl+K 提示 + 版本。"""
        g = self._grid()
        x = g["icon_x"]
        self.create_line(_px(16), y, w - _px(16), y, fill=theme.SIDEBAR_HAIRLINE, width=1)
        # Ctrl+K 提示：圆角键帽胶囊 + 键位文字。接了 on_palette 就整条可点——
        # 「可点的视觉暗示」要么可点，要么不画成暗示
        label = "Ctrl"
        f = tkfont.Font(font=theme.FONT_SMALL)
        lw = f.measure(label)
        hint_tags = ("palette-hint",) if self._on_palette else ()
        pts = paint.round_points(
            x, y + _px(12), x + lw + _px(12), y + _px(30), _px(4),
        )
        self.create_polygon(*pts, fill="", outline=theme.SIDEBAR_MUTED,
                            width=1, smooth=True, tags=hint_tags)
        self.create_text(
            x + lw / 2 + _px(6), y + _px(21), text=label,
            fill=theme.SIDEBAR_MUTED, font=theme.FONT_SMALL, tags=hint_tags,
        )
        self.create_text(
            x + lw + _px(20), y + _px(21), text="+ K  快速复制密钥",
            anchor="w", fill=theme.SIDEBAR_MUTED, font=theme.FONT_SMALL,
            tags=hint_tags,
        )
        if self._on_palette:
            self.tag_bind("palette-hint", "<Button-1>",
                          lambda _e: self._on_palette())
        self.create_text(
            x, y + _px(46), text=f"kv {__version__} · 数据不出本机",
            anchor="w", fill=theme.SIDEBAR_SECTION, font=theme.FONT_SUBTITLE,
        )

    def _set_hover(self, key: str) -> None:
        """hover 淡入淡出（4 帧 ~64ms）——行 hover 是高频交互走快档，
        与按钮的 8 帧同语言但更急促。底色从该行渐变色起步，与背景无缝。
        光标不在这里管（_on_motion 统一裁决）。"""
        if key == self._hover_key:
            return
        self._hover_gen += 1
        gen = self._hover_gen
        old, self._hover_key = self._hover_key, key
        if old and old != self._current and old in self._row_geo:
            self._fade_row(old, self._hover_bg, enter=False, gen=gen)
            self._hover_bg = None
        if key and key != self._current and key in self._row_geo:
            self._hover_bg = self._hover_pill(key)
            # 色块创建在最顶层，图标与文字必须抬回它之上（旧实现漏抬图标，
            # hover 时图标被色块盖住）
            self.tag_raise(f"icon-{key}", self._hover_bg)
            if key in self._row_text:
                self.tag_raise(self._row_text[key], self._hover_bg)
            self._fade_row(key, self._hover_bg, enter=True, gen=gen)

    def _hover_pill(self, key: str) -> str:
        """hover 底色块（从该行渐变色起步），垫在渐变上、文字下。"""
        g = self._grid()
        w = self.winfo_width()
        y, row_h = self._row_geo[key]
        pts = paint.round_points(
            g["inset"], y, w - g["inset"], y + row_h - g["row_gap"], _px(9),
        )
        return self.create_polygon(
            *pts, fill=self._row_base.get(key, theme.SIDEBAR_TOP),
            outline="", smooth=True,
        )

    def _fade_row(self, key: str, polygon: str | None, *, enter: bool,
                  gen: int) -> None:
        """行 hover 的底色与文字色同步过渡；期间 hover 变了/重画了就停手。"""
        if polygon and not enter:
            try:
                src = self.itemcget(polygon, "fill")  # 从当前色续变，不跳变
            except tk.TclError:
                return
        else:
            src = self._row_base.get(key, theme.SIDEBAR_TOP)
        fg_from, fg_to = (
            (theme.SIDEBAR_FG, theme.SIDEBAR_HOVER_FG)
            if enter else (theme.SIDEBAR_HOVER_FG, theme.SIDEBAR_FG)
        )

        def tick(step: int) -> None:
            if gen != self._hover_gen:
                return
            t = step / 4
            try:
                if polygon:
                    self.itemconfigure(polygon, fill=motion.lerp_color(
                        src, theme.SIDEBAR_ACTIVE_BG, t if enter else 1 - t))
                if key in self._row_text:
                    self.itemconfigure(self._row_text[key], fill=motion.lerp_color(
                        fg_from, fg_to, t))
            except tk.TclError:
                return  # 控件已销毁
            if step < 4:
                self.after(16, tick, step + 1)
            elif not enter and polygon:
                try:
                    self.delete(polygon)
                except tk.TclError:
                    pass

        tick(0)

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
