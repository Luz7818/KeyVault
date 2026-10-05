"""测试替身。

这里的每一个类都只在 tests/ 下使用。test_arch.py 会断言：没有任何非测试模块
import 本文件 —— 尤其不许让 PlaintextProtector 溜进产品代码路径，那会让「静息
加密」这条保证悄悄失效而所有测试仍然全绿。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from kv import clock
from kv.errors import DpapiError

DEFAULT_START = datetime.fromisoformat("2026-10-03T22:41:00+08:00")


class PlaintextProtector:
    """仅测试替身：不加密，只加一个可识别的前缀。

    加前缀是故意的 —— 它让「这个 blob 确实经过了 Protector」可断言，
    而不是碰巧原样落盘。
    """

    MARKER = b"PLAINTEXT-TEST-DOUBLE:"

    def protect(self, data: bytes) -> bytes:
        if not isinstance(data, bytes):
            raise DpapiError("protect 只接受 bytes")
        return self.MARKER + data

    def unprotect(self, blob: bytes) -> bytes:
        out = bytearray()
        self.unprotect_into(blob, out)
        result = bytes(out)
        for i in range(len(out)):
            out[i] = 0
        out.clear()
        return result

    def unprotect_into(self, blob: bytes, out: bytearray) -> None:
        if not blob.startswith(self.MARKER):
            raise DpapiError("不是本替身产生的 blob", code=13)
        plain = blob[len(self.MARKER) :]
        del out[:]
        out.extend(plain)


class FakeClock:
    """确定性时钟。过期预警、擦除 deadline、inbox TTL、审计链全靠它才可测。"""

    def __init__(self, start: datetime = DEFAULT_START):
        self.current = start

    def install(self) -> "FakeClock":
        clock.set_provider(lambda: self.current)
        return self

    def uninstall(self) -> None:
        clock.set_provider(None)

    def advance(self, **kwargs) -> datetime:
        self.current = self.current + timedelta(**kwargs)
        return self.current

    def set(self, when: datetime) -> None:
        self.current = when

    def iso(self) -> str:
        return self.current.isoformat(timespec="seconds")


class FakeClipboard:
    """脚本化剪切板。records 每一次写入，好断言「什么都没被持久化」。"""

    def __init__(self, script: list[tuple[int, str]] | None = None):
        self.script = list(script or [])
        self.index = 0
        self.writes: list[tuple[str, bool]] = []
        self.content: str | None = None
        self.sequence_number = 0
        self.contention = False

    def push(self, text: str) -> None:
        self.sequence_number += 1
        self.script.append((self.sequence_number, text))

    def sequence(self) -> int:
        if self.index < len(self.script):
            self.sequence_number = self.script[self.index][0]
        return self.sequence_number

    def read(self) -> str | None:
        if self.contention:
            return None
        if self.content is not None:
            return self.content
        if self.index < len(self.script):
            self.content = self.script[self.index][1]
            self.index += 1
            return self.content
        return None

    def write(self, text: str, *, exclude_history: bool = True) -> bool:
        self.writes.append((text, exclude_history))
        self.content = text
        self.sequence_number += 1
        return True
