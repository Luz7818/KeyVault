"""kv GUI 包。叶子模块 —— kv 包内没有任何模块 import 它。"""

from __future__ import annotations


def launch(scale: float = 1.0, ui_scale: float = 1.0) -> None:
    """创建主窗口并进入 tkinter 事件循环。

    scale 是显示器 DPI 缩放，ui_scale 是用户偏好（设置表 ui_scale），
    两者相乘决定全部字号与几何。
    """
    from kv.gui.app import App

    app = App(scale=scale, ui_scale=ui_scale)
    app.mainloop()
