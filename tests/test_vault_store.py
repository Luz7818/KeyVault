"""存储层：schema、绑定金丝雀、去重、审计链与追加式触发器、明文窗口归零。"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401
from tests.fakes import FakeClock, PlaintextProtector

from kv import errors
from kv.core import audit as auditlog
from kv.core import db, masking, repo
from kv.core.vault import VaultStore
from kv.crypto import dpapi
from kv.model import CONFIDENCE_EXACT, CONFIDENCE_NONE, Candidate, Verdict
from kv.ops import save as saveops

WINDOWS = sys.platform == "win32"

VALUE = b"sk-" + b"A" * 44 + b"c31f"
TRUSTED = Verdict(platform="deepseek", confidence=CONFIDENCE_EXACT, source="hostname",
                  evidence="hostname api.deepseek.com", mask_style=(0, 4))
UNTRUSTED = Verdict(platform="unknown", confidence=CONFIDENCE_NONE, source="none",
                    evidence="没有任何规则匹配")


def make_vault(root: Path, protector=None) -> VaultStore:
    store_protector = protector or PlaintextProtector()
    db_path = root / "vault.db"
    canary = store_protector.protect(dpapi.CANARY_TEXT.encode("utf-8"))
    db.initialize(db_path, canary_blob=canary, canary_text=dpapi.CANARY_TEXT)
    return VaultStore(db_path, store_protector)


class VaultTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.fake = FakeClock().install()
        self.store = make_vault(self.root)
        self.addCleanup(self._teardown)

    def _teardown(self):
        self.fake.uninstall()
        self._tmp.cleanup()

    def add_one(self, value: bytes = VALUE, name: str = "", **policy) -> saveops.SaveResult:
        candidate = Candidate(value=value, key_name=policy.pop("key_name", None), source="manual")
        verdict = policy.pop("verdict", TRUSTED)
        return saveops.commit(
            candidate, verdict, store=self.store,
            policy=saveops.SavePolicy(name=name, actor="test", **policy),
        )


class TestSchema(VaultTestCase):
    EXPECTED_TABLES = {
        "meta", "binding", "secret", "fingerprint", "alias", "tag", "secret_tag",
        "correction", "audit", "pending_wipe", "inbox", "setting",
    }

    def table_names(self):
        with self.store.read() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        return {r["name"] for r in rows}

    def test_all_tables_created(self):
        self.assertTrue(self.EXPECTED_TABLES <= self.table_names(),
                        f"缺表：{self.EXPECTED_TABLES - self.table_names()}")

    def test_meta_records_version(self):
        with self.store.read() as conn:
            self.assertEqual(repo.get_meta(conn, "schema_version"), "1")
            self.assertEqual(repo.get_meta(conn, "created_at"), self.fake.iso())

    def test_binding_canary_present_and_verifies(self):
        self.store.verify_binding()

    def test_wal_mode_enabled(self):
        with self.store.read() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

    def test_foreign_keys_on(self):
        with self.store.read() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_inbox_pending_index_is_partial_and_unique(self):
        """同一个 sha256 可以有两条已决定的行，但不能有两条 pending。"""
        with self.store.session() as conn:
            for status in ("accepted", "rejected"):
                repo.insert_inbox(conn, row={
                    "sha256": "a" * 64, "mask_head": "", "mask_tail": "",
                    "value_len": 1,
                    "value_blob": b"x", "kind": "token", "key_name": None,
                    "platform": "deepseek", "confidence": "exact", "evidence": "",
                    "candidates_json": "[]", "suggested_name": "n", "source": "clipboard",
                    "context_json": "{}", "captured_at": self.fake.iso(),
                    "ttl_until": self.fake.iso(), "status": status,
                })
            with self.assertRaises(sqlite3.IntegrityError):
                repo.insert_inbox(conn, row={
                    "sha256": "b" * 64, "mask_head": "", "mask_tail": "",
                    "value_len": 1,
                    "value_blob": b"x", "kind": "token", "key_name": None,
                    "platform": "deepseek", "confidence": "exact", "evidence": "",
                    "candidates_json": "[]", "suggested_name": "n", "source": "clipboard",
                    "context_json": "{}", "captured_at": self.fake.iso(),
                    "ttl_until": self.fake.iso(), "status": "pending",
                })
                repo.insert_inbox(conn, row={
                    "sha256": "b" * 64, "mask_head": "", "mask_tail": "",
                    "value_len": 1,
                    "value_blob": b"x", "kind": "token", "key_name": None,
                    "platform": "deepseek", "confidence": "exact", "evidence": "",
                    "candidates_json": "[]", "suggested_name": "n", "source": "clipboard",
                    "context_json": "{}", "captured_at": self.fake.iso(),
                    "ttl_until": self.fake.iso(), "status": "pending",
                })


class TestAuditAppendOnly(VaultTestCase):
    def test_update_is_blocked_by_trigger(self):
        self.store.record_audit("doctor", "test", detail={"ok": True})
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            with self.store.session() as conn:
                conn.execute("UPDATE audit SET event = 'tampered'")
        self.assertIn("append-only", str(ctx.exception))

    def test_delete_is_blocked_by_trigger(self):
        self.store.record_audit("doctor", "test")
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            with self.store.session() as conn:
                conn.execute("DELETE FROM audit")
        self.assertIn("append-only", str(ctx.exception))

    def test_row_survives_after_failed_tamper(self):
        self.store.record_audit("doctor", "test")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.session() as conn:
                conn.execute("DELETE FROM audit")
        self.assertEqual(len(self.store.list_audit(limit=100)), 1)


class TestAuditChain(VaultTestCase):
    def test_empty_log_verifies(self):
        result = self.store.verify_audit()
        self.assertTrue(result.ok)
        self.assertEqual(result.count, 0)

    def test_chain_verifies_after_many_events(self):
        for i in range(25):
            self.store.record_audit("doctor", "test", detail={"i": i})
        result = self.store.verify_audit()
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.count, 25)

    def test_tampering_a_field_breaks_the_chain(self):
        """触发器挡住了正常的 UPDATE，但有人可以先 drop 触发器。
        chain_hash 就是为这种情况准备的：删改行仍然可被发现。"""
        for i in range(5):
            self.store.record_audit("doctor", "test", detail={"i": i})
        with self.store.session() as conn:
            conn.execute("DROP TRIGGER audit_no_update")
            conn.execute("UPDATE audit SET actor = 'someone-else' WHERE id = 3")
        result = self.store.verify_audit()
        self.assertFalse(result.ok)
        self.assertEqual(result.first_bad_id, 3)

    def test_deleting_a_middle_row_breaks_the_chain(self):
        for i in range(5):
            self.store.record_audit("doctor", "test", detail={"i": i})
        with self.store.session() as conn:
            conn.execute("DROP TRIGGER audit_no_delete")
            conn.execute("DELETE FROM audit WHERE id = 2")
        result = self.store.verify_audit()
        self.assertFalse(result.ok)

    def test_canonicalization_is_deterministic(self):
        a = repo.canonical_json({"b": 1, "a": 2})
        b = repo.canonical_json({"a": 2, "b": 1})
        self.assertEqual(a, b)

    def test_chain_hash_depends_on_id(self):
        base = dict(ts="t", event="e", actor="a", secret_id=None,
                    name_snapshot=None, sha256_prefix=None, detail_json="{}")
        h1 = auditlog.compute_chain("prev", auditlog.canonical_row(audit_id=1, **base))
        h2 = auditlog.compute_chain("prev", auditlog.canonical_row(audit_id=2, **base))
        self.assertNotEqual(h1, h2)


class TestAuditDetailSanitizer(VaultTestCase):
    def test_rejects_bytes_values(self):
        with self.assertRaises(errors.KvError):
            auditlog.sanitize_detail({"blob": b"anything"})

    def test_rejects_secret_flavoured_keys(self):
        for key in ("value", "token", "password", "api_key", "plaintext"):
            with self.subTest(key=key), self.assertRaises(errors.KvError):
                auditlog.sanitize_detail({key: "even-a-short-string"})

    def test_truncates_long_strings(self):
        out = auditlog.sanitize_detail({"note": "x" * 500})
        self.assertLess(len(out["note"]), 260)
        self.assertIn("+", out["note"])

    def test_passes_through_safe_fields(self):
        out = auditlog.sanitize_detail({"platform": "deepseek", "len": 48, "code": 13})
        self.assertEqual(out, {"platform": "deepseek", "len": 48, "code": 13})

    def test_prefix_is_truncated_to_12(self):
        self.store.record_audit("doctor", "test", sha256_prefix="a" * 64)
        row = self.store.list_audit(limit=1)[0]
        self.assertEqual(len(row["sha256_prefix"]), 12)


class TestSaveAndDedupe(VaultTestCase):
    def test_creates_record(self):
        result = self.add_one(name="deepseek-main")
        self.assertEqual(result.outcome, "created")
        self.assertEqual(result.name, "deepseek-main")
        row = self.store.get("deepseek-main")
        self.assertEqual(row.platform, "deepseek")
        self.assertEqual(row.confidence, CONFIDENCE_EXACT)
        self.assertEqual(row.evidence, "hostname api.deepseek.com")
        self.assertEqual(row.value_len, len(VALUE))
        self.assertEqual(row.created_at, self.fake.iso())

    def test_trailing_newline_dedupes(self):
        """剪切板复制常带尾部换行 —— 不规范化就会每次复制都产生一条重复行。"""
        first = self.add_one(name="a", value=VALUE)
        second = self.add_one(name="b", value=VALUE + b"\r\n")
        self.assertEqual(first.outcome, "created")
        self.assertEqual(second.outcome, "deduped")
        self.assertEqual(second.secret_id, first.secret_id)
        self.assertEqual(self.store.count(), 1)

    def test_dedupe_adds_alias_instead_of_second_row(self):
        first = self.add_one(name="primary")
        self.add_one(name="secondary", value=VALUE + b"\n")
        row = self.store.by_id(first.secret_id)
        self.assertIn("secondary", row.aliases)
        self.assertEqual(self.store.get("secondary").id, first.secret_id)

    def test_dedupe_merges_tags(self):
        self.add_one(name="a", tags=("prod",))
        result = self.add_one(name="b", value=VALUE + b"\n", tags=("ci", "prod"))
        self.assertEqual(result.outcome, "deduped")
        self.assertEqual(set(self.store.get("a").tags), {"prod", "ci"})

    def test_dedupe_writes_audit_not_a_second_save(self):
        self.add_one(name="a")
        self.add_one(name="b", value=VALUE + b"\n")
        events = [r["event"] for r in self.store.list_audit(limit=100)]
        self.assertEqual(events.count("save"), 1)
        self.assertEqual(events.count("dedupe_hit"), 1)

    def test_readding_a_revoked_value_warns_loudly(self):
        self.add_one(name="a")
        with self.store.session() as conn:
            conn.execute("UPDATE secret SET status = 'revoked' WHERE name = 'a'")
            conn.execute("UPDATE fingerprint SET status = 'revoked', revoked_at = ? "
                         "WHERE sha256 = (SELECT sha256 FROM secret WHERE name = 'a')",
                         (self.fake.iso(),))
        result = self.add_one(name="b", value=VALUE + b"\n")
        self.assertEqual(result.outcome, "deduped")
        self.assertIn("吊销", result.warning)

    def test_untrusted_verdict_refused_without_force(self):
        with self.assertRaises(errors.RefusedError):
            self.add_one(name="x", verdict=UNTRUSTED)
        self.assertEqual(self.store.count(), 0)

    def test_untrusted_verdict_accepted_with_force(self):
        result = self.add_one(name="x", verdict=UNTRUSTED, force=True)
        self.assertEqual(result.outcome, "created")
        self.assertEqual(self.store.get("x").platform, "unknown")

    def test_empty_value_rejected(self):
        result = self.add_one(name="x", value=b"   \n")
        self.assertEqual(result.outcome, "rejected")
        self.assertEqual(self.store.count(), 0)

    def test_duplicate_name_different_value_raises(self):
        self.add_one(name="taken")
        with self.assertRaises(errors.AlreadyExistsError):
            self.add_one(name="taken", value=b"sk-" + b"B" * 40)

    def test_fingerprint_history_row_created(self):
        result = self.add_one(name="a")
        history = self.store.fingerprints_of(result.secret_id)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["status"], "active")
        self.assertEqual(history[0]["sha256"], masking.fingerprint(VALUE))

    def test_auto_suggested_name_from_platform(self):
        result = self.add_one()
        self.assertEqual(result.name, "deepseek")
        second = self.add_one(value=b"sk-" + b"B" * 44)
        self.assertEqual(second.name, "deepseek-2")

    def test_auto_suggested_name_from_key_name(self):
        result = self.add_one(key_name="DEEPSEEK_API_KEY")
        self.assertEqual(result.name, "deepseek-api-key")

    def test_suggested_name_skips_alias_collisions(self):
        self.add_one(name="deepseek")
        self.add_one(name="other", value=b"sk-" + b"B" * 44)
        with self.store.session() as conn:
            repo.insert_alias(conn, 2, "deepseek-2")
        result = self.add_one(value=b"sk-" + b"C" * 44)
        self.assertEqual(result.name, "deepseek-3")


class TestMetadataGuard(VaultTestCase):
    def test_note_containing_a_key_is_refused(self):
        with self.assertRaises(errors.RefusedError):
            self.add_one(name="a", note="备用 sk-" + "A" * 40)

    def test_note_containing_hex32_is_refused(self):
        with self.assertRaises(errors.RefusedError):
            self.add_one(name="a", note="高德 " + "0123456789abcdef" * 2)

    def test_plain_note_accepted(self):
        result = self.add_one(name="a", note="给 CI 用的那把，2027 年过期")
        self.assertEqual(result.outcome, "created")
        self.assertEqual(self.store.get("a").note, "给 CI 用的那把，2027 年过期")

    def test_source_url_with_credential_refused(self):
        with self.assertRaises(errors.RefusedError):
            self.add_one(name="a", source_url="https://x:" + "g" * 40 + "@host/")

    def test_plain_source_url_accepted(self):
        self.add_one(name="a", source_url="https://platform.deepseek.com/api_keys")
        self.assertIn("deepseek.com", self.store.get("a").source_url)


class TestReveal(VaultTestCase):
    def test_reveal_yields_plaintext_then_zeroes(self):
        self.add_one(name="a")
        captured = bytearray()
        with self.store.reveal("a") as buffer:
            self.assertEqual(bytes(buffer), VALUE)
            captured = buffer
        self.assertEqual(len(captured), 0, "退出窗口后缓冲区必须被清空")

    def test_reveal_writes_audit_after_the_window(self):
        self.add_one(name="a")
        with self.store.reveal("a") as buffer:
            self.assertEqual(len(self.store.list_audit(event="reveal", limit=10)), 0)
        rows = self.store.list_audit(event="reveal", limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name_snapshot"], "a")

    def test_reveal_audit_never_contains_the_value(self):
        self.add_one(name="a")
        with self.store.reveal("a"):
            pass
        row = self.store.list_audit(event="reveal", limit=1)[0]
        rendered = str(dict(row)) + row["detail_json"]
        self.assertNotIn(VALUE.decode(), rendered)
        self.assertNotIn("A" * 20, rendered)

    def test_reveal_updates_last_revealed_at(self):
        self.add_one(name="a")
        self.fake.advance(hours=3)
        with self.store.reveal("a"):
            pass
        self.assertEqual(self.store.get("a").last_revealed_at, self.fake.iso())

    def test_reveal_missing_name_raises(self):
        with self.assertRaises(errors.NotFoundError):
            with self.store.reveal("nope"):
                pass

    def test_reveal_zeroes_even_when_body_raises(self):
        self.add_one(name="a")
        captured = None
        with self.assertRaises(RuntimeError):
            with self.store.reveal("a") as buffer:
                captured = buffer
                raise RuntimeError("boom")
        self.assertEqual(len(captured), 0)


class TestQuery(VaultTestCase):
    def setUp(self):
        super().setUp()
        self.add_one(name="deepseek-main", tags=("prod",))
        self.add_one(name="github-ci", value=b"ghp_" + b"B" * 36,
                     verdict=Verdict(platform="github", confidence=CONFIDENCE_EXACT,
                                     source="shape", evidence="shape ghp_", mask_style=(4, 4)),
                     tags=("ci",), note="Actions 部署用")
        self.add_one(name="amap-rest", value=b"0123456789abcdef" * 2,
                     verdict=Verdict(platform="amap", confidence=CONFIDENCE_EXACT,
                                     source="keyname", evidence="keyname AMAP_REST_KEY",
                                     mask_style=(0, 4)),
                     key_name="AMAP_REST_KEY")

    def test_list_all(self):
        self.assertEqual(len(self.store.list_secrets()), 3)

    def test_filter_by_platform(self):
        rows = self.store.list_secrets(platform="github")
        self.assertEqual([r.name for r in rows], ["github-ci"])

    def test_filter_by_tag(self):
        rows = self.store.list_secrets(tag="prod")
        self.assertEqual([r.name for r in rows], ["deepseek-main"])

    def test_tag_filter_is_exact_not_substring(self):
        """LIKE '%prod%' 会同时匹配到 production —— 这正是用多对多的理由。"""
        self.assertEqual(self.store.list_secrets(tag="pro"), [])
        self.assertEqual(len(self.store.list_secrets(tag="prod")), 1)

    def test_query_matches_name(self):
        self.assertEqual([r.name for r in self.store.list_secrets(query="github")], ["github-ci"])

    def test_query_matches_note(self):
        self.assertEqual([r.name for r in self.store.list_secrets(query="Actions")], ["github-ci"])

    def test_query_matches_key_name(self):
        self.assertEqual([r.name for r in self.store.list_secrets(query="AMAP_REST")], ["amap-rest"])

    def test_query_escapes_wildcards(self):
        """用户搜 '100%' 不该匹配到一切。

        '_' 命中 amap-rest 是**正确**的 —— 它的 key_name 是 AMAP_REST_KEY，含字面
        下划线。若转义失效，'_' 会当通配符匹配到全部 3 条，这才是 bug。
        """
        self.assertEqual(self.store.list_secrets(query="%"), [])
        self.assertEqual([r.name for r in self.store.list_secrets(query="_")], ["amap-rest"])

    def test_literal_underscore_search_works(self):
        self.assertEqual(
            [r.name for r in self.store.list_secrets(query="AMAP_REST_KEY")], ["amap-rest"]
        )

    def test_query_is_case_insensitive(self):
        self.assertEqual([r.name for r in self.store.list_secrets(query="GITHUB")], ["github-ci"])

    def test_limit(self):
        self.assertEqual(len(self.store.list_secrets(limit=2)), 2)

    def test_preview_stored_not_derived(self):
        """列表渲染不该需要解密 —— 断言头尾两段有值且不含明文。"""
        for row in self.store.list_secrets():
            self.assertTrue(row.preview())
            self.assertNotIn("A" * 20, row.preview())
            self.assertNotIn("A" * 20, row.mask_head + row.mask_tail)

    def test_mask_char_is_a_pure_display_preference(self):
        """存的是头尾两段而不是合成串，所以换掩码字符立刻生效、无需重算全库。

        回归：曾经存合成好的 masked 串，于是 `config --set mask_char=•` 看起来
        什么都不做 —— 一个静默无效的设置，正是本项目要消灭的那类 bug。
        """
        row = self.store.list_secrets(query="deepseek")[0]
        # TRUSTED 的 mask_style 是 (0, 4)，所以头部是空的
        self.assertEqual(row.preview("*"), "****c31f")
        self.assertEqual(row.preview("•"), "••••c31f")
        self.assertEqual(row.preview("#"), "####c31f")

    def test_mask_char_with_a_prefix_preserving_style(self):
        self.add_one(
            name="prefixed", value=VALUE,
            verdict=Verdict(platform="deepseek", confidence=CONFIDENCE_EXACT,
                            source="shape", evidence="shape sk-", mask_style=(3, 4)),
        )
        row = self.store.get("prefixed")
        self.assertEqual(row.mask_head, "sk-")
        self.assertEqual(row.mask_tail, "c31f")
        self.assertEqual(row.preview("*"), "sk-****c31f")
        self.assertEqual(row.preview("•"), "sk-••••c31f")

    def test_get_by_sha256(self):
        fp = masking.fingerprint(VALUE)
        self.assertEqual(self.store.by_sha256(fp).name, "deepseek-main")
        self.assertIsNone(self.store.by_sha256("0" * 64))


class TestRenameDeleteTags(VaultTestCase):
    def test_rename_keeps_old_name_as_alias(self):
        self.add_one(name="old")
        self.store.rename("old", "new")
        self.assertEqual(self.store.get("new").name, "new")
        self.assertEqual(self.store.get("old").id, self.store.get("new").id)
        self.assertIn("old", self.store.get("new").aliases)

    def test_rename_to_existing_name_raises(self):
        self.add_one(name="a")
        self.add_one(name="b", value=b"sk-" + b"B" * 44)
        with self.assertRaises(errors.AlreadyExistsError):
            self.store.rename("a", "b")

    def test_rename_writes_audit(self):
        self.add_one(name="old")
        self.store.rename("old", "new")
        self.assertEqual(len(self.store.list_audit(event="rename", limit=10)), 1)

    def test_delete_removes_record_but_keeps_audit(self):
        self.add_one(name="doomed")
        self.store.delete("doomed")
        with self.assertRaises(errors.NotFoundError):
            self.store.get("doomed")
        rows = self.store.list_audit(event="delete", limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name_snapshot"], "doomed")

    def test_delete_cascades_to_fingerprint_and_tags(self):
        result = self.add_one(name="doomed", tags=("t1",))
        self.store.delete("doomed")
        with self.store.read() as conn:
            fps = conn.execute("SELECT COUNT(*) AS n FROM fingerprint").fetchone()["n"]
            links = conn.execute("SELECT COUNT(*) AS n FROM secret_tag").fetchone()["n"]
        self.assertEqual(fps, 0)
        self.assertEqual(links, 0)
        self.assertEqual(result.outcome, "created")

    def test_set_platform_marks_manual(self):
        ambiguous = Verdict(
            platform="openai-compatible", confidence="ambiguous", source="shape",
            evidence="shape openai_compatible: 没有主机名或键名就无法区分", mask_style=(0, 4),
        )
        self.add_one(name="a", verdict=ambiguous)
        self.assertEqual(self.store.get("a").confidence, "ambiguous")
        self.store.set_platform("a", "deepseek")
        row = self.store.get("a")
        self.assertEqual(row.platform, "deepseek")
        self.assertEqual(row.confidence, "manual")
        self.assertEqual(row.detect_source, "correction")

    def test_tags_are_many_to_many(self):
        self.add_one(name="a", tags=("x", "y"))
        self.add_one(name="b", value=b"sk-" + b"B" * 44, tags=("y", "z"))
        with self.store.read() as conn:
            self.assertEqual(dict(repo.list_tags(conn)), {"x": 1, "y": 2, "z": 1})

    def test_remove_tag(self):
        result = self.add_one(name="a", tags=("x", "y"))
        with self.store.session() as conn:
            self.store.remove_tag(conn, result.secret_id, "x")
        self.assertEqual(self.store.get("a").tags, ("y",))


class TestBinding(VaultTestCase):
    def test_wrong_account_blob_raises_binding_error(self):
        """模拟换机器/换账户：金丝雀用另一个 entropy 加密，解不开。"""
        other = make_vault(self.root / "other", protector=_WrongEntropyProtector())
        with self.assertRaises(errors.BindingError) as ctx:
            other.verify_binding()
        self.assertIn("另一个 Windows 账户", str(ctx.exception))

    def test_missing_canary_raises_not_initialized(self):
        store = VaultStore(self.root / "nope.db")
        with self.assertRaises(errors.VaultNotInitializedError):
            store.verify_binding()


class _WrongEntropyProtector(PlaintextProtector):
    """protect 用一个 entropy，unprotect 用另一个 —— 模拟 DPAPI 绑定失效。"""

    def unprotect(self, blob: bytes) -> bytes:
        raise errors.DpapiError("解密失败", code=13)

    def unprotect_into(self, blob: bytes, out: bytearray) -> None:
        raise errors.DpapiError("解密失败", code=13)


class TestVersionGate(VaultTestCase):
    def test_newer_schema_refused(self):
        with self.store.session() as conn:
            conn.execute("UPDATE meta SET value = '999' WHERE key = 'schema_version'")
        with self.store.read() as conn:
            with self.assertRaises(errors.KvError) as ctx:
                db.check_version(conn)
        self.assertIn("999", str(ctx.exception))

    def test_uninitialized_db_detected(self):
        self.assertFalse(db.is_initialized(self.root / "absent.db"))
        self.assertTrue(db.is_initialized(self.store.db_path))


class TestInitTransactionBoundary(unittest.TestCase):
    """initialize 的事务边界：BEGIN 前失败要冒根因，BEGIN 后失败要回滚。

    回归背景：曾有版本在 except 里无条件 ROLLBACK，apply_schema（BEGIN 之前）
    失败时 ROLLBACK 自身抛 no transaction is active，把真实错误盖死。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _init(self) -> None:
        protector = PlaintextProtector()
        db.initialize(
            self.root / "vault.db",
            canary_blob=protector.protect(dpapi.CANARY_TEXT.encode("utf-8")),
            canary_text=dpapi.CANARY_TEXT,
        )

    def test_failure_before_begin_propagates_root_cause(self):
        original = db.apply_schema

        def broken(conn):
            raise RuntimeError("schema 阶段的真实错误")

        db.apply_schema = broken
        try:
            with self.assertRaises(RuntimeError) as ctx:
                self._init()
        finally:
            db.apply_schema = original
        self.assertEqual(str(ctx.exception), "schema 阶段的真实错误")

    def test_failure_in_transaction_rolls_back_meta(self):
        original = repo.bootstrap_meta

        def broken(conn, **kwargs):
            raise RuntimeError("meta 阶段的真实错误")

        repo.bootstrap_meta = broken
        try:
            with self.assertRaises(RuntimeError) as ctx:
                self._init()
        finally:
            repo.bootstrap_meta = original
        self.assertEqual(str(ctx.exception), "meta 阶段的真实错误")
        conn = db.connect(self.root / "vault.db")
        try:
            self.assertIsNone(repo.get_meta(conn, "schema_version"))
        finally:
            conn.close()


@unittest.skipUnless(WINDOWS, "静息加密要用真 DPAPI")
class TestAtRestEncryption(unittest.TestCase):
    """M0 验收的硬指标：用值去 grep 原始 DB 文件必须返回空。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        canary = dpapi.protect(dpapi.CANARY_TEXT.encode("utf-8"))
        self.db_path = self.root / "vault.db"
        db.initialize(self.db_path, canary_blob=canary, canary_text=dpapi.CANARY_TEXT)
        self.store = VaultStore(self.db_path)

    def raw_bytes(self) -> bytes:
        blobs = b""
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.db_path) + suffix)
            if candidate.exists():
                blobs += candidate.read_bytes()
        return blobs

    def test_value_absent_from_raw_db(self):
        secret = b"sk-" + b"S" * 40 + b"tail"
        saveops.commit(
            Candidate(value=secret), TRUSTED, store=self.store,
            policy=saveops.SavePolicy(name="leak-check", actor="test"),
        )
        raw = self.raw_bytes()
        self.assertGreater(len(raw), 0)
        self.assertNotIn(secret, raw)
        self.assertNotIn(b"S" * 40, raw)
        self.assertNotIn(secret.decode(), raw.decode("latin-1"))

    def test_metadata_is_searchable_plaintext(self):
        """元数据明文是设计的一部分：不加密才能不解密就搜索。"""
        saveops.commit(
            Candidate(value=b"sk-" + b"S" * 40), TRUSTED, store=self.store,
            policy=saveops.SavePolicy(name="findable-name", note="一条中文备注", actor="test"),
        )
        raw = self.raw_bytes().decode("latin-1")
        self.assertIn("findable-name", raw)
        self.assertIn("deepseek", raw)

    def test_preview_from_stored_parts(self):
        saveops.commit(
            Candidate(value=b"sk-" + b"S" * 40 + b"c31f"), TRUSTED, store=self.store,
            policy=saveops.SavePolicy(name="m", actor="test"),
        )
        row = self.store.get("m")
        self.assertTrue(row.preview().endswith("c31f"))
        self.assertNotIn("S" * 20, row.preview())
        self.assertNotIn("S" * 20, row.mask_head + row.mask_tail)

    def test_binding_canary_real_dpapi(self):
        self.store.verify_binding()
        self.assertTrue(self.store.doctor()["binding_ok"])


if __name__ == "__main__":
    unittest.main()
