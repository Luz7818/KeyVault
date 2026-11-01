"""kv 原生消息宿主入口 shim。修 sys.path、stderr UTF-8、冻结态兼职分发。

与 kv_gui.py 同理：ctypes/进程级关注点放根 shim（test_arch 隔离规则允许
的入口位置）。与 kv_gui 的差异：
- **stdout/stdin 是 native messaging 协议通道**——绝不 reconfigure 成文本、
  绝不打印任何东西；日志只走 stderr（kv/host/log.py）。
- 冻结态兼职两件事：`_wipe N`（copy_secret 派出的擦除 helper 就是本 exe，
  与 kv_gui 对 `_wipe` 的分发是一对，spawn_helper 冻结分支不改）与
  `--version`（packaging/verify.py 第六查用）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# stderr 可以当日志通道做 UTF-8；stdin/stdout 必须保持二进制协议通道
_stream = getattr(sys.stderr, "reconfigure", None)
if _stream:
    try:
        _stream(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass


def _dispatch_argv() -> None:
    """非会话形态的分发：--version（任意模式）与 _wipe（仅冻结态）。"""
    argv = sys.argv[1:]
    if argv and argv[0] == "--version":
        from kv import __version__

        print(f"kv-host {__version__}")
        raise SystemExit(0)
    if getattr(sys, "frozen", False) and argv and argv[0] == "_wipe":
        from kv.cli import main as cli_main

        sys.exit(cli_main(argv))


_dispatch_argv()


def main() -> int:
    from kv.host.app import main as host_main

    return host_main()


if __name__ == "__main__":
    sys.exit(main())
