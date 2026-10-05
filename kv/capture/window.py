"""前台窗口标题 —— 第四级识别信号。

这是唯一能救回「从厂商控制台复制一把裸 sk-」那个场景的信号，而那正是用户最初的
痛点。但它也是一个隐私权衡，所以规矩是硬的：

**原始标题只在内存里比对，比对完立刻丢弃。绝不写库、绝不写日志、绝不进审计。**
窗口标题可能含敏感文本（文档名、邮件主题、聊天对象）。落盘的只有推导出的平台 id
和一句「前台窗口标题含 X 的关键词」，X 是规则表里的词，不是标题原文。

tests/test_arch.py 与本模块的 doctest 一起守这条：本模块不 import 任何存储层。
"""

from __future__ import annotations

import ctypes
import sys

BUFFER_CHARS = 1024

_user32 = None


def _bind():
    global _user32
    if _user32 is not None:
        return _user32
    if sys.platform != "win32":
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    _user32 = user32
    return _user32


def foreground_title() -> str:
    """当前前台窗口标题。拿不到就返回空串 —— **永不抛**。

    watch 每 0.5 s 调一次，这里任何异常都会把监听循环打断。
    """
    user32 = _bind()
    if user32 is None:
        return ""
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        buffer = ctypes.create_unicode_buffer(BUFFER_CHARS)
        length = user32.GetWindowTextW(hwnd, buffer, BUFFER_CHARS)
        if length <= 0:
            return ""
        return buffer.value
    except (OSError, ValueError):
        return ""


def match_platform(title: str, rules_index) -> str:
    """在内存里把标题比对成平台 id，然后标题就被丢掉。

    rules_index 是 detect.rules.WINDOW_INDEX 那样的 ((关键词小写, Rule), ...)。
    这里**只返回平台 id**，不返回命中的关键词，也不返回标题本身 —— 调用方拿不到
    原文就没法把它写进任何地方。
    """
    if not title:
        return ""
    lowered = title.lower()
    best_platform = ""
    best_specificity = -1
    for needle, rule in rules_index:
        if needle in lowered and rule.specificity > best_specificity:
            best_platform, best_specificity = rule.platform, rule.specificity
    return best_platform
