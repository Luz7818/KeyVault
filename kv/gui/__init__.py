"""kv GUI 包。叶子模块 —— kv 包内没有任何模块 import 它。"""

from __future__ import annotations


def launch() -> None:
    """创建主窗口并进入 tkinter 事件循环。"""
    from kv.gui.app import App

    app = App()
    app.mainloop()
