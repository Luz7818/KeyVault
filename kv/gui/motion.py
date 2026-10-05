"""动效原语：弹簧与颜色插值。

原则来自流体界面的通行做法——动画从当前值出发、可随时重定向（速度延续，
不硬切）、临界阻尼为主（不弹跳）。tkinter 没有 rAF，用 after(16) 当帧时钟；
动画只碰 place/颜色这类轻量属性，不触发几何重排，60fps 稳。
"""

from __future__ import annotations

import tkinter as tk
from math import pi, sqrt


class Spring:
    """一维弹簧。to() 可中途重定向，速度自动延续——重定向不产生跳变。

    apply(value) 在每一帧被调；widget 销毁后循环自动停。
    """

    def __init__(
        self, widget: tk.Widget, apply, *,
        damping: float = 1.0, response: float = 0.22,
    ) -> None:
        self._widget = widget
        self._apply = apply
        self._k = (2 * pi / response) ** 2
        self._c = 2 * damping * sqrt(self._k)
        self._x = 0.0
        self._v = 0.0
        self._target = 0.0
        self._job: str | None = None

    def jump(self, value: float) -> None:
        """瞬移到位（初始化场景），不产生动画。"""
        self._x = self._target = value
        self._v = 0.0
        self._apply(value)

    def to(self, target: float) -> None:
        self._target = target
        if self._job is None:
            self._job = self._widget.after(16, self._tick)

    @property
    def value(self) -> float:
        return self._x

    def _tick(self) -> None:
        self._job = None
        dt = 1 / 60
        accel = self._k * (self._target - self._x) - self._c * self._v
        self._v += accel * dt
        self._x += self._v * dt
        settled = abs(self._x - self._target) < 0.4 and abs(self._v) < 8
        if settled:
            self._x = self._target
        try:
            self._apply(self._x)
        except tk.TclError:  # 窗口已销毁
            return
        if settled:
            return
        self._job = self._widget.after(16, self._tick)


def lerp_color(a: str, b: str, t: float) -> str:
    """hex 颜色线性插值。t=0 得 a，t=1 得 b。"""
    ta = _parse(a)
    tb = _parse(b)
    mixed = tuple(round(x + (y - x) * t) for x, y in zip(ta, tb))
    return "#%02x%02x%02x" % mixed


def _parse(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
