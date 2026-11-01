"""native messaging 帧协议：4 字节小端长度前缀 + UTF-8 JSON。

与 Chrome/Edge 的约定（developer.chrome.com/docs/apps/nativeMessaging）：
浏览器→宿主单条上限 4 GB、宿主→浏览器单条上限 1 MB。这里对入站自设
4 MB 闸（捕获文本用不到更大，超长直接断为协议错误），出站超 1 MB 拒发
——寄超大响应只会被浏览器静默丢弃，宁可显式失败。
"""

from __future__ import annotations

import json
import struct
import sys
from typing import BinaryIO

HEADER = struct.Struct("<I")
MAX_INCOMING = 4 * 1024 * 1024
MAX_OUTGOING = 1024 * 1024


class ProtocolError(Exception):
    """帧损坏 / 超长 / 不是 JSON 对象。宿主把它报告给浏览器后终止会话。"""


def read_message(stream: BinaryIO) -> dict | None:
    """读一条消息。流干净结束（EOF 打在帧边界上）返回 None。

    半帧 EOF、超长、非法 JSON 都抛 ProtocolError——对宿主来说这些意味着
    通道对端不是它预期的东西，继续读只会越错越远。
    """
    header = _read_exact(stream, HEADER.size)
    if header is None:
        return None
    (length,) = HEADER.unpack(header)
    if length == 0:
        raise ProtocolError("零长帧不是合法的 native messaging 消息")
    if length > MAX_INCOMING:
        raise ProtocolError(f"入站帧 {length} 字节，超过上限 {MAX_INCOMING}")
    payload = _read_exact(stream, length)
    if payload is None:
        raise ProtocolError("消息体在 EOF 处截断")
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError(f"消息不是合法 UTF-8 JSON：{exc}") from exc
    if not isinstance(message, dict):
        raise ProtocolError("消息必须是 JSON 对象")
    return message


def write_message(stream: BinaryIO, message: dict) -> None:
    """写一条消息并 flush。超上限拒发（寄了也会被浏览器丢掉）。"""
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_OUTGOING:
        raise ProtocolError(
            f"出站帧 {len(payload)} 字节，超过浏览器上限 {MAX_OUTGOING}——请缩小请求范围"
        )
    stream.write(HEADER.pack(len(payload)))
    stream.write(payload)
    stream.flush()


def _read_exact(stream: BinaryIO, n: int) -> bytes | None:
    """读满 n 字节；首读即 EOF 返回 None，中途 EOF 视为截断（由调用方区分）。"""
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            if not buf:
                return None  # 干净 EOF
            raise ProtocolError("消息体在 EOF 处截断")
        buf += chunk
    return bytes(buf)


def is_tty_stdin() -> bool:
    """诊断用：stdin 不是管道说明宿主不是被浏览器拉起的（人工误跑）。"""
    try:
        return sys.stdin.isatty()
    except (ValueError, OSError):
        return False
