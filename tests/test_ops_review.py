"""M2 review 测试：PendingItem、review()、spool()、pending_from_inbox()。

review 是 watch 和 review 命令共用的接缝 —— 内存队列和 spool 表都经它。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401
from tests.fakes import FakeClock, PlaintextProtector

from kv import clock
from kv.core import db, masking, repo
from kv.core.vault import VaultStore
from kv.detect.corrections import CorrectionSet
from kv.detect import resolve
from kv.model import Candidate, Verdict
from kv.ops import review as reviewops
from kv.ops import save as saveops
from kv.parse import pipeline

SECRET = "sk-" + "A" * 44 + "c31f"
GITHUB_TOKEN = "ghp_" + "B" * 36


def _make_store(vault_dir: Path) -> VaultStore:
    vault_dir.mkdir(parents=True, exist_ok=True)
    db_path = vault_dir / "vault.db"
    protector = PlaintextProtector()
    canary = protector.protect(b"kv-canary")
    db.initialize(db_path, canary_blob=canary, canary_text="kv-canary")
    return VaultStore(db_path, protector=protector)


def _make_item(text: str, *, source: str = "manual") -> reviewops.PendingItem:
    result = pipeline.parse(text, source_hint=source)
    candidate = result.candidates[0]
    corrections = CorrectionSet.empty()
    verdict = resolve.resolve(candidate, corrections)
    return reviewops.PendingItem(candidate=candidate, verdict=verdict,
                                 captured_at=clock.now_iso())


class TestPendingItem(unittest.TestCase):
    def test_auto_acceptable_for_high_confidence(self):
        item = _make_item(f"DEEPSEEK_API_KEY={SECRET}")
        self.assertTrue(item.auto_acceptable)

    def test_not_auto_acceptable_for_ambiguous(self):
        item = _make_item(SECRET)  # bare sk- → ambiguous
        self.assertFalse(item.auto_acceptable)

    def test_not_auto_acceptable_for_unknown(self):
        item = reviewops.PendingItem(
            candidate=Candidate(value=b"random"),
            verdict=Verdict(platform="unknown", confidence="none",
                            evidence="", mask_style=(0, 4), kind="token"),
        )
        self.assertFalse(item.auto_acceptable)


class TestReview(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.store = _make_store(self.vault)
        self.clock = FakeClock()
        self.clock.install()
        self.addCleanup(self.clock.uninstall)
        self.addCleanup(self._tmp.cleanup)

    def test_review_auto_accepts_high_confidence(self):
        """auto=True 接受 exact/high/manual 的候选。"""
        item = _make_item(f"DEEPSEEK_API_KEY={SECRET}")
        outcome = reviewops.review(
            [item], self.store, interactive=False, auto=True, actor="test",
        )
        self.assertEqual(outcome.accepted, 1)
        self.assertEqual(outcome.skipped, 0)

    def test_review_skips_ambiguous_in_auto_mode(self):
        """auto=True 不够自动接受门槛的 → skipped。"""
        item = _make_item(SECRET)  # bare sk- → ambiguous
        outcome = reviewops.review(
            [item], self.store, interactive=False, auto=True, actor="test",
        )
        self.assertEqual(outcome.skipped, 1)
        self.assertEqual(outcome.accepted, 0)

    def test_review_dedupes_existing_secret(self):
        """已存在的值 → deduped。"""
        # 先存一条
        cand = Candidate(value=SECRET.encode("utf-8"), kind="token")
        verdict = Verdict(platform="deepseek", confidence="manual",
                          source="correction", evidence="test",
                          mask_style=(3, 4), kind="token")
        saveops.commit(cand, verdict, store=self.store,
                       policy=saveops.SavePolicy(name="existing", actor="test"))
        # 再 review 同一个值
        item = _make_item(f"DEEPSEEK_API_KEY={SECRET}")
        outcome = reviewops.review(
            [item], self.store, interactive=False, auto=True, actor="test",
        )
        self.assertEqual(outcome.deduped, 1)

    def test_review_on_decided_callback_fires(self):
        """on_decided 回调在每条决定后被调用。"""
        item = _make_item(f"DEEPSEEK_API_KEY={SECRET}")
        decided = []
        reviewops.review(
            [item], self.store, interactive=False, auto=True, actor="test",
            on_decided=lambda i, a, r: decided.append((a, r)),
        )
        self.assertEqual(len(decided), 1)
        self.assertEqual(decided[0][0], "accept")


class TestSpool(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.store = _make_store(self.vault)
        self.clock = FakeClock()
        self.clock.install()
        self.addCleanup(self.clock.uninstall)
        self.addCleanup(self._tmp.cleanup)

    def test_spool_writes_encrypted_inbox_row(self):
        """spool() 把候选 DPAPI 加密后写进 inbox。"""
        item = _make_item(f"DEEPSEEK_API_KEY={SECRET}")
        inbox_id = reviewops.spool(self.store, item)
        self.assertIsNotNone(inbox_id)
        with self.store.read() as conn:
            rows = repo.list_inbox(conn, "pending")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["platform"], "deepseek")

    def test_spool_duplicate_sha256_raises(self):
        """已存在的 sha256 → spool 抛 IntegrityError（只查 secret 表，不查 inbox）。"""
        import sqlite3
        item = _make_item(f"DEEPSEEK_API_KEY={SECRET}")
        reviewops.spool(self.store, item)
        with self.assertRaises(sqlite3.IntegrityError):
            reviewops.spool(self.store, item)

    def test_pending_from_inbox_reconstructs_queue(self):
        """pending_from_inbox() 从 inbox 表重建 PendingItem 队列。"""
        item = _make_item(f"DEEPSEEK_API_KEY={SECRET}")
        reviewops.spool(self.store, item)
        items = reviewops.pending_from_inbox(self.store)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].verdict.platform, "deepseek")
        self.assertIsNotNone(items[0].inbox_id)


class TestDescribeItem(unittest.TestCase):
    def test_describe_includes_source_and_span(self):
        item = _make_item(f"DEEPSEEK_API_KEY={SECRET}", source="clipboard")
        desc = reviewops.describe_item(1, 3, item)
        self.assertIn("[1/3]", desc)
        self.assertIn("clipboard", desc)

    def test_describe_includes_key_name_when_present(self):
        item = _make_item(f"DEEPSEEK_API_KEY={SECRET}")
        desc = reviewops.describe_item(1, 1, item)
        self.assertIn("DEEPSEEK_API_KEY", desc)


if __name__ == "__main__":
    unittest.main()
