"""宿主请求分发：捕获 → spool → approve/reject → list/copy 全链。

vault 用 PlaintextProtector 替身、剪贴板用 FakeClipboard、擦除 spawn 打桩
——单元层不碰真 DPAPI/剪切板/子进程（tests/fakes.py 纪律）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401
from tests.fakes import FakeClipboard, FakeClock, PlaintextProtector
from tests.test_vault_store import make_vault

from kv.host import service

SEED_TEXT = "DEEPSEEK_API_KEY=sk-abc123_def456ghi789jklmnopqrstu12"


class HostServiceTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.fake_clock = FakeClock().install()
        self.addCleanup(self.fake_clock.uninstall)
        self.clipboard = FakeClipboard()
        self.store = make_vault(self.root, PlaintextProtector())
        self.spawns: list[int] = []
        self._patch_spawn()

    def _patch_spawn(self):
        from kv.capture import wipe

        original = wipe.spawn_helper
        wipe.spawn_helper = lambda wipe_id, **kw: self.spawns.append(wipe_id) or 4242
        self.addCleanup(setattr, wipe, "spawn_helper", original)

    # ------------------------------------------------------------ 便捷

    def capture(self, text=SEED_TEXT, **fields) -> dict:
        request = {"type": "capture", "text": text,
                   "source_url": "https://platform.deepseek.com/api-keys",
                   "title": "DeepSeek Platform - API Keys", **fields}
        return service.handle(self.store, request, backend=self.clipboard)

    def approve(self, inbox_id: int, **fields) -> dict:
        request = {"type": "approve", "inbox_id": inbox_id, **fields}
        return service.handle(self.store, request, backend=self.clipboard)


class TestCapture(HostServiceTestCase):

    def test_capture_spools_promptable_candidate(self):
        response = self.capture()
        self.assertEqual(len(response["results"]), 1)
        row = response["results"][0]
        self.assertEqual(row["status"], "pending")
        self.assertTrue(row["promptable"])
        self.assertEqual(row["platform"], "deepseek")
        self.assertEqual(row["confidence"], "high")
        self.assertIn("****", row["preview"])  # 掩码，绝无明文
        self.assertNotIn("sk-abc", row["preview"])
        self.assertFalse(any("sk-abc" in str(v) for v in response["results"][0].values()))

    def test_source_url_lands_in_record_on_approve(self):
        inbox_id = self.capture()["results"][0]["inbox_id"]
        result = self.approve(inbox_id)
        self.assertEqual(result["outcome"], "created")
        row = self.store.get(result["name"])
        self.assertEqual(row.source_url, "https://platform.deepseek.com/api-keys")
        self.assertEqual(row.origin, "extension")

    def test_duplicate_capture_is_not_an_error(self):
        first = self.capture()["results"][0]
        second = self.capture()["results"][0]
        self.assertEqual(first["status"], "pending")
        self.assertEqual(second["status"], "already_pending")
        self.assertEqual(second["inbox_id"], first["inbox_id"])

    def test_same_value_already_in_vault_reports_existing(self):
        self.capture()
        self.approve(self.capture()["results"][0]["inbox_id"])  # approve 前的重复捕获是 pending
        response = self.capture()
        # 已入库（approved）后再捕获 → existing，不再进 inbox
        self.assertEqual(response["results"][0]["status"], "existing")

    def test_unpromptable_text_goes_to_inbox_without_prompt(self):
        response = self.capture(text="random token abc123 not recognized")
        for row in response["results"]:
            self.assertFalse(row["promptable"])

    def test_missing_text_is_usage_error(self):
        self.assertEqual(
            list(service.handle(self.store, {"type": "capture"})),
            ["error"],
        )


class TestApproveReject(HostServiceTestCase):

    def test_approve_creates_and_marks_inbox(self):
        inbox_id = self.capture()["results"][0]["inbox_id"]
        result = self.approve(inbox_id)
        self.assertTrue(result["ok"])
        self.assertEqual(result["outcome"], "created")
        self.assertIn("deepseek", result["name"])
        from kv.core import repo

        with self.store.read() as conn:
            row = repo.get_inbox(conn, inbox_id)
        self.assertEqual(row["status"], "accepted")

    def test_approve_with_custom_name(self):
        inbox_id = self.capture()["results"][0]["inbox_id"]
        result = self.approve(inbox_id, name="我的 deepseek")
        self.assertEqual(result["name"], "我的 deepseek")

    def test_approve_twice_reports_error(self):
        inbox_id = self.capture()["results"][0]["inbox_id"]
        self.approve(inbox_id)
        result = self.approve(inbox_id)
        self.assertIn("error", result)

    def test_reject_marks_rejected(self):
        inbox_id = self.capture()["results"][0]["inbox_id"]
        result = service.handle(self.store, {"type": "reject", "inbox_id": inbox_id})
        self.assertTrue(result["ok"])
        from kv.core import repo

        with self.store.read() as conn:
            self.assertEqual(repo.get_inbox(conn, inbox_id)["status"], "rejected")
        # 拒绝后再捕获同值：不再 pending（rejected 不占部分唯一索引）→ 重新 spool
        again = self.capture()["results"][0]
        self.assertEqual(again["status"], "pending")


class TestListCopy(HostServiceTestCase):

    def test_list_returns_masked_rows_only(self):
        self.capture()
        self.approve(self.capture()["results"][0]["inbox_id"])
        response = service.handle(self.store, {"type": "list", "query": "deep"})
        self.assertEqual(len(response["rows"]), 1)
        row = response["rows"][0]
        self.assertEqual(row["platform"], "deepseek")
        self.assertNotIn("sk-abc123def456", str(row))

    def test_copy_writes_clipboard_and_schedules_wipe(self):
        self.capture()
        name = self.approve(self.capture()["results"][0]["inbox_id"])["name"]
        response = service.handle(self.store, {"type": "copy", "name": name},
                                  backend=self.clipboard)
        self.assertTrue(response["ok"])
        self.assertEqual(response["ttl"], 30)
        self.assertTrue(response["excluded_history"])
        self.assertEqual(len(self.spawns), 1)  # 擦除 helper 已排定

    def test_copy_unknown_name_errors(self):
        result = service.handle(self.store, {"type": "copy", "name": "ghost"})
        self.assertIn("error", result)


class TestUninitializedStore(HostServiceTestCase):

    def test_ping_reports_not_initialized(self):
        result = service.handle(None, {"type": "ping"})
        self.assertTrue(result["pong"])
        self.assertFalse(result["initialized"])

    def test_other_requests_report_guidance(self):
        result = service.handle(None, {"type": "list"})
        self.assertIn("vault 未初始化", result["error"])

    def test_unknown_type(self):
        result = service.handle(self.store, {"type": "wat"})
        self.assertIn("未知请求类型", result["error"])


if __name__ == "__main__":
    unittest.main()
