"""自绘矢量图标（Canvas 线条画）。

不依赖任何图标字体——tkinter 渲染系统字形跨版本不可控，Canvas 线条
可控、锐利、颜色随选中态实时切换。每个函数在给定 canvas 上以 (cx, cy)
为中心画一个 size 见方的图标，stroke 是线条色，返回无。
"""

from __future__ import annotations

import tkinter as tk

WIDTH = 2  # 线宽随 scale 的比例基准


def _p(v: float, size: int) -> float:
    """-8..8 设计网格坐标 → 实际像素偏移。"""
    return v * size / 16


def draw_key(c: tk.Canvas, cx: float, cy: float, size: int, color: str) -> None:
    """钥匙：圆环 + 斜柄 + 两齿。"""
    r = _p(3.4, size)
    hx, hy = cx + _p(-4, size), cy + _p(4, size)
    c.create_oval(
        hx - r, hy - r, hx + r, hy + r, outline=color, width=WIDTH,
    )
    c.create_line(
        cx + _p(-1.5, size), cy + _p(1.5, size),
        cx + _p(6, size), cy + _p(-6, size),
        fill=color, width=WIDTH, capstyle="round",
    )
    c.create_line(
        cx + _p(3.2, size), cy + _p(-3.2, size),
        cx + _p(5.4, size), cy + _p(-1.0, size),
        fill=color, width=WIDTH, capstyle="round",
    )


def draw_search(c: tk.Canvas, cx: float, cy: float, size: int, color: str) -> None:
    """放大镜。"""
    r = _p(5, size)
    ox, oy = cx + _p(-1.5, size), cy + _p(-1.5, size)
    c.create_oval(ox - r, oy - r, ox + r, oy + r, outline=color, width=WIDTH)
    c.create_line(
        ox + _p(3.8, size), oy + _p(3.8, size),
        cx + _p(6.5, size), cy + _p(6.5, size),
        fill=color, width=WIDTH, capstyle="round",
    )


def draw_bolt(c: tk.Canvas, cx: float, cy: float, size: int, color: str) -> None:
    """闪电（填充多边形）。"""
    pts = [
        (-1, -7), (-6, 1), (-1.5, 1), (-2.5, 7), (5, -2), (0.5, -2), (3, -7),
    ]
    flat = []
    for vx, vy in pts:
        flat.extend([cx + _p(vx, size), cy + _p(vy, size)])
    c.create_polygon(*flat, fill=color, smooth=True)


def draw_layers(c: tk.Canvas, cx: float, cy: float, size: int, color: str) -> None:
    """批量：三条圆头横线（列表）。"""
    for i, vy in enumerate((-4.5, 0, 4.5)):
        c.create_line(
            cx + _p(-6, size), cy + _p(vy, size),
            cx + _p(6, size), cy + _p(vy, size),
            fill=color, width=WIDTH, capstyle="round",
        )


def draw_shield(c: tk.Canvas, cx: float, cy: float, size: int, color: str) -> None:
    """盾牌。"""
    pts = [
        (0, -7), (6, -4.5), (6, 1.5), (3, 5.5), (0, 7), (-3, 5.5), (-6, 1.5), (-6, -4.5),
    ]
    flat = []
    for vx, vy in pts:
        flat.extend([cx + _p(vx, size), cy + _p(vy, size)])
    c.create_polygon(
        *flat, outline=color, width=WIDTH, smooth=True, fill="",
    )


def draw_sliders(c: tk.Canvas, cx: float, cy: float, size: int, color: str) -> None:
    """设置：三条滑杆线各带一个圆点（比齿轮更现代）。"""
    rows = [(-4.5, 2.5), (0, -3), (4.5, 3.5)]
    for vy, knob in rows:
        y = cy + _p(vy, size)
        c.create_line(
            cx + _p(-6, size), y, cx + _p(6, size), y,
            fill=color, width=WIDTH, capstyle="round",
        )
        kx = cx + _p(knob, size)
        r = _p(2.2, size)
        c.create_oval(kx - r, y - r, kx + r, y + r, fill=color, outline=color)


ICONS: dict[str, callable] = {
    "secrets": draw_key,
    "detect": draw_search,
    "capture": draw_bolt,
    "ingest": draw_layers,
    "security": draw_shield,
    "settings": draw_sliders,
}
