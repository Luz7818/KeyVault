"""备份/恢复与健康报告的 ops 层测试。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import tests  # noqa: F401
from tests.fakes import FakeClock, PlaintextProtector

from kv import errors
from kv.core import db
from kv.core.vault import VaultStore
from kv.crypto import dpapi
from kv.model import Candidate, Verdict
from kv.ops import backup, health, save as saveops
from kv.core import masking

TRUSTED = Verdict(platform="deepseek", confidence="exact", source="correction",
                  evidence="test", mask_style=masking.infer_mask_style(b"sk-x" + b"A" * 40),
                  kind="token")


def make_vault(root: Path) -> VaultStore:
    root.mkdir(parents=True, exist_ok=True)
    protector = PlaintextProtector()
    db.initialize(root / "vault.db", canary_blob=protector.protect(b"kv-canary"),
                  canary_text="kv-canary")
    return VaultStore(root / "vault.db", protector=protector)


def add(store: VaultStore, name: str, value: str, **policy) -> None:
    cand = Candidate(value=value.encode(), kind="token", source="test", span="test")
    saveops.commit(cand, TRUSTED, store=store,
                   policy=saveops.SavePolicy(name=name, origin="test", actor="t", force=True, **policy))


class TestBackup(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = make_vault(self.root / "v1")
        add(self.store, "api-1", "sk-" + "A" * 44 + "c31f", tags=("prod",))
        add(self.store, "api-2", "ghp_" + "B" * 36 + "zz")
        self.target = self.root / "backup.kvb"
        self.addCleanup(self._tmp.cleanup)

    def test_export_restore_roundtrip(self):
        """导出 → 换一个新 vault 恢复 → 数量与明文值完全一致。"""
        result = backup.export_backup(self.store, self.target, "correct horse")
        self.assertEqual(result.count, 2)
        self.assertTrue(self.target.exists())

        other = make_vault(self.root / "v2")
        restored = backup.restore_backup(other, self.target, "correct horse")
        self.assertEqual(restored.total, 2)
        self.assertEqual(restored.created, 2)
        names = {r.name for r in other.list_secrets()}
        self.assertEqual(names, {"api-1", "api-2"})
        with other.reveal("api-1") as buf:
            self.assertEqual(bytes(buf).decode(), "sk-" + "A" * 44 + "c31f")
        row = next(r for r in other.list_secrets() if r.name == "api-1")
        self.assertEqual(row.tags, ("prod",))

    def test_wrong_password_rejected(self):
        backup.export_backup(self.store, self.target, "correct horse")
        other = make_vault(self.root / "v3")
        with self.assertRaises(errors.BackupAuthError):
            backup.restore_backup(other, self.target, "wrong password")

    def test_tampered_ciphertext_rejected(self):
        backup.export_backup(self.store, self.target, "correct horse")
        blob = bytearray(self.target.read_bytes())
        blob[len(blob) // 2] ^= 0xFF  # 翻转密文中段一字节
        self.target.write_bytes(bytes(blob))
        other = make_vault(self.root / "v4")
        with self.assertRaises(errors.BackupAuthError):
            backup.restore_backup(other, self.target, "correct horse")

    def test_bad_magic_rejected(self):
        self.target.write_bytes(b"NOTKV" + b"\x00" * 128)
        other = make_vault(self.root / "v5")
        with self.assertRaises(errors.BackupError):
            backup.restore_backup(other, self.target, "pw123456")

    def test_weak_password_refused(self):
        with self.assertRaises(errors.UsageError):
            backup.export_backup(self.store, self.target, "short")

    def test_restore_twice_dedupes(self):
        backup.export_backup(self.store, self.target, "correct horse")
        other = make_vault(self.root / "v6")
        backup.restore_backup(other, self.target, "correct horse")
        again = backup.restore_backup(other, self.target, "correct horse")
        self.assertEqual(again.created, 0)
        self.assertEqual(again.deduped, 2)
        self.assertEqual(len(other.list_secrets()), 2)


def _add(store: VaultStore, name: str, *, expires_at: str | None = None) -> None:
    """每条值必须不同：同值会被 saveops 去重，不会创建第二行。"""
    value = f"sk-{name:<42}" + "ddd"
    cand = Candidate(value=value.encode(), kind="token", source="test", span="test")
    saveops.commit(cand, TRUSTED, store=store,
                   policy=saveops.SavePolicy(name=name, origin="test", actor="t", force=True,
                                             expires_at=expires_at))


class TestHealth(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = make_vault(self.root / "v")
        self.addCleanup(self._tmp.cleanup)

    def test_expiring_and_stale(self):
        now = datetime(2026, 10, 7, 12, 0, 0).astimezone()
        _add(self.store, "soon", expires_at=(now + timedelta(days=10)).isoformat())
        _add(self.store, "later", expires_at=(now + timedelta(days=90)).isoformat())
        _add(self.store, "plain")
        # plain 的 updated_at 是「现在」，要走 stale 分支得把 now 推到未来
        report = health.health_report(self.store, now=now + timedelta(days=200))
        self.assertNotIn("soon", report.expiring)      # 200 天前早已过期 → 进 expired
        self.assertEqual(report.expired_count, 2)      # soon / later 都过期
        self.assertIn("later", report.stale)
        self.assertIn("plain", report.stale)

    def test_expiring_window(self):
        now = datetime(2026, 10, 7, 12, 0, 0).astimezone()
        _add(self.store, "edge", expires_at=(now + timedelta(days=29)).isoformat())
        report = health.health_report(self.store, now=now)
        self.assertEqual(report.expiring, ("edge",))
        self.assertEqual(report.expired_count, 0)

    def test_revoked_ignored(self):
        now = datetime(2026, 10, 7, 12, 0, 0).astimezone()
        _add(self.store, "dead", expires_at=(now - timedelta(days=5)).isoformat())
        from kv.ops.rotate import revoke

        revoke(self.store, "dead")
        report = health.health_report(self.store, now=now)
        self.assertEqual(report.expired_count, 0)
        self.assertEqual(report.expiring, ())


if __name__ == "__main__":
    unittest.main()
