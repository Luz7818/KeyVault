"""Win32 剪切板后端（零依赖，ctypes）。

为什么不用 PowerShell 的 Get-Clipboard：每次调用冷启 powershell.exe 要 150–400 ms，
而 watch 以 0.5 s 间隔轮询 —— 那等于永远每秒起两个进程。而且它的输出要过控制台
代码页解码，本机 CP 是 936，实测 tasklist 的输出就是乱码。ctypes 直接拿 UTF-16，
编码由我们精确控制。

**本文件最重要的部分是那三个排除格式。** 不设它们的话，kv copy 会把你的 API key
存进 Windows 剪切板历史（Win+V），无限期保留，而且——若开了云剪切板——同步到你
的其他设备。那会静默地抵消整个工具的意义。
"""

from __future__ import annotations

import ctypes
import sys
import time
from typing import Protocol

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

# 三个已注册格式的名字。只有这三个有意义：RegisterClipboardFormatW 会高高兴兴
# 注册一个任意名字（实测 CF_EXCLUDECLIPBOARDHISTORY -> id 50123）而它什么都不做。
FORMAT_EXCLUDE_MONITOR = "ExcludeClipboardContentFromMonitorProcessing"
FORMAT_INCLUDE_IN_HISTORY = "CanIncludeInClipboardHistory"
FORMAT_UPLOAD_TO_CLOUD = "CanUploadToCloudClipboard"

OPEN_RETRIES = 5
OPEN_RETRY_DELAY = 0.04


class ClipboardBackend(Protocol):
    """剪切板接缝。tests/fakes.py 提供 FakeClipboard 作为替身。"""

    def sequence(self) -> int:
        """变更序号。**不打开剪切板**，所以永不与其他应用争用、成本为零。"""

    def read(self) -> str | None:
        """读文本。被别的应用占着时返回 None，**永不抛** —— 争用不该让 watch 崩。"""

    def write(self, text: str, *, exclude_history: bool = True) -> bool:
        """写文本。exclude_history 默认开：把机密挡在 Win+V 与云剪切板之外。"""


class ClipboardError(Exception):
    """剪切板操作失败。不携带任何值。"""


_user32 = None
_kernel32 = None
_formats: dict[str, int] = {}


def _bind():
    global _user32, _kernel32
    if _user32 is not None:
        return _user32, _kernel32
    if sys.platform != "win32":
        raise ClipboardError(f"Win32 剪切板只在 Windows 上可用，当前平台 {sys.platform}")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_int
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = ctypes.c_int
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = ctypes.c_int
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.GetClipboardSequenceNumber.argtypes = []
    user32.GetClipboardSequenceNumber.restype = ctypes.c_uint
    user32.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]
    user32.RegisterClipboardFormatW.restype = ctypes.c_uint
    user32.CountClipboardFormats.argtypes = []
    user32.CountClipboardFormats.restype = ctypes.c_int
    user32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
    user32.IsClipboardFormatAvailable.restype = ctypes.c_int

    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = ctypes.c_int
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.restype = ctypes.c_void_p
    kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
    kernel32.GlobalSize.restype = ctypes.c_size_t

    _user32, _kernel32 = user32, kernel32
    return _user32, _kernel32


def registered_format(name: str) -> int:
    """注册（幂等）并返回格式 id。实测三个格式分别得到 49947 / 49956 / 49788。"""
    if name in _formats:
        return _formats[name]
    user32, _ = _bind()
    fmt = user32.RegisterClipboardFormatW(name)
    if not fmt:
        raise ClipboardError(f"RegisterClipboardFormatW 失败：{ctypes.get_last_error()}")
    _formats[name] = fmt
    return fmt


def _open(user32) -> bool:
    """打开剪切板，失败则重试。别的应用可能正拿着它 —— 那不是错误，只是稍后再试。"""
    for attempt in range(OPEN_RETRIES):
        if user32.OpenClipboard(None):
            return True
        if attempt < OPEN_RETRIES - 1:
            time.sleep(OPEN_RETRY_DELAY)
    return False


class Win32Clipboard:
    """唯一随产品发布的后端。"""

    def sequence(self) -> int:
        user32, _ = _bind()
        return int(user32.GetClipboardSequenceNumber())

    def read(self) -> str | None:
        user32, kernel32 = _bind()
        if not _open(user32):
            return None
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            locked = kernel32.GlobalLock(handle)
            if not locked:
                return None
            try:
                return ctypes.wstring_at(locked)
            finally:
                kernel32.GlobalUnlock(handle)
        except (OSError, ValueError):
            return None
        finally:
            user32.CloseClipboard()

    def write(self, text: str, *, exclude_history: bool = True) -> bool:
        user32, kernel32 = _bind()
        if not _open(user32):
            return False
        try:
            if not user32.EmptyClipboard():
                return False
            if exclude_history:
                # 先设排除标记：它一出现，剪切板上**所有**格式都不进历史与云同步。
                self._set_flag(user32, kernel32, FORMAT_EXCLUDE_MONITOR, 1)
            if not self._set_text(user32, kernel32, text):
                return False
            if exclude_history:
                # 这两个是序列化的 DWORD 0，分别关掉本地历史与跨设备同步。
                self._set_flag(user32, kernel32, FORMAT_INCLUDE_IN_HISTORY, 0)
                self._set_flag(user32, kernel32, FORMAT_UPLOAD_TO_CLOUD, 0)
            return True
        finally:
            user32.CloseClipboard()

    def _set_text(self, user32, kernel32, text: str) -> bool:
        encoded = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        if not handle:
            return False
        locked = kernel32.GlobalLock(handle)
        if not locked:
            kernel32.GlobalFree(handle)
            return False
        try:
            ctypes.memmove(locked, encoded, len(encoded))
        finally:
            kernel32.GlobalUnlock(handle)
        # 成功之后**不要** GlobalFree —— 所有权已经转移给系统。
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        return True

    def _set_flag(self, user32, kernel32, format_name: str, value: int) -> bool:
        try:
            fmt = registered_format(format_name)
        except ClipboardError:
            return False
        payload = ctypes.c_uint32(value)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, ctypes.sizeof(payload))
        if not handle:
            return False
        locked = kernel32.GlobalLock(handle)
        if not locked:
            kernel32.GlobalFree(handle)
            return False
        try:
            ctypes.memmove(locked, ctypes.byref(payload), ctypes.sizeof(payload))
        finally:
            kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(fmt, handle):
            kernel32.GlobalFree(handle)
            return False
        return True

    def has_exclusion_formats(self) -> bool:
        """剪切板上现在有没有那三个排除格式。测试用它断言历史排除真的生效了。"""
        user32, _ = _bind()
        if not _open(user32):
            return False
        try:
            return all(
                user32.IsClipboardFormatAvailable(registered_format(name))
                for name in (FORMAT_EXCLUDE_MONITOR, FORMAT_INCLUDE_IN_HISTORY,
                             FORMAT_UPLOAD_TO_CLOUD)
            )
        except ClipboardError:
            return False
        finally:
            user32.CloseClipboard()


def default_backend() -> ClipboardBackend:
    return Win32Clipboard()
