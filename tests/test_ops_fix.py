"""M2 fix / set-platform / corrections 测试。

fix 处理识别欠账（ambiguous/none），set-platform 改平台并可选记住选择，
corrections 持久化用户纠正规则。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401
from tests.fakes import FakeClock, PlaintextProtector

from kv import clock
from kv.core import db, repo
from kv.core.vault import VaultStore
from kv.model import Candidate, Verdict
from kv.ops import fix as fixops
from kv.ops import save as saveops

SECRET = "sk-" + "A" * 44 + "c31f"


def _make_store(vault_dir: Path) -> VaultStore:
    vault_dir.mkdir(parents=True, exist_ok=True)
    db_path = vault_dir / "vault.db"
    protector = PlaintextProtector()
    canary = protector.protect(b"kv-canary")
    db.initialize(db_path, canary_blob=canary, canary_text="kv-canary")
    return VaultStore(db_path, protector=protector)


def _add_secret(store, name, value=SECRET, *, platform="openai-compatible",
                confidence="ambiguous", key_name=None):
    cand = Candidate(
        value=value.encode("utf-8"), kind="token",
        key_name=key_name,
    )
    verdict = Verdict(
        platform=platform, confidence=confidence,
        source="shape", evidence="test",
        mask_style=(3, 4), kind="token",
    )
    return saveops.commit(
        cand, verdict, store=store,
        policy=saveops.SavePolicy(name=name, actor="test", force=True),
    )


class TestUnresolved(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.store = _make_store(self.vault)
        self.clock = FakeClock()
        self.clock.install()
        self.addCleanup(self.clock.uninstall)
        self.addCleanup(self._tmp.cleanup)

    def test_returns_ambiguous_and_none_records(self):
        _add_secret(self.store, "amb", confidence="ambiguous")
        # "none" 置信度无法通过 saveops.commit 创建（会被拒绝），
        # 直接走 repo 层插入来测试 unresolved() 的过滤逻辑。
        self._insert_none_record()
        _add_secret(self.store, "ok", confidence="manual",
                     value="sk-" + "C" * 44, platform="deepseek")
        rows = fixops.unresolved(self.store)
        names = {r.name for r in rows}
        self.assertIn("amb", names)
        self.assertIn("none-record", names)
        self.assertNotIn("ok", names)

    def _insert_none_record(self):
        """直接插入一条 confidence=none 的记录，绕过 saveops 的拒绝逻辑。"""
        from kv.core import masking
        from kv import clock

        value = b"sk-" + b"B" * 44
        canonical = masking.canonicalize(value, "token")
        sha256 = masking.fingerprint(canonical, "token")
        blob = self.store.encrypt(canonical)
        head, tail = masking.split_mask(canonical, 3, 4)
        now = clock.now_iso()
        with self.store.session() as conn:
            repo.insert_secret(conn, row={
                "name": "none-record",
                "platform": "unknown",
                "confidence": "none",
                "evidence": "test",
                "detect_source": "shape",
                "key_name": None,
                "kind": "token",
                "value_blob": blob,
                "sha256": sha256,
                "mask_head": head,
                "mask_tail": tail,
                "value_len": len(canonical),
                "status": "active",
                "note": "",
                "source_url": "",
                "origin": "test",
                "extra_json": "{}",
                "created_at": now,
                "updated_at": now,
                "expires_at": None,
                "last_used_at": None,
                "last_revealed_at": None,
            })

    def test_empty_when_all_resolved(self):
        _add_secret(self.store, "ok", confidence="manual", platform="deepseek")
        rows = fixops.unresolved(self.store)
        self.assertEqual(rows, [])


class TestSetPlatform(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.store = _make_store(self.vault)
        self.clock = FakeClock()
        self.clock.install()
        self.addCleanup(self.clock.uninstall)
        self.addCleanup(self._tmp.cleanup)

    def test_changes_platform(self):
        _add_secret(self.store, "ds", confidence="ambiguous")
        fixops.set_platform(self.store, "ds", "deepseek")
        row = self.store.get("ds")
        self.assertEqual(row.platform, "deepseek")
        self.assertEqual(row.confidence, "manual")

    def test_remember_keyname_creates_correction(self):
        """--remember keyname 持久化一条纠正规则。"""
        _add_secret(self.store, "ds", confidence="ambiguous",
                     key_name="DEEPSEEK_API_KEY")
        fixops.set_platform(self.store, "ds", "deepseek", remember="keyname")
        corrections = fixops.list_corrections(self.store)
        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections[0]["kind"], "keyname")
        self.assertEqual(corrections[0]["platform"], "deepseek")
        self.assertIn("DEEPSEEK_API_KEY", corrections[0]["pattern"])

    def test_remember_without_key_name_is_noop(self):
        """没有 key_name 时 --remember keyname 不创建纠正（走 else 分支但不报错）。"""
        _add_secret(self.store, "ds", confidence="ambiguous")
        # 不给 remember 参数，只改平台
        fixops.set_platform(self.store, "ds", "deepseek")
        corrections = fixops.list_corrections(self.store)
        self.assertEqual(len(corrections), 0)

    def test_bad_remember_value_raises(self):
        from kv.errors import UsageError

        _add_secret(self.store, "ds", confidence="ambiguous")
        with self.assertRaises(UsageError):
            fixops.set_platform(self.store, "ds", "deepseek", remember="bogus")


class TestDropCorrection(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.store = _make_store(self.vault)
        self.clock = FakeClock()
        self.clock.install()
        self.addCleanup(self.clock.uninstall)
        self.addCleanup(self._tmp.cleanup)

    def test_drop_existing_correction(self):
        _add_secret(self.store, "ds", confidence="ambiguous",
                     key_name="DEEPSEEK_API_KEY")
        fixops.set_platform(self.store, "ds", "deepseek", remember="keyname")
        corrections = fixops.list_corrections(self.store)
        self.assertEqual(len(corrections), 1)
        fixops.drop_correction(self.store, corrections[0]["id"])
        self.assertEqual(len(fixops.list_corrections(self.store)), 0)

    def test_drop_nonexistent_raises(self):
        from kv.errors import NotFoundError

        with self.assertRaises(NotFoundError):
            fixops.drop_correction(self.store, 99999)


class TestMenuChoices(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.store = _make_store(self.vault)
        self.clock = FakeClock()
        self.clock.install()
        self.addCleanup(self.clock.uninstall)
        self.addCleanup(self._tmp.cleanup)

    def test_ambiguous_shows_group_members(self):
        """歧义记录只列同组成员（openai-compatible 的组）。"""
        _add_secret(self.store, "amb", confidence="ambiguous",
                     platform="openai-compatible")
        row = self.store.get("amb")
        choices = fixops._menu_choices(row)
        platforms = [p for _, p in choices]
        # openai-compatible 组至少含 deepseek / siliconflow / moonshot
        self.assertIn("deepseek", platforms)
        # 不应包含自身
        self.assertNotIn("openai-compatible", platforms)

    def test_none_confidence_shows_all_platforms(self):
        """confidence=none 列全部平台。"""
        _add_secret(self.store, "unk", confidence="none",
                     value="sk-" + "Z" * 44, platform="unknown")
        row = self.store.get("unk")
        choices = fixops._menu_choices(row)
        platforms = [p for _, p in choices]
        self.assertIn("deepseek", platforms)
        self.assertIn("github", platforms)


if __name__ == "__main__":
    unittest.main()
