"""GUI 主题：设计令牌 + DPI 缩放 + ttk 样式注册。

颜色、字体、间距集中在这里，页面模块只引用令牌不硬编码。
init(scale) 在创建任何窗口前调用（kv_gui.py 入口按显示器 DPI 算好 scale），
把字体换成像素字号、几何常量按比例放大——不感知 DPI 的位图拉伸是文字
发虚的根因，感知之后全部尺寸必须自己缩放。
setup_style() 把 ttk 基底切到 clam（Windows 默认 vista 不吃颜色配置），
再按令牌注册全部组件样式。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# ---------------------------------------------------------------- 色板
BG = "#f4f5f7"            # 窗口衬底（对话框、边缘）
SURFACE = "#ffffff"       # 内容区纸面
CONTENT_BG = SURFACE      # 兼容旧引用：内容区背景
BORDER = "#e4e7ee"        # 1px 描边
BORDER_STRONG = "#cbd2de"
TABLE_HEADER_BG = "#f8f9fb"
TABLE_ALT_BG = "#fafbfd"  # 斑马纹
TABLE_SELECT_BG = "#eef2ff"
HOVER_BG = "#f1f3f9"
PRESS_BG = "#e6eaf4"

# 主色与语义色
ACCENT = "#3d5fe8"
ACCENT_HOVER = "#2f4fd6"
ACCENT_ACTIVE = "#2743b8"
ACCENT_SOFT = "#eef2ff"
ERROR = "#d92d20"
ERROR_SOFT = "#fee4e2"
WARNING = "#d97706"
SUCCESS = "#12805c"

TEXT = "#1f2430"
TEXT_SECONDARY = "#667085"
TEXT_TERTIARY = "#98a2b3"

# 侧边栏（深墨蓝）
SIDEBAR_BG = "#141821"
SIDEBAR_FG = "#9aa4b8"
SIDEBAR_HOVER_FG = "#c9d2e3"
SIDEBAR_ACTIVE_BG = "#222a3a"
SIDEBAR_ACTIVE_FG = "#ffffff"
SIDEBAR_INDICATOR = ACCENT
SIDEBAR_MUTED = "#5d6575"

# ---------------------------------------------------------------- 字体
FONT_FAMILY = "Microsoft YaHei UI"
MONO_FAMILY = "Cascadia Mono"   # 不可用时 init() 会退回 Consolas
FONT = (FONT_FAMILY, 10)
FONT_BOLD = (FONT_FAMILY, 10, "bold")
FONT_HEADING = (FONT_FAMILY, 16, "bold")
FONT_SUBTITLE = (FONT_FAMILY, 9)
FONT_SMALL = (FONT_FAMILY, 9)
FONT_TABLE = (FONT_FAMILY, 10)
FONT_MONO = (MONO_FAMILY, 10)

# ---------------------------------------------------------------- 尺寸
SCALE = 1.0
PAD = 16
PAD_SM = 8
PAD_LG = 24
GAP = 10
SIDEBAR_WIDTH = 208
STATUS_HEIGHT = 30
ROW_HEIGHT = 38
CONTROL_HEIGHT = 38
RADIUS = 10

# 像素字号基线（scale=1 时），init() 按显示器放大
_BODY_PX = 15
_SMALL_PX = 13
_HEADING_PX = 24
_MONO_PX = 15


def init(scale: float) -> None:
    """按 DPI 缩放系数重建字体与几何令牌。创建窗口前调用一次。"""
    global SCALE, PAD, PAD_SM, PAD_LG, GAP, SIDEBAR_WIDTH, STATUS_HEIGHT
    global ROW_HEIGHT, CONTROL_HEIGHT, RADIUS
    global FONT, FONT_BOLD, FONT_HEADING, FONT_SUBTITLE, FONT_SMALL
    global FONT_TABLE, FONT_MONO, MONO_FAMILY

    SCALE = scale
    PAD = _s(16)
    PAD_SM = _s(8)
    PAD_LG = _s(24)
    GAP = _s(10)
    SIDEBAR_WIDTH = _s(208)
    STATUS_HEIGHT = _s(30)
    ROW_HEIGHT = _s(32)
    CONTROL_HEIGHT = _s(34)
    RADIUS = _s(9)

    FONT = (FONT_FAMILY, -_s(_BODY_PX))
    FONT_BOLD = (FONT_FAMILY, -_s(_BODY_PX), "bold")
    FONT_HEADING = (FONT_FAMILY, -_s(_HEADING_PX), "bold")
    FONT_SUBTITLE = (FONT_FAMILY, -_s(_SMALL_PX))
    FONT_SMALL = (FONT_FAMILY, -_s(_SMALL_PX))
    FONT_TABLE = (FONT_FAMILY, -_s(_BODY_PX))
    FONT_MONO = (MONO_FAMILY, -_s(_MONO_PX))


def _s(px: int) -> int:
    return max(round(px * SCALE), 1)


def _resolve_mono_family() -> None:
    """Cascadia Mono 存在就用它（更现代的等宽），否则退回 Consolas。"""
    global MONO_FAMILY
    import tkinter.font as tkfont

    families = set(tkfont.families())
    for candidate in ("Cascadia Mono", "Cascadia Code"):
        if candidate in families:
            MONO_FAMILY = candidate
            return


def flat(widget: tk.Widget) -> None:
    """给原生 tk.Text/Listbox 统一扁平描边：1px 灰框，聚焦变主色。"""
    widget.configure(
        relief="flat",
        highlightthickness=1,
        highlightbackground=BORDER,
        highlightcolor=ACCENT,
        bd=0,
    )


def setup_style() -> None:
    """切到 clam 基底并按令牌注册全部组件样式。进 GUI 先调这个。"""
    global FONT_MONO
    _resolve_mono_family()
    FONT_MONO = (MONO_FAMILY, -_s(_MONO_PX))
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass  # 环境没有 clam 时退回默认，样式尽力而为
    _base(style)
    _buttons(style)
    _inputs(style)
    _notebook(style)
    _tree_and_scrollbars(style)
    _cards_and_choices(style)


def _base(style: ttk.Style) -> None:
    """全局默认与文本层级。"""
    style.configure(
        ".",
        background=SURFACE,
        foreground=TEXT,
        font=FONT,
        bordercolor=BORDER,
        focuscolor=ACCENT,
    )
    # 框架：内容区是一张白纸
    style.configure("TFrame", background=SURFACE)
    style.configure("Muted.TFrame", background=BG)
    # 文本层级
    style.configure("TLabel", background=SURFACE, foreground=TEXT)
    style.configure("Heading.TLabel", font=FONT_HEADING, foreground=TEXT)
    style.configure("Sub.TLabel", font=FONT_SUBTITLE, foreground=TEXT_SECONDARY)
    style.configure("Muted.TLabel", foreground=TEXT_TERTIARY)
    style.configure("Muted.TFrame TLabel", background=BG)
    style.configure("Accent.TLabel", foreground=ACCENT)


def _buttons(style: ttk.Style) -> None:
    """ttk 按钮兜底样式；页面主路径已换自绘 RoundButton。"""
    style.configure(
        "TButton",
        background=SURFACE,
        foreground=TEXT,
        bordercolor=BORDER_STRONG,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        relief="flat",
        focusthickness=1,
        focuscolor=ACCENT,
        padding=(14, 6),
        font=FONT,
    )
    style.map("TButton", background=[("active", HOVER_BG)])
    style.configure("Danger.TButton", foreground=ERROR)
    style.map("Danger.TButton", foreground=[("active", ERROR)])


def _inputs(style: ttk.Style) -> None:
    """输入类：1px 灰框，聚焦变主色。"""
    style.configure(
        "TEntry",
        fieldbackground=SURFACE,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        insertcolor=TEXT,
        padding=(8, 5),
        font=FONT,
    )
    style.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)])
    style.configure(
        "TCombobox",
        fieldbackground=SURFACE,
        background=HOVER_BG,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        arrowcolor=TEXT_SECONDARY,
        padding=(6, 4),
        font=FONT,
    )
    style.map(
        "TCombobox",
        bordercolor=[("focus", ACCENT)],
        fieldbackground=[("readonly", SURFACE)],
        arrowcolor=[("active", TEXT)],
    )


def _notebook(style: ttk.Style) -> None:
    """标签页：扁平，选中的那张"纸"主色文字。"""
    style.configure(
        "TNotebook",
        background=SURFACE,
        bordercolor=SURFACE,
        lightcolor=SURFACE,
        tabmargins=(0, 4, 0, 0),
    )
    style.configure(
        "TNotebook.Tab",
        background=SURFACE,
        foreground=TEXT_SECONDARY,
        padding=(16, 8),
        font=FONT,
        bordercolor=SURFACE,
        lightcolor=SURFACE,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", SURFACE)],
        foreground=[("selected", ACCENT), ("active", TEXT)],
    )


def _tree_and_scrollbars(style: ttk.Style) -> None:
    """表格与滚动条：行高、去边框、扁平表头、主色浅底选中。"""
    style.configure(
        "Treeview",
        background=SURFACE,
        fieldbackground=SURFACE,
        foreground=TEXT,
        rowheight=ROW_HEIGHT,
        bordercolor=BORDER,
        borderwidth=1,
        relief="flat",
        font=FONT_TABLE,
    )
    style.map(
        "Treeview",
        background=[("selected", TABLE_SELECT_BG)],
        foreground=[("selected", TEXT)],
    )
    style.configure(
        "Treeview.Heading",
        background=TABLE_HEADER_BG,
        foreground=TEXT_SECONDARY,
        font=FONT_SUBTITLE,
        relief="flat",
        bordercolor=SURFACE,
        padding=(8, 7),
    )
    style.map("Treeview.Heading", background=[("active", TABLE_HEADER_BG)])
    for orient in ("Vertical", "Horizontal"):
        style.configure(
            f"{orient}.TScrollbar",
            background="#e9ecf2",
            troughcolor=SURFACE,
            bordercolor=SURFACE,
            arrowcolor=TEXT_TERTIARY,
            relief="flat",
        )
        style.map(
            f"{orient}.TScrollbar",
            background=[("active", "#d9dee8"), ("pressed", "#c9d0dd")],
        )
    style.configure("TSeparator", background=BORDER)


def _cards_and_choices(style: ttk.Style) -> None:
    """LabelFrame 卡片与单选/复选。"""
    style.configure(
        "TLabelframe",
        background=SURFACE,
        bordercolor=BORDER,
        relief="flat",
        borderwidth=1,
    )
    style.configure(
        "TLabelframe.Label",
        background=SURFACE,
        foreground=TEXT_SECONDARY,
        font=FONT_SUBTITLE,
    )
    style.configure("TRadiobutton", background=SURFACE, focuscolor=ACCENT, font=FONT)
    style.map(
        "TRadiobutton",
        background=[("active", SURFACE)],
        indicatorcolor=[("selected", ACCENT)],
    )
    style.configure("TCheckbutton", background=SURFACE, focuscolor=ACCENT, font=FONT)
    style.map(
        "TCheckbutton",
        background=[("active", SURFACE)],
        indicatorcolor=[("selected", ACCENT)],
    )
