"""Windows DPAPI（CryptProtectData / CryptUnprotectData）绑定。

实测确立的绑定规则，违反任何一条都不会抛异常，而是**直接杀掉进程**：

1. 两个函数都在 **crypt32.dll**，不在 advapi32（advapi32 里查不到，AttributeError）。
2. 两个函数都取 **7 个参数**。CryptUnprotectData 的第 6 个是 dwFlags。
   声明成 6 个 → 进程崩溃、退出码 127、无任何输出。这是静默杀手。
3. argtypes / restype **必须**设置。不设的话 None 会被当成 32 位零传进去 → 同样崩溃。
   所以在导入时设一次，绝不每次调用设。
4. CryptUnprotectData 的第 2 参类型是 **POINTER(c_wchar_p)**，不是 c_wchar_p。
   用裸 c_wchar_p 配 byref() → ctypes.ArgumentError: wrong type。
5. 源缓冲区必须在 FFI 调用期间保持存活，否则是使用后释放。

entropy 用固定应用常量。说实话：entropy 和代码在同一个二进制里，对拿到 DB 文件的
攻击者不提供任何额外保密性 —— DPAPI 的保护就是用户的登录凭据，句号。它的真实
价值是域分隔（别的用 DPAPI 的程序产生的 blob 不会被误交叉解密）和版本标记。
传错 entropy 表现为 GetLastError() == 13。
"""

from __future__ import annotations

import ctypes
import sys
from typing import Protocol

from kv.errors import DpapiError, descriptor

UI_FORBIDDEN = 0x1
LOCAL_MACHINE = 0x4

APP_ENTROPY = b"keyvault.v1"

CANARY_TEXT = "keyvault-binding-canary"

_ERROR_MESSAGES = {
    13: "entropy 不对，或这个 blob 是在另一个 Windows 账户下创建的（DPAPI blob 不能跨账户/跨机器迁移）",
    87: "参数不合法 —— 通常是 blob 已损坏或被截断",
    8: "内存不足",
}


class Protector(Protocol):
    """加解密接缝。tests/ 提供 PlaintextProtector 作为替身。"""

    def protect(self, data: bytes) -> bytes: ...

    def unprotect(self, blob: bytes) -> bytes: ...

    def unprotect_into(self, blob: bytes, out: bytearray) -> None: ...


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


_BLOB_PTR = ctypes.POINTER(DATA_BLOB)

_crypt32 = None
_kernel32 = None


def _bind():
    """惰性绑定，让本模块在非 Windows 上也能被 import（测试会 skip）。"""
    global _crypt32, _kernel32
    if _crypt32 is not None:
        return _crypt32, _kernel32
    if sys.platform != "win32":
        raise DpapiError(f"DPAPI 只在 Windows 上可用，当前平台 {sys.platform}")

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    protect = crypt32.CryptProtectData
    protect.restype = ctypes.c_int
    protect.argtypes = [
        _BLOB_PTR,            # pDataIn
        ctypes.c_wchar_p,     # szDataDescr
        _BLOB_PTR,            # pOptionalEntropy
        ctypes.c_void_p,      # pvReserved
        ctypes.c_void_p,      # pPromptStruct
        ctypes.c_uint32,      # dwFlags
        _BLOB_PTR,            # pDataOut
    ]

    unprotect = crypt32.CryptUnprotectData
    unprotect.restype = ctypes.c_int
    unprotect.argtypes = [
        _BLOB_PTR,                      # pDataIn
        ctypes.POINTER(ctypes.c_wchar_p),  # ppszDataDescr —— 注意是 POINTER
        _BLOB_PTR,                      # pOptionalEntropy
        ctypes.c_void_p,                # pvReserved
        ctypes.c_void_p,                # pPromptStruct
        ctypes.c_uint32,                # dwFlags —— 第 6 个参数，漏掉它就崩
        _BLOB_PTR,                      # pDataOut
    ]

    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    _crypt32, _kernel32 = crypt32, kernel32
    return _crypt32, _kernel32


def _make_blob(data: bytes, keepalive: list) -> DATA_BLOB:
    """造一个 DATA_BLOB。缓冲区追加进 keepalive，让它活得比 FFI 调用久。"""
    buffer = ctypes.create_string_buffer(data, len(data)) if data else ctypes.create_string_buffer(1)
    keepalive.append(buffer)
    blob = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    keepalive.append(blob)
    return blob


def _release(blob: DATA_BLOB) -> None:
    """归零输出缓冲区再 LocalFree。顺序要紧：先擦后放。"""
    if blob.pbData and blob.cbData:
        ctypes.memset(blob.pbData, 0, blob.cbData)
    _kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))
    blob.pbData = ctypes.POINTER(ctypes.c_char)()
    blob.cbData = 0


def describe_failure(code: int) -> str:
    known = _ERROR_MESSAGES.get(code)
    return f"{known}；GetLastError={code}" if known else f"GetLastError={code}"


def protect(data: bytes, *, entropy: bytes = APP_ENTROPY, local_machine: bool = False) -> bytes:
    """加密。默认 per-user：机器级 blob 本机任何账户都能解，等于放弃唯一的访问控制。"""
    if not isinstance(data, bytes):
        raise DpapiError(f"protect 只接受 bytes，收到 {type(data).__name__}")
    crypt32, _ = _bind()
    keepalive: list = []
    flags = UI_FORBIDDEN | (LOCAL_MACHINE if local_machine else 0)

    in_blob = _make_blob(data, keepalive)
    entropy_blob = _make_blob(entropy, keepalive) if entropy else None
    out_blob = DATA_BLOB()
    keepalive.append(out_blob)

    # szDataDescr 传 NULL：任何能对 blob 调 CryptUnprotectData 的人都能读回描述，
    # 把记录名放那里是零收益地泄漏元数据。
    succeeded = crypt32.CryptProtectData(
        ctypes.byref(in_blob), None,
        ctypes.byref(entropy_blob) if entropy_blob else None,
        None, None, flags, ctypes.byref(out_blob),
    )
    if not succeeded:
        code = ctypes.get_last_error()
        raise DpapiError(f"加密失败（{descriptor(data)}）：{describe_failure(code)}", code=code)
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        _release(out_blob)


def unprotect(blob: bytes, *, entropy: bytes = APP_ENTROPY) -> bytes:
    """解密成 bytes。优先用 unprotect_into —— 那个版本不产生不可变的明文字节串。"""
    out = bytearray()
    unprotect_into(blob, out, entropy=entropy)
    result = bytes(out)
    for i in range(len(out)):
        out[i] = 0
    out.clear()
    return result


def unprotect_into(blob: bytes, out: bytearray, *, entropy: bytes = APP_ENTROPY) -> None:
    """解密进调用方持有的可变缓冲区。明文从不作为不可变 bytes 对象存在。

    这是相对朴素 string_at 版本唯一真实的内存卫生改进。调用方负责在 finally 里
    逐字节归零 out。
    """
    if not isinstance(blob, bytes):
        raise DpapiError(f"unprotect 只接受 bytes，收到 {type(blob).__name__}")
    crypt32, _ = _bind()
    keepalive: list = []

    in_blob = _make_blob(blob, keepalive)
    entropy_blob = _make_blob(entropy, keepalive) if entropy else None
    out_blob = DATA_BLOB()
    description = ctypes.c_wchar_p()
    keepalive.extend([out_blob, description])

    succeeded = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), ctypes.byref(description),
        ctypes.byref(entropy_blob) if entropy_blob else None,
        None, None, UI_FORBIDDEN, ctypes.byref(out_blob),
    )
    if not succeeded:
        code = ctypes.get_last_error()
        raise DpapiError(f"解密失败（{descriptor(blob)}）：{describe_failure(code)}", code=code)
    try:
        length = out_blob.cbData
        del out[:]
        out.extend(bytes(length))
        if length:
            target = (ctypes.c_char * length).from_buffer(out)
            ctypes.memmove(target, out_blob.pbData, length)
    finally:
        _release(out_blob)


def self_test(*, entropy: bytes = APP_ENTROPY) -> str:
    """往返一次 canary。返回明文以便调用方比对；失败抛 DpapiError 带错误码。

    kv init 用它写 binding.canary_blob，kv doctor 用它判断这个 vault 是不是
    在另一个 Windows 账户下建的。
    """
    payload = CANARY_TEXT.encode("utf-8")
    blob = protect(payload, entropy=entropy)
    recovered = unprotect(blob, entropy=entropy)
    if recovered != payload:
        raise DpapiError("DPAPI 自检失败：往返不一致")
    return CANARY_TEXT


class DpapiProtector:
    """Protector 的产品实现。"""

    def __init__(self, *, entropy: bytes = APP_ENTROPY, local_machine: bool = False):
        self.entropy = entropy
        self.local_machine = local_machine

    def protect(self, data: bytes) -> bytes:
        return protect(data, entropy=self.entropy, local_machine=self.local_machine)

    def unprotect(self, blob: bytes) -> bytes:
        return unprotect(blob, entropy=self.entropy)

    def unprotect_into(self, blob: bytes, out: bytearray) -> None:
        unprotect_into(blob, out, entropy=self.entropy)
