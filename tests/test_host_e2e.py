"""宿主端到端：以真子进程跑 kv_host.py，stdio 上走完整协议。

等价于浏览器视角的最小会话：ping → capture → approve → list → copy。
vault 用 KV_VAULT_DIR 指向临时目录（真 DPAPI；捕获/入库管线是真实路径）。
subprocess 级测试按 test_cli_m0.TestSubprocess 的模式注明动机。
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
KV_HOST = ROOT / "kv_host.py"


def _frame(message: dict) -> bytes:
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
    return struct.pack("<I", len(payload)) + payload


def _read_frame(stream) -> dict:
    header = stream.read(4)
    if not header:
        return {}
    (length,) = struct.unpack("<I", header)
    return json.loads(stream.read(length).decode("utf-8"))


@unittest.skipUnless(sys.platform == "win32", "vault 依赖 DPAPI")
class TestHostEndToEnd(unittest.TestCase):
    """启动 shim、发真帧、读真响应——协议实现与请求分发的集成验证。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"

        from kv.core import db
        from kv.crypto import dpapi
        from kv.core.vault import VaultStore

        canary = dpapi.protect(dpapi.CANARY_TEXT.encode("utf-8"))
        db.initialize(self.vault / "vault.db", canary_blob=canary,
                      canary_text=dpapi.CANARY_TEXT)
        self.store = VaultStore(self.vault / "vault.db")

        env = {**os.environ, "KV_VAULT_DIR": str(self.vault)}
        self.proc = subprocess.Popen(
            [sys.executable, "-I", str(KV_HOST)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, cwd=str(ROOT),
        )
        self.addCleanup(self._shutdown)

    def _shutdown(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def _ask(self, message: dict) -> dict:
        self.proc.stdin.write(_frame(message))
        self.proc.stdin.flush()
        return _read_frame(self.proc.stdout)

    def test_full_capture_approve_list_copy_session(self):
        pong = self._ask({"type": "ping"})
        self.assertTrue(pong["pong"])
        self.assertTrue(pong["initialized"])

        captured = self._ask({
            "type": "capture",
            "text": "DEEPSEEK_API_KEY=sk-abc123_def456ghi789jklmnopqrstu12",
            "source_url": "https://platform.deepseek.com/api-keys",
            "title": "DeepSeek Platform - API Keys",
        })
        results = captured["results"]
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["promptable"])
        self.assertNotIn("sk-abc123def456", json.dumps(captured))  # 响应无明文

        approved = self._ask({"type": "approve",
                              "inbox_id": results[0]["inbox_id"]})
        self.assertTrue(approved["ok"])
        self.assertEqual(approved["outcome"], "created")
        name = approved["name"]
        self.assertEqual(self.store.get(name).source_url,
                         "https://platform.deepseek.com/api-keys")

        listing = self._ask({"type": "list", "query": "deep"})
        self.assertEqual(len(listing["rows"]), 1)
        self.assertEqual(listing["rows"][0]["name"], name)

        from kv.capture.clipboard import ClipboardError

        try:
            copied = self._ask({"type": "copy", "name": name})
        except (ClipboardError, OSError):
            self.skipTest("剪切板被系统级占用，无法做真复制验证")  # 环境问题不误报
        self.assertTrue(copied["ok"])

    def test_bad_frame_terminates_with_usage_code(self):
        self.proc.stdin.write(b"\xff\xff\xff\xff")
        self.proc.stdin.flush()
        self.assertEqual(self.proc.wait(timeout=15), 1)  # EXIT_USAGE


if __name__ == "__main__":
    unittest.main()
