"""异常树。

契约：本模块产生的任何 __str__ 都不得含有密钥值。异常只携带长度、sha256 前缀
和系统错误码。tests/test_errors_redaction.py 逐条断言这一点 —— 失败路径恰恰是
最容易把值拼进消息的时候。
"""

from __future__ import annotations

import hashlib

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NOT_FOUND = 2
EXIT_LEAKS = 3
EXIT_BINDING = 4
EXIT_GATE = 5
EXIT_INTERRUPTED = 130


def descriptor(value: bytes) -> str:
    """把一个值描述成不含它自己的字符串。所有异常消息用它，不用 f"{value}"。"""
    return f"len={len(value)} sha256={sha256_prefix(value)}"


def sha256_prefix(value: bytes, width: int = 12) -> str:
    return hashlib.sha256(value).hexdigest()[:width]


class KvError(Exception):
    """所有可预期失败的基类。exit_code 由 cli.py 读取。"""

    exit_code = EXIT_USAGE

    def __init__(self, message: str, *, code: int | None = None, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.code = code
        self.hint = hint

    def __str__(self) -> str:
        if self.code is not None:
            return f"{self.message}（系统错误码 {self.code}）"
        return self.message


class UsageError(KvError):
    exit_code = EXIT_USAGE


class NotFoundError(KvError):
    exit_code = EXIT_NOT_FOUND


class AlreadyExistsError(KvError):
    exit_code = EXIT_USAGE


class LeaksFoundError(KvError):
    exit_code = EXIT_LEAKS


class DpapiError(KvError):
    exit_code = EXIT_BINDING


class BindingError(KvError):
    """vault 是在另一个 Windows 账户下建的，DPAPI blob 解不开。"""

    exit_code = EXIT_BINDING


class VaultNotInitializedError(KvError):
    exit_code = EXIT_BINDING

    def __init__(self, path: str = ""):
        where = f"：{path}" if path else ""
        super().__init__(
            f"vault 尚未初始化{where}",
            hint="先跑 kv init",
        )


class GateError(KvError):
    """失败关闭闸门：检测表变了但 golden 自检没重跑。"""

    exit_code = EXIT_GATE


class RefusedError(KvError):
    """识别结果不可信（如裸 hex-32），拒绝落盘，除非 --force。"""

    exit_code = EXIT_USAGE
