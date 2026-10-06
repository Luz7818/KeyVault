"""kv GUI 入口 shim。修 sys.path、DPI 感知、UTF-8 后启动 tkinter 主窗口。

DPI 感知必须放在这里而不是 kv/gui/：进程不感知 DPI 时系统会把整个窗口
位图拉伸，文字发虚——这是「界面不清晰」的根因。ctypes 出错的失败模式是
进程级崩溃，按 test_arch 的隔离规则 FFI 只允许在入口与 crypto/capture。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _dpi_scale() -> float:
    """进程级 DPI 感知，返回缩放系数。必须在创建任何 Tk 窗口之前调用。"""
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
        except (OSError, AttributeError):
            ctypes.windll.user32.SetProcessDPIAware()
        hdc = ctypes.windll.user32.GetDC(0)
        try:
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        finally:
            ctypes.windll.user32.ReleaseDC(0, hdc)
        return max(dpi / 96.0, 1.0)
    except Exception:
        return 1.0


def _ui_scale() -> float:
    """读用户在设置页选的界面缩放（设置表持久化，默认 100%）。"""
    try:
        from kv.core import db, repo
        from kv.paths import vault_db

        conn = db.connect(vault_db())
        try:
            raw = repo.get_setting(conn, "ui_scale", "")
        finally:
            conn.close()
        value = float(raw) if raw else 1.0
        return value if 0.8 <= value <= 1.6 else 1.0
    except Exception:
        return 1.0  # vault 未初始化/损坏时按 100%，不挡启动


from kv.gui import theme  # noqa: E402  —— 必须在 sys.path 修好之后导入

theme.init(_dpi_scale() * _ui_scale())

from kv.gui import launch  # noqa: E402

if __name__ == "__main__":
    launch()
