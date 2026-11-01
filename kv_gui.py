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


def _dispatch_frozen_cli() -> None:
    """打包态里 kv.exe 兼职跑 CLI 子命令。

    kv copy 派出的擦除 helper 在冻结态就是 exe 自己（sys.frozen 下没有
    kv.py 脚本和 -I 选项）—— 不在这里接住 `_wipe` argv，它就会变成
    「每复制一次密钥弹一个多余的 GUI 窗口，剪切板永不擦除」。
    """
    if not getattr(sys, "frozen", False):
        return
    argv = sys.argv[1:]
    if argv and argv[0] == "_wipe":
        from kv.cli import main as _cli_main

        sys.exit(_cli_main(argv))


_dispatch_frozen_cli()


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


def _dark_title_bar(window) -> None:
    """Windows 深色标题栏（best-effort：不支持或失败都保持系统默认）。

    深墨蓝侧边栏配系统默认白标题栏，是当前界面最刺眼的一道断裂；DWM
    属性一改即得。与 DPI 感知同理放 shim——ctypes 出错的失败模式是进程级
    崩溃，按 test_arch 的隔离规则 FFI 只允许在入口与 crypto/capture
    （10-06 曾把这段写进 kv/gui/ 被门禁正确拦下，这次放在规则允许的位置）。
    """
    try:
        import ctypes

        window.update_idletasks()  # 顶层包装 HWND 在完成几何布局后才可靠
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        if not hwnd:
            return
        value = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE（Win10 旧版取 19）
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value),
            ) == 0:
                return
    except Exception:
        pass  # 非 Windows / 无 dwmapi：标题栏保持系统默认


from kv.gui import theme  # noqa: E402  —— 必须在 sys.path 修好之后导入

theme.init(_dpi_scale())

from kv.gui import launch  # noqa: E402

if __name__ == "__main__":
    launch(ui_scale=_ui_scale(), on_window=_dark_title_bar)
