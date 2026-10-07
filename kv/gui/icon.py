"""应用窗口图标：标题栏与任务栏用品牌盾徽，替代 tkinter 默认羽毛。

图标资产 packaging/icon.png 开发态在仓库、打包后随 datas 进 _MEIPASS，
两个位置都找一遍；找不到就静默跳过（无图标不影响功能）。
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path


def icon_file() -> Path | None:
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS", ".")) / "packaging" / "icon.png")
    candidates.append(Path(__file__).resolve().parents[2] / "packaging" / "icon.png")
    for c in candidates:
        if c.is_file():
            return c
    return None


def apply(root: tk.Tk) -> None:
    path = icon_file()
    if path is None:
        return
    try:
        photo = tk.PhotoImage(file=str(path))
        root.iconphoto(True, photo)  # True = 设为所有后续窗口的默认
        root._kv_icon_ref = photo  # 防 GC：PhotoImage 被回收图标会消失
    except tk.TclError:
        pass
