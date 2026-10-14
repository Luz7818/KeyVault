"""画布绘图原语：圆角矩形顶点与垂直渐变。

Card / RoundEntry / TabBar pill / SlimScrollbar 共用的几何与底纹都在这里，
controls.py 只组装控件不画形状。tkinter 里只有 Canvas 能画圆角与渐变——
这是全部「质感」的物理基础。
"""

from __future__ import annotations

import tkinter as tk

from kv.gui import motion


def round_points(x0: int, y0: int, x1: int, y1: int, r: int) -> list[tuple[float, float]]:
    """圆角矩形的 polygon 顶点（smooth=True 渲染成圆角）。"""
    r = max(0, min(r, (x1 - x0) // 2, (y1 - y0) // 2))
    return [
        (x0 + r, y0), (x1 - r, y0), (x1, y0), (x1, y0 + r),
        (x1, y1 - r), (x1, y1), (x1 - r, y1), (x0 + r, y1),
        (x0, y1), (x0, y1 - r), (x0, y0 + r), (x0, y0),
    ]


def draw_v_gradient(canvas: tk.Canvas, top: str, bottom: str,
                    width: int, height: int, *, steps: int = 96) -> None:
    """整个画布铺一层垂直渐变（逐行色带，steps 越大越平滑）。"""
    canvas.delete("all")
    if width <= 0 or height <= 0:
        return
    band = max(height / steps, 1)
    for i in range(steps):
        y0 = round(i * band)
        y1 = min(round((i + 1) * band) + 1, height)
        if y0 >= height:
            break
        color = motion.lerp_color(top, bottom, i / (steps - 1))
        canvas.create_rectangle(0, y0, width, y1, fill=color, outline="", width=0)
