"""动效原语：帧时钟、缓动、弹簧、颜色插值、窗口淡入。

设计令牌单源在这里（页面模块只引用不硬编码），口径来自通行动效规则：
- 频率闸门：100+ 次/天（快捷键/命令面板）永不动画；数十次/天只做
  near-imperceptible 的快档（≤64ms）；偶尔出现的表面（模态框/气泡）才
  用标准动画；稀有/首见（空态）才花惊喜预算。
- 时长预算：按压反馈 100-160ms、气泡 125-200ms、模态 200-500ms，
  UI 硬上限 300ms。tkinter 没有 rAF，after(FRAME_MS) 当 60fps 帧时钟。
- 曲线：入场/出场一律缓出（ease-out），屏内移动用弹簧；UI 永不 ease-in。
动画只碰 place/颜色这类轻量属性，不触发几何重排，60fps 稳。
"""

from __future__ import annotations

import tkinter as tk
from math import pi, sqrt

FRAME_MS = 16  # 帧时钟（≈60fps）。测试把帧时钟注入成 1ms 即可确定性收敛


def ease_out(t: float) -> float:
    """二次缓出：起步快、收尾慢——入场/淡入的标准曲线（UI 永不用 ease-in）。"""
    t = min(max(t, 0.0), 1.0)
    return 1 - (1 - t) ** 2


def fade_in_window(win: tk.Wm, *, ms: int = 150, steps: int = 8) -> None:
    """Toplevel 淡入（alpha 0→1，ease-out，总时长 ms）。

    tkinter 没有逐控件透明度，但整个 Toplevel 有（-alpha）——模态框、
    详情窗、悬浮气泡这类「偶尔出现」的表面是仅有的适用面；键盘唤起的
    高频面（命令面板）不接这个函数。平台不支持逐窗 alpha 时直接显示。
    ms 与 steps 都可注入：测试传 steps=2/ms=2 按完成态轮询。
    """
    interval = max(round(ms / steps), 1)
    try:
        win.attributes("-alpha", 0.0)
    except tk.TclError:
        return  # 不支持 -alpha（老 WM / 某些合成器）——保持原样显示

    def tick(step: int = 0) -> None:
        if not win.winfo_exists():
            return
        value = ease_out((step + 1) / steps)
        try:
            win.attributes("-alpha", value)
        except tk.TclError:
            return
        if step + 1 < steps:
            win.after(interval, tick, step + 1)

    win.after(interval, tick)  # 起手帧也走时钟：调用后 alpha 确定为 0


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
            self._job = self._widget.after(FRAME_MS, self._tick)

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
        self._job = self._widget.after(FRAME_MS, self._tick)


def lerp_color(a: str, b: str, t: float) -> str:
    """hex 颜色线性插值。t=0 得 a，t=1 得 b。"""
    ta = _parse(a)
    tb = _parse(b)
    mixed = tuple(round(x + (y - x) * t) for x, y in zip(ta, tb))
    return "#%02x%02x%02x" % mixed


def _parse(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
