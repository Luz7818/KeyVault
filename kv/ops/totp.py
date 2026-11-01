"""TOTP 验证码生成（RFC 6238）。纯 stdlib：hmac + hashlib + base64 + struct。

种子就是记录的值（base32，验证器「显示密钥」里复制的那串）。SHA1 / 6 位 /
30 秒是 RFC 默认，也是各家验证器的兼容基线——不开参数面，generate 只留
digits 给 RFC 测试向量用。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
from datetime import datetime

PERIOD = 30
DIGITS = 6


class SeedError(ValueError):
    """值不是合法的 base32 TOTP 种子。"""


def parse_seed(raw: str) -> bytes:
    """宽容解析 base32：大小写不敏感、忽略空格与连字符、自动补 padding。"""
    cleaned = "".join(raw.split()).replace("-", "").upper()
    if not cleaned:
        raise SeedError("种子为空")
    try:
        return base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))
    except Exception as exc:
        raise SeedError("不是合法的 base32 种子") from exc


def generate(seed: bytes, now: datetime, *, digits: int = DIGITS) -> tuple[str, int]:
    """(验证码, 剩余秒数)。now 需 timezone-aware（clock.now() 满足），
    counter 按 RFC 6238 取 UTC 时间步。"""
    timestamp = int(now.timestamp())
    counter = timestamp // PERIOD
    digest = hmac.new(seed, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 10 ** digits
    return f"{code:0{digits}d}", PERIOD - timestamp % PERIOD
