"""kv GUI 入口 shim。修正 sys.path 和 UTF-8 后启动 tkinter 主窗口。"""

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

from kv.gui import launch  # noqa: E402

if __name__ == "__main__":
    launch()
