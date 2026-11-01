"""宿主诊断日志：只写 stderr。

stdout 是 native messaging 协议通道，任何 print 都会腐蚀帧——这是宿主
与 `_wipe` helper（stdio 全 DEVNULL）最大的差异。浏览器把宿主的 stderr
记进自己的日志（--enable-logging 可见），平时静默、排障时在看。
"""

from __future__ import annotations

import sys

PREFIX = "kv-host:"


def info(message: str, *args) -> None:
    _emit(message, args)


def warning(message: str, *args) -> None:
    _emit("WARN " + message, args)


def _emit(message: str, args: tuple) -> None:
    try:
        if args:
            message = message % args
        print(PREFIX, message, file=sys.stderr, flush=True)
    except (OSError, ValueError):
        pass  # stderr 被关/编码失败时静默——日志绝不影响协议
