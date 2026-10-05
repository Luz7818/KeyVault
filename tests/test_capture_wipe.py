"""M2 wipe 测试：copy → 30 秒擦除、争用跳过、清扫兜底。

用 FakeClipboard 代替真 Win32 剪切板。PlaintextProtector 代替真 DPAPI。
不测子进程 spawn（那是 Windows 集成测试的活）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401
from tests.fakes import FakeClipboard, FakeClock, PlaintextProtector

from kv import clock
from kv.capture import wipe
from kv.core import db, masking, repo
from kv.core.vault import VaultStore
from kv.model import Candidate, Verdict
from kv.ops import save as saveops

SECRET = "sk-" + "A" * 44 + "c31f"


def _make_store(vault_dir: Path) -> VaultStore:
    vault_dir.mkdir(parents=True, exist_ok=True)
    db_path = vault_dir / "vault.db"
    protector = PlaintextProtector()
    canary = protector.protect(b"kv-canary")
    db.initialize(db_path, canary_blob=canary, canary_text="kv-canary")
    return VaultStore(db_path, protector=protector)


def _add_secret(store, name="deepseek-main", value=SECRET, platform="deepseek"):
    cand = Candidate(value=value.encode("utf-8"), kind="token")
    verdict = Verdict(
        platform=platform, confidence="manual", source="correction",
        evidence="test", mask_style=(3, 4), kind="token",
    )
    return saveops.commit(cand, verdict, store=store,
                          policy=saveops.SavePolicy(name=name, actor="test"))


class TestRunWipe(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.store = _make_store(self.vault)
        self.clock = FakeClock()
        self.clock.install()
        self.addCleanup(self.clock.uninstall)
        self.addCleanup(self._tmp.cleanup)

    def test_wipe_missing_id_returns_missing(self):
        backend = FakeClipboard()
        result = wipe.run_wipe(self.store, 99999, backend, wait=False)
        self.assertEqual(result, "missing")

    def test_wipe_fingerprint_match_overwrites_clipboard(self):
        """指纹匹配 → 覆盖剪切板为 benign text，状态变 done。"""
        _add_secret(self.store)
        backend = FakeClipboard()
        # 手动插一条 pending_wipe
        fp = masking.fingerprint(SECRET.encode("utf-8"), "token")
        with self.store.session() as conn:
            wipe_id = repo.insert_pending_wipe(
                conn, sha256=fp, secret_id=1,
                deadline_at=clock.plus_seconds(30),
            )
        # 让剪切板上的内容指纹匹配
        backend.content = SECRET
        result = wipe.run_wipe(self.store, wipe_id, backend, wait=False)
        self.assertEqual(result, "done")
        # 剪切板现在应该是 benign text
        self.assertEqual(backend.content, wipe.BENIGN_TEXT)
        # 审计里应该有 wipe 事件
        with self.store.read() as conn:
            rows = repo.all_audit_rows(conn)
        events = [r["event"] for r in rows]
        self.assertIn("wipe", events)

    def test_wipe_fingerprint_mismatch_skips(self):
        """指纹不匹配 → 跳过，不覆盖用户后来复制的东西。"""
        _add_secret(self.store)
        backend = FakeClipboard()
        fp = masking.fingerprint(SECRET.encode("utf-8"), "token")
        with self.store.session() as conn:
            wipe_id = repo.insert_pending_wipe(
                conn, sha256=fp, secret_id=1,
                deadline_at=clock.plus_seconds(30),
            )
        # 剪切板上是别的东西
        backend.content = "something else entirely"
        result = wipe.run_wipe(self.store, wipe_id, backend, wait=False)
        self.assertEqual(result, "skipped")
        # 用户的内容存活
        self.assertEqual(backend.content, "something else entirely")

    def test_wipe_already_done_returns_status(self):
        """已经擦过的再擦一次 → 返回 already-done。"""
        _add_secret(self.store)
        backend = FakeClipboard()
        fp = masking.fingerprint(SECRET.encode("utf-8"), "token")
        with self.store.session() as conn:
            wipe_id = repo.insert_pending_wipe(
                conn, sha256=fp, secret_id=1,
                deadline_at=clock.plus_seconds(30),
            )
            repo.mark_wipe(conn, wipe_id, "done")
        result = wipe.run_wipe(self.store, wipe_id, backend, wait=False)
        self.assertEqual(result, "already-done")


class TestSweepDueWipes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.store = _make_store(self.vault)
        self.clock = FakeClock()
        self.clock.install()
        self.addCleanup(self.clock.uninstall)
        self.addCleanup(self._tmp.cleanup)

    def test_sweep_cleans_expired_entries(self):
        """过期的 pending_wipe 被清扫。"""
        _add_secret(self.store)
        backend = FakeClipboard()
        fp = masking.fingerprint(SECRET.encode("utf-8"), "token")
        # 设一个已经过期的 deadline
        self.clock.advance(hours=1)
        with self.store.session() as conn:
            repo.insert_pending_wipe(
                conn, sha256=fp, secret_id=1,
                deadline_at=clock.plus_seconds(-60),  # 已经过期
            )
        backend.content = SECRET
        swept = wipe.sweep_due_wipes(self.store, backend, actor="test")
        self.assertEqual(len(swept), 1)
        self.assertEqual(swept[0], "done")

    def test_sweep_with_no_due_entries_returns_empty(self):
        backend = FakeClipboard()
        swept = wipe.sweep_due_wipes(self.store, backend, actor="test")
        self.assertEqual(swept, [])


class TestCopySecret(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.store = _make_store(self.vault)
        self.clock = FakeClock()
        self.clock.install()
        self.addCleanup(self.clock.uninstall)
        self.addCleanup(self._tmp.cleanup)

    def test_copy_writes_clipboard_and_records_wipe(self):
        """copy_secret 把值放上剪切板，并创建一条 pending_wipe。"""
        _add_secret(self.store)
        backend = FakeClipboard()
        result = wipe.copy_secret(
            self.store, "deepseek-main", backend, ttl=30.0,
            actor="test", wipe=True, exclude_history=True,
        )
        self.assertEqual(result.name, "deepseek-main")
        self.assertEqual(result.platform, "deepseek")
        self.assertTrue(result.excluded_from_history)
        self.assertIsNotNone(result.wipe_id)
        # 剪切板上的内容是明文值
        self.assertEqual(backend.content, SECRET)
        # 写入时带了 exclude_history=True
        self.assertTrue(backend.writes[-1][1])

    def test_copy_without_wipe_leaves_no_pending(self):
        """wipe=False 不创建 pending_wipe。"""
        _add_secret(self.store)
        backend = FakeClipboard()
        result = wipe.copy_secret(
            self.store, "deepseek-main", backend, ttl=30.0,
            actor="test", wipe=False,
        )
        self.assertIsNone(result.wipe_id)
        self.assertEqual(result.ttl, 0.0)

    def test_copy_exclude_history_flag_propagates(self):
        """exclude_history=False 时写入不带排除标记。"""
        _add_secret(self.store)
        backend = FakeClipboard()
        result = wipe.copy_secret(
            self.store, "deepseek-main", backend, ttl=30.0,
            actor="test", wipe=False, exclude_history=False,
        )
        self.assertFalse(result.excluded_from_history)
        self.assertFalse(backend.writes[-1][1])


if __name__ == "__main__":
    unittest.main()
