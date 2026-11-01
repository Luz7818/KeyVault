"""kv GUI 包。叶子模块 —— kv 包内没有任何模块 import 它。"""

from __future__ import annotations

import tkinter as tk
from typing import Callable


def launch(scale: float = 1.0, ui_scale: float = 1.0,
           on_window: Callable[[tk.Tk], None] | None = None) -> None:
    """创建主窗口并进入 tkinter 事件循环。

    scale 是显示器 DPI 缩放，ui_scale 是用户偏好（设置表 ui_scale），
    两者相乘决定全部字号与几何。on_window 在窗口就绪后、主循环前回调——
    shim 用它挂 DWM 深色标题栏（ctypes 只许在入口 shim，见 test_arch）。
    """
    from kv.gui.app import App

    app = App(scale=scale, ui_scale=ui_scale)
    if on_window is not None:
        on_window(app)
    app.mainloop()
