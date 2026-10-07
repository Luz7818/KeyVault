"""自绘矢量图标（Canvas 线条画）。

图标语言：圆头线条、双层线宽（主线 1.8 / 细节 1.4）、关键处用实心点缀，
参考成熟密码管理工具的几何极简风格。不依赖任何图标字体——tkinter 渲染
系统字形跨版本不可控，Canvas 线条可控、锐利、颜色随选中态实时切换。

每个函数在给定 canvas 上以 (cx, cy) 为中心画一个 size 见方的图标，
stroke 是线条色。设计网格为 -8..8，_p 换算到实际像素。
"""

from __future__ import annotations

import tkinter as tk

W_MAIN = 1.9   # 主线宽（视觉基准，实际随 size 缩放）
W_DETAIL = 1.4


def _p(v: float, size: int) -> float:
    """-8..8 设计网格坐标 → 实际像素偏移。"""
    return v * size / 16


def _line(c: tk.Canvas, x0: float, y0: float, x1: float, y1: float,
          color: str, w: float = W_MAIN) -> None:
    c.create_line(x0, y0, x1, y1, fill=color, width=w, capstyle="round")


def _dot(c: tk.Canvas, cx: float, cy: float, r: float, color: str) -> None:
    c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline=color)


def draw_key(c: tk.Canvas, cx: float, cy: float, size: int, color: str) -> None:
    """斜 45° 圆头钥匙：圆环 + 环心实心点 + 柄 + 单齿。"""
    r = _p(3.2, size)
    hx, hy = cx + _p(-4.1, size), cy + _p(4.1, size)
    c.create_oval(hx - r, hy - r, hx + r, hy + r, outline=color, width=W_MAIN)
    _dot(c, hx, hy, _p(1.1, size), color)
    edge = r / 1.414
    tip_x, tip_y = cx + _p(6.4, size), cy + _p(-6.4, size)
    _line(c, hx + edge, hy - edge, tip_x, tip_y, color)
    # 齿：柄 3/4 处向右下的短垂线
    mx, my = cx + _p(3.2, size), cy + _p(-3.2, size)
    _line(c, mx, my, cx + _p(5.4, size), cy + _p(-1.0, size), color, W_DETAIL)


def draw_search(c: tk.Canvas, cx: float, cy: float, size: int, color: str) -> None:
    """放大镜：圆环 + 柄 + 镜片内一道高光弧。"""
    r = _p(4.8, size)
    ox, oy = cx + _p(-1.6, size), cy + _p(-1.6, size)
    c.create_oval(ox - r, oy - r, ox + r, oy + r, outline=color, width=W_MAIN)
    _line(
        c, ox + _p(3.6, size), oy + _p(3.6, size),
        cx + _p(6.6, size), cy + _p(6.6, size), color,
    )
    # 高光弧：左上内侧的一段细弧，模拟玻璃反光
    c.create_arc(
        ox - r * 0.62, oy - r * 0.62, ox + r * 0.62, oy + r * 0.62,
        start=95, extent=70, style="arc", outline=color, width=W_DETAIL,
    )


def draw_bolt(c: tk.Canvas, cx: float, cy: float, size: int, color: str) -> None:
    """闪电：圆角填充多边形。"""
    pts = [
        (-0.6, -7.2), (-6.4, 0.8), (-1.8, 0.8), (-2.8, 7.2),
        (6.2, -1.2), (1.2, -1.2), (3.4, -7.2),
    ]
    flat: list[float] = []
    for vx, vy in pts:
        flat.extend([cx + _p(vx, size), cy + _p(vy, size)])
    c.create_polygon(*flat, fill=color, smooth=True)


def draw_layers(c: tk.Canvas, cx: float, cy: float, size: int, color: str) -> None:
    """批量：三层菱形堆叠（现代 Stack 图标）。"""
    top = [(0, -6.5), (6, -3), (0, 0.5), (-6, -3)]
    flat: list[float] = []
    for vx, vy in top:
        flat.extend([cx + _p(vx, size), cy + _p(vy, size)])
    c.create_polygon(*flat, outline=color, width=W_MAIN, smooth=True, fill="")
    for dy in (4.6, 8.2):
        _line(c, cx + _p(-6, size), cy + _p(dy - 3.5, size),
              cx, cy + _p(dy, size), color)
        _line(c, cx, cy + _p(dy, size),
              cx + _p(6, size), cy + _p(dy - 3.5, size), color)


def draw_shield(c: tk.Canvas, cx: float, cy: float, size: int, color: str) -> None:
    """安全：盾牌 + 内部对勾。"""
    pts = [
        (0, -7), (6, -4.5), (6, 1.2), (3.2, 5.4), (0, 7),
        (-3.2, 5.4), (-6, 1.2), (-6, -4.5),
    ]
    flat: list[float] = []
    for vx, vy in pts:
        flat.extend([cx + _p(vx, size), cy + _p(vy, size)])
    c.create_polygon(*flat, outline=color, width=W_MAIN, smooth=True, fill="")
    _line(c, cx + _p(-2.6, size), cy + _p(0.2, size),
          cx + _p(-0.6, size), cy + _p(2.6, size), color)
    _line(c, cx + _p(-0.6, size), cy + _p(2.6, size),
          cx + _p(3.4, size), cy + _p(-2.4, size), color)


def draw_sliders(c: tk.Canvas, cx: float, cy: float, size: int, color: str) -> None:
    """设置：两条滑杆线 + 错位空心旋钮，比齿轮更轻盈。"""
    rows = [(-3.2, 2.6), (3.2, -2.6)]
    for vy, knob in rows:
        y = cy + _p(vy, size)
        _line(c, cx + _p(-6.2, size), y, cx + _p(6.2, size), y, color)
        kx = cx + _p(knob, size)
        kr = _p(2.4, size)
        c.create_oval(kx - kr, y - kr, kx + kr, y + kr, fill=color, outline=color)


def draw_logo(c: tk.Canvas, cx: float, cy: float, size: int, color: str,
              hole: str) -> None:
    """品牌标志：圆角盾 + 中心钥匙孔（孔色与盾色分开，层级更清晰）。"""
    pts = [
        (0, -7.4), (6.6, -4.6), (6.6, 1.4), (3.4, 5.8), (0, 7.4),
        (-3.4, 5.8), (-6.6, 1.4), (-6.6, -4.6),
    ]
    flat: list[float] = []
    for vx, vy in pts:
        flat.extend([cx + _p(vx, size), cy + _p(vy, size)])
    c.create_polygon(*flat, outline=color, width=2.0, smooth=True, fill="")
    hr = _p(2.0, size)
    hy = cy + _p(-1.2, size)
    c.create_oval(cx - hr, hy - hr, cx + hr, hy + hr, fill=hole, outline=hole)
    _line(c, cx, hy + hr * 0.4, cx, cy + _p(3.6, size), hole, 2.2)


ICONS: dict[str, callable] = {
    "secrets": draw_key,
    "detect": draw_search,
    "capture": draw_bolt,
    "ingest": draw_layers,
    "security": draw_shield,
    "settings": draw_sliders,
}
