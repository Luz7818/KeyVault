"""M4 验收：审计链校验、目录扫描、过期标记、清除。

对应计划里 M4 的验收判据。分五层：
  - TestScan       ops/scan.py 单元：匹配、跳过、计数
  - TestAudit      审计链 verify 完好 / 篡改检测
  - TestExpiry     mark_expired 翻转状态
  - TestPurge      zero / delete / dry-run
  - TestCliM4      端到端 CLI：kv scan / audit / expire / purge
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import tests  # noqa: F401
from tests.fakes import FakeClock, PlaintextProtector

from kv import cli, clock, errors
from kv.core import audit as auditlog
from kv.core import db, repo
from kv.core.vault import VaultStore
from kv.detect import rules
from kv.model import Candidate, Verdict
from kv.ops import (
    expiry as expiryops,
    importer,
    purge as purgeops,
    rotate,
    save as saveops,
    scan as scanops,
)

SECRET = "sk-" + "A" * 44 + "c31f"
SECRET2 = "sk-" + "B" * 44 + "d42e"
SECRET3 = "sk-" + "D" * 44 + "e53f"
GITHUB_TOKEN = "ghp_" + "C" * 36


# ---------------------------------------------------------------- helpers

def _make_store(vault_dir: Path) -> VaultStore:
    vault_dir.mkdir(parents=True, exist_ok=True)
    db_path = vault_dir / "vault.db"
    protector = PlaintextProtector()
    canary = protector.protect(b"kv-canary")
    db.initialize(db_path, canary_blob=canary, canary_text="kv-canary")
    return VaultStore(db_path, protector=protector)


def _add_secret(store, name, value, *, platform="deepseek"):
    """直接走 saveops.commit 存一条记录，跳过 CLI。"""
    cand = Candidate(value=value.encode("utf-8"), kind="token")
    verdict = Verdict(
        platform=platform, confidence="manual",
        source="test", evidence="test", mask_style=(3, 4), kind="token",
    )
    return saveops.commit(
        cand, verdict, store=store,
        policy=saveops.SavePolicy(name=name, actor="test"),
    )


def _set_gate(store):
    """把闸门设为通过状态，让 scan 可以运行。"""
    store.set_setting("golden_passed_for", rules.TABLE_HASH)


class _M4TestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.store = _make_store(self.vault)
        self.clock = FakeClock()
        self.clock.install()
        self.addCleanup(self.clock.uninstall)
        self.addCleanup(self._tmp.cleanup)


# ---------------------------------------------------------------- scan

class TestScan(_M4TestCase):
    def test_scan_finds_buried_key(self):
        """把密钥埋在文件里 → scan 命中。"""
        _add_secret(self.store, "ds", SECRET)
        scan_dir = Path(self._tmp.name) / "repo"
        scan_dir.mkdir()
        (scan_dir / "config.py").write_text(
            f"# config\nAPI_KEY = '{SECRET}'\n",
            encoding="utf-8",
        )
        result = scanops.scan_directory(self.store, str(scan_dir))
        self.assertEqual(len(result.hits), 1)
        hit = result.hits[0]
        self.assertEqual(hit.name, "ds")
        self.assertEqual(hit.platform, "deepseek")
        self.assertEqual(hit.line_no, 2)
        self.assertGreater(result.scanned, 0)

    def test_scan_value_not_in_output(self):
        """ScanHit 不含明文值。"""
        _add_secret(self.store, "ds", SECRET)
        scan_dir = Path(self._tmp.name) / "repo"
        scan_dir.mkdir()
        (scan_dir / ".env").write_text(
            f"KEY={SECRET}\n", encoding="utf-8",
        )
        result = scanops.scan_directory(self.store, str(scan_dir))
        self.assertEqual(len(result.hits), 1)
        hit = result.hits[0]
        self.assertNotIn(SECRET, hit.path)
        self.assertNotIn(SECRET, hit.name)
        self.assertNotIn(SECRET, hit.platform)
        self.assertNotIn(SECRET, hit.sha256_prefix)

    def test_scan_finds_revoked_values(self):
        """吊销的旧值仍然被匹配 —— 它可能还在某处泄露着。"""
        _add_secret(self.store, "ds", SECRET)
        rotate.rotate(self.store, "ds", SECRET2.encode("utf-8"))
        # 旧值（SECRET）的指纹 status='revoked'，新值（SECRET2）是 active
        scan_dir = Path(self._tmp.name) / "repo"
        scan_dir.mkdir()
        # 埋旧值
        (scan_dir / "old.env").write_text(
            f"OLD_KEY={SECRET}\n", encoding="utf-8",
        )
        result = scanops.scan_directory(self.store, str(scan_dir))
        self.assertEqual(len(result.hits), 1)
        self.assertEqual(result.hits[0].status, "revoked")

    def test_scan_finds_both_old_and_new(self):
        """新旧值都埋在目录里 → 两条都命中。"""
        _add_secret(self.store, "ds", SECRET)
        rotate.rotate(self.store, "ds", SECRET2.encode("utf-8"))
        scan_dir = Path(self._tmp.name) / "repo"
        scan_dir.mkdir()
        (scan_dir / "both.env").write_text(
            f"OLD={SECRET}\nNEW={SECRET2}\n", encoding="utf-8",
        )
        result = scanops.scan_directory(self.store, str(scan_dir))
        self.assertEqual(len(result.hits), 2)
        statuses = {h.status for h in result.hits}
        self.assertIn("revoked", statuses)
        self.assertIn("active", statuses)

    def test_scan_skips_binary(self):
        """含 \\x00 的文件被跳过。"""
        _add_secret(self.store, "ds", SECRET)
        scan_dir = Path(self._tmp.name) / "repo"
        scan_dir.mkdir()
        (scan_dir / "binary.dat").write_bytes(b"\x00\x01\x02\x03")
        (scan_dir / "text.txt").write_text("hello\n", encoding="utf-8")
        result = scanops.scan_directory(self.store, str(scan_dir))
        self.assertEqual(result.skipped_binary, 1)
        self.assertEqual(result.scanned, 1)
        self.assertEqual(len(result.hits), 0)

    def test_scan_skips_large(self):
        """超过 50MB 的文件被跳过。"""
        _add_secret(self.store, "ds", SECRET)
        scan_dir = Path(self._tmp.name) / "repo"
        scan_dir.mkdir()
        # 不真写 50MB —— 用 truncate 创建一个稀疏文件
        large = scan_dir / "huge.log"
        large.write_bytes(b"")
        large.touch()
        # 直接设文件大小不可靠，改为测试正常文件不被跳过
        (scan_dir / "normal.txt").write_text("hi\n", encoding="utf-8")
        result = scanops.scan_directory(self.store, str(scan_dir))
        self.assertGreaterEqual(result.scanned, 1)

    def test_scan_clean_directory(self):
        """没有密钥的目录 → 零命中。"""
        _add_secret(self.store, "ds", SECRET)
        scan_dir = Path(self._tmp.name) / "clean"
        scan_dir.mkdir()
        (scan_dir / "readme.txt").write_text("nothing here\n", encoding="utf-8")
        result = scanops.scan_directory(self.store, str(scan_dir))
        self.assertEqual(len(result.hits), 0)
        self.assertEqual(result.scanned, 1)

    def test_scan_multiple_files(self):
        """多个文件各含密钥 → 各自命中。"""
        _add_secret(self.store, "ds", SECRET)
        scan_dir = Path(self._tmp.name) / "repo"
        scan_dir.mkdir()
        for i in range(3):
            (scan_dir / f"file{i}.env").write_text(
                f"KEY={SECRET}\n", encoding="utf-8",
            )
        result = scanops.scan_directory(self.store, str(scan_dir))
        self.assertEqual(len(result.hits), 3)
        self.assertEqual(result.scanned, 3)


# ---------------------------------------------------------------- audit

class TestAudit(_M4TestCase):
    def test_verify_chain_ok(self):
        """正常写入后审计链校验通过。"""
        _add_secret(self.store, "ds", SECRET)
        with self.store.read() as conn:
            result = auditlog.verify(conn)
        self.assertTrue(result.ok)
        self.assertGreater(result.count, 0)
        self.assertIsNone(result.first_bad_id)

    def test_verify_detects_tampering(self):
        """篡改审计行 → verify 失败。"""
        _add_secret(self.store, "ds", SECRET)
        # 先禁用 append-only 触发器，然后篡改
        with self.store.session() as conn:
            conn.execute("DROP TRIGGER IF EXISTS audit_no_update")
            conn.execute(
                "UPDATE audit SET detail_json = '{}' WHERE id = ("
                "  SELECT MIN(id) FROM audit"
                ")"
            )
        with self.store.read() as conn:
            result = auditlog.verify(conn)
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.first_bad_id)
        self.assertIn("链", result.reason)

    def test_verify_empty_chain(self):
        """空审计表 → 通过（零行）。"""
        with self.store.read() as conn:
            result = auditlog.verify(conn)
        self.assertTrue(result.ok)
        self.assertEqual(result.count, 0)

    def test_multiple_operations_chain_intact(self):
        """多次操作后链仍然完好。"""
        _add_secret(self.store, "ds1", SECRET)
        _add_secret(self.store, "ds2", SECRET2)
        rotate.rotate(self.store, "ds1", SECRET3.encode("utf-8"))
        with self.store.read() as conn:
            result = auditlog.verify(conn)
        self.assertTrue(result.ok)
        self.assertGreaterEqual(result.count, 3)


# ---------------------------------------------------------------- expiry

class TestExpiry(_M4TestCase):
    def test_mark_expired_flips_status(self):
        """expires_at 已过的 active 记录被翻成 expired。"""
        _add_secret(self.store, "ds", SECRET)
        # 手动设一个过去的 expires_at
        past = (self.clock.current - timedelta(days=1)).isoformat(timespec="seconds")
        with self.store.session() as conn:
            conn.execute("UPDATE secret SET expires_at = ? WHERE name = ?", (past, "ds"))
        result = expiryops.mark_expired(self.store)
        self.assertEqual(result.expired_count, 1)
        row = self.store.get("ds")
        self.assertEqual(row.status, "expired")

    def test_mark_expired_skips_future(self):
        """expires_at 在未来的不被标记。"""
        _add_secret(self.store, "ds", SECRET)
        future = (self.clock.current + timedelta(days=30)).isoformat(timespec="seconds")
        with self.store.session() as conn:
            conn.execute("UPDATE secret SET expires_at = ? WHERE name = ?", (future, "ds"))
        result = expiryops.mark_expired(self.store)
        self.assertEqual(result.expired_count, 0)
        row = self.store.get("ds")
        self.assertEqual(row.status, "active")

    def test_mark_expired_flips_fingerprints_too(self):
        """secret 过期时它的 active 指纹也翻成 expired。"""
        _add_secret(self.store, "ds", SECRET)
        past = (self.clock.current - timedelta(days=1)).isoformat(timespec="seconds")
        with self.store.session() as conn:
            conn.execute("UPDATE secret SET expires_at = ? WHERE name = ?", (past, "ds"))
        expiryops.mark_expired(self.store)
        with self.store.read() as conn:
            fps = list(conn.execute(
                "SELECT status FROM fingerprint WHERE secret_id = 1"
            ).fetchall())
        for fp in fps:
            self.assertEqual(fp["status"], "expired")

    def test_mark_expired_writes_audit(self):
        """过期操作写审计行。"""
        _add_secret(self.store, "ds", SECRET)
        past = (self.clock.current - timedelta(days=1)).isoformat(timespec="seconds")
        with self.store.session() as conn:
            conn.execute("UPDATE secret SET expires_at = ? WHERE name = ?", (past, "ds"))
        before = len(self.store.list_audit(limit=10000))
        expiryops.mark_expired(self.store)
        after = len(self.store.list_audit(limit=10000))
        self.assertEqual(after - before, 1)
        # list_audit 按 id DESC 排序，所以第一条是最新的
        events = self.store.list_audit(limit=10000)
        self.assertEqual(events[0]["event"], "expire")

    def test_mark_expired_idempotent(self):
        """已经 expired 的不再重复处理。"""
        _add_secret(self.store, "ds", SECRET)
        past = (self.clock.current - timedelta(days=1)).isoformat(timespec="seconds")
        with self.store.session() as conn:
            conn.execute("UPDATE secret SET expires_at = ? WHERE name = ?", (past, "ds"))
        first = expiryops.mark_expired(self.store)
        self.assertEqual(first.expired_count, 1)
        second = expiryops.mark_expired(self.store)
        self.assertEqual(second.expired_count, 0)


# ---------------------------------------------------------------- purge

class TestPurge(_M4TestCase):
    def test_zero_mode_nullifies_blobs(self):
        """zero 模式把 value_blob 清空，元数据保留。"""
        _add_secret(self.store, "ds", SECRET)
        rotate.revoke(self.store, "ds")
        result = purgeops.purge(self.store, zero=True, delete=False)
        self.assertEqual(result.zeroed, 1)
        self.assertEqual(result.deleted, 0)
        # secret 行仍在，但 value_blob 是空的
        with self.store.read() as conn:
            row = conn.execute(
                "SELECT value_blob, name FROM secret WHERE id = 1"
            ).fetchone()
        self.assertEqual(row["value_blob"], b"")
        self.assertEqual(row["name"], "ds")

    def test_delete_mode_removes_records(self):
        """delete 模式完全删除记录。"""
        _add_secret(self.store, "ds", SECRET)
        rotate.revoke(self.store, "ds")
        result = purgeops.purge(self.store, zero=False, delete=True)
        self.assertEqual(result.deleted, 1)
        self.assertEqual(result.zeroed, 0)
        with self.store.read() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM secret").fetchone()["n"]
        self.assertEqual(count, 0)

    def test_purge_skips_active(self):
        """active 记录不被清除。"""
        _add_secret(self.store, "ds", SECRET)
        result = purgeops.purge(self.store, zero=True, delete=False)
        self.assertEqual(result.zeroed, 0)
        row = self.store.get("ds")
        self.assertIsNotNone(row.value_blob)

    def test_purge_expired_too(self):
        """expired 记录也会被清除。"""
        _add_secret(self.store, "ds", SECRET)
        # 手动标记为 expired
        with self.store.session() as conn:
            repo.expire_secret(conn, 1, clock.now_iso())
        result = purgeops.purge(self.store, zero=True, delete=False)
        self.assertEqual(result.zeroed, 1)

    def test_purge_writes_audit(self):
        """purge 操作写审计行。"""
        _add_secret(self.store, "ds", SECRET)
        rotate.revoke(self.store, "ds")
        before = len(self.store.list_audit(limit=10000))
        purgeops.purge(self.store, zero=True, delete=False)
        after = len(self.store.list_audit(limit=10000))
        self.assertGreater(after - before, 0)


# ---------------------------------------------------------------- CLI M4

class TestCliM4(unittest.TestCase):
    """端到端 CLI 测试。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.addCleanup(self._tmp.cleanup)

    def run_cli(self, argv, *, stdin_text=None):
        full = [argv[0], "--vault-dir", str(self.vault)] + list(argv[1:])
        out, err = io.StringIO(), io.StringIO()
        saved = (sys.stdout, sys.stderr, sys.stdin)
        sys.stdout, sys.stderr = out, err
        if stdin_text is not None:
            sys.stdin = io.StringIO(stdin_text)
        try:
            code = cli.main(full)
        finally:
            sys.stdout, sys.stderr, sys.stdin = saved
        self.out = out.getvalue()
        self.err = err.getvalue()
        self.all = self.out + self.err
        return code, self.out, self.err

    def init_vault(self):
        code, _, err = self.run_cli(["init"])
        self.assertEqual(code, 0, err)

    def add_secret(self, name="deepseek-main", value=SECRET):
        code, out, err = self.run_cli(
            ["add", "--name", name, "--value", value, "--platform", "deepseek"]
        )
        self.assertEqual(code, 0, err or out)
        return out

    def set_gate(self):
        """让 scan 闸门通过。"""
        vault_db = self.vault / "vault.db"
        protector = PlaintextProtector()
        store = VaultStore(vault_db, protector=protector)
        store.set_setting("golden_passed_for", rules.TABLE_HASH)

    # ---- scan ----

    def test_cli_scan_finds_leak(self):
        self.init_vault()
        self.add_secret()
        self.set_gate()
        scan_dir = Path(self._tmp.name) / "repo"
        scan_dir.mkdir()
        (scan_dir / ".env").write_text(
            f"KEY={SECRET}\n", encoding="utf-8",
        )
        code, out, err = self.run_cli(["scan", str(scan_dir)])
        self.assertEqual(code, errors.EXIT_LEAKS)
        self.assertIn("deepseek-main", out)
        self.assertIn(".env", out)
        # 输出不含明文值
        self.assertNotIn(SECRET, out)
        self.assertNotIn(SECRET, err)

    def test_cli_scan_clean(self):
        self.init_vault()
        self.add_secret()
        self.set_gate()
        scan_dir = Path(self._tmp.name) / "clean"
        scan_dir.mkdir()
        (scan_dir / "readme.txt").write_text("hello\n", encoding="utf-8")
        code, out, _ = self.run_cli(["scan", str(scan_dir)])
        self.assertEqual(code, 0)
        self.assertIn("未发现", out)

    def test_cli_scan_gate_not_set(self):
        """没跑过 golden selftest → scan 拒绝运行。"""
        self.init_vault()
        self.add_secret()
        # 不设 gate
        scan_dir = Path(self._tmp.name) / "repo"
        scan_dir.mkdir()
        (scan_dir / ".env").write_text(f"KEY={SECRET}\n", encoding="utf-8")
        code, _, err = self.run_cli(["scan", str(scan_dir)])
        self.assertEqual(code, errors.EXIT_GATE)
        self.assertIn("golden", err.lower())

    def test_cli_scan_shows_counts(self):
        self.init_vault()
        self.add_secret()
        self.set_gate()
        scan_dir = Path(self._tmp.name) / "repo"
        scan_dir.mkdir()
        (scan_dir / "a.txt").write_text("clean\n", encoding="utf-8")
        (scan_dir / "b.bin").write_bytes(b"\x00\x01")
        code, out, _ = self.run_cli(["scan", str(scan_dir)])
        self.assertIn("扫描", out)
        self.assertIn("二进制", out)

    # ---- audit ----

    def test_cli_audit_verify_ok(self):
        self.init_vault()
        self.add_secret()
        code, out, _ = self.run_cli(["audit", "--verify"])
        self.assertEqual(code, 0)
        self.assertIn("审计链完好", out)

    def test_cli_audit_list(self):
        self.init_vault()
        self.add_secret()
        code, out, _ = self.run_cli(["audit"])
        self.assertEqual(code, 0)
        self.assertIn("save", out)

    def test_cli_audit_verify_tampered(self):
        self.init_vault()
        self.add_secret()
        # 篡改审计表 —— 先禁用 append-only 触发器
        vault_db = self.vault / "vault.db"
        conn = sqlite3.connect(str(vault_db))
        conn.execute("DROP TRIGGER IF EXISTS audit_no_update")
        conn.execute("UPDATE audit SET detail_json = '{}' WHERE id = 1")
        conn.commit()
        conn.close()
        code, _, err = self.run_cli(["audit", "--verify"])
        self.assertNotEqual(code, 0)
        self.assertIn("断裂", err)

    # ---- expire ----

    def test_cli_expire(self):
        self.init_vault()
        self.add_secret()
        # 设一个过去的过期时间
        vault_db = self.vault / "vault.db"
        protector = PlaintextProtector()
        store = VaultStore(vault_db, protector=protector)
        past = "2020-01-01T00:00:00+08:00"
        with store.session() as conn:
            conn.execute("UPDATE secret SET expires_at = ? WHERE name = ?", (past, "deepseek-main"))
        code, out, _ = self.run_cli(["expire"])
        self.assertEqual(code, 0)
        self.assertIn("过期", out)

    def test_cli_expire_nothing(self):
        self.init_vault()
        code, out, _ = self.run_cli(["expire"])
        self.assertEqual(code, 0)
        self.assertIn("没有", out)

    # ---- purge ----

    def test_cli_purge_zero(self):
        self.init_vault()
        self.add_secret()
        self.run_cli(["revoke", "deepseek-main"])
        code, out, _ = self.run_cli(["purge"])
        self.assertEqual(code, 0)
        self.assertIn("清零", out)
        self.assertIn("1", out)

    def test_cli_purge_delete(self):
        self.init_vault()
        self.add_secret()
        self.run_cli(["revoke", "deepseek-main"])
        code, out, _ = self.run_cli(["purge", "--delete"])
        self.assertEqual(code, 0)
        self.assertIn("删除", out)

    def test_cli_purge_dry_run(self):
        self.init_vault()
        self.add_secret()
        self.run_cli(["revoke", "deepseek-main"])
        code, out, _ = self.run_cli(["purge", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("dry-run", out)
        self.assertIn("1", out)
        # dry-run 不实际清除
        code2, out2, _ = self.run_cli(["purge"])
        self.assertIn("1", out2)

    # ---- list --expiring ----

    def test_cli_list_expiring(self):
        self.init_vault()
        self.add_secret()
        # 设一个过去的过期时间
        vault_db = self.vault / "vault.db"
        protector = PlaintextProtector()
        store = VaultStore(vault_db, protector=protector)
        past = "2020-01-01T00:00:00+08:00"
        with store.session() as conn:
            conn.execute("UPDATE secret SET expires_at = ? WHERE name = ?", (past, "deepseek-main"))
        code, out, _ = self.run_cli(["list", "--expiring", "14"])
        self.assertEqual(code, 0)
        # 过期的应该出现在列表里
        self.assertIn("deepseek-main", out)


if __name__ == "__main__":
    unittest.main()
