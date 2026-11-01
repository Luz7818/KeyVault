"""native messaging 帧协议：往返、EOF、坏帧。"""

from __future__ import annotations

import io
import struct
import unittest

from kv.host import protocol


def frame(payload: bytes) -> bytes:
    return struct.pack("<I", len(payload)) + payload


class TestReadMessage(unittest.TestCase):

    def test_roundtrip(self):
        stream = io.BytesIO(frame('{"type":"ping","中文":"值"}'.encode("utf-8")))
        self.assertEqual(protocol.read_message(stream),
                         {"type": "ping", "中文": "值"})

    def test_clean_eof_returns_none(self):
        self.assertIsNone(protocol.read_message(io.BytesIO(b"")))

    def test_zero_length_frame_raises(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.read_message(io.BytesIO(b"\x00\x00\x00\x00"))
        # 空对象是合法消息
        self.assertEqual(protocol.read_message(io.BytesIO(frame(b"{}"))), {})

    def test_truncated_body_raises(self):
        stream = io.BytesIO(struct.pack("<I", 100) + b'{"a"')
        with self.assertRaises(protocol.ProtocolError):
            protocol.read_message(stream)

    def test_oversized_frame_raises(self):
        stream = io.BytesIO(struct.pack("<I", protocol.MAX_INCOMING + 1))
        with self.assertRaises(protocol.ProtocolError):
            protocol.read_message(stream)

    def test_non_json_raises(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.read_message(io.BytesIO(frame(b"not json")))

    def test_non_object_raises(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.read_message(io.BytesIO(frame(b"[1,2]")))


class TestWriteMessage(unittest.TestCase):

    def test_roundtrip(self):
        stream = io.BytesIO()
        protocol.write_message(stream, {"ok": True, "n": 1})
        stream.seek(0)
        self.assertEqual(protocol.read_message(stream), {"ok": True, "n": 1})

    def test_oversized_outgoing_raises(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.write_message(io.BytesIO(), {"blob": "x" * (protocol.MAX_OUTGOING)})


if __name__ == "__main__":
    unittest.main()
