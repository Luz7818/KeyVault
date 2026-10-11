"""CLI export / restore 验收：init 的 R1 提示点名的命令必须真的能用。

口令经 console.hidden_input 读入（不回显、不进 argv），测试里 monkeypatch 它。
覆盖：导出全量（含 revoked）、口令不一致拒绝、弱口令拒绝、跨库恢复、
口令错误拒绝、确认取消、重复恢复去重。
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401
from kv import cli, console

SECRET = "sk-" + "A" * 44 + "c31f"
SECRET2 = "sk-" + "B" * 44 + "d42e"
PASSWORD = "correct horse battery"


class BackupCliTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.backup_path = Path(self._tmp.name) / "backup.kvb"
        self.addCleanup(self._tmp.cleanup)
        self._hidden_original = console.hidden_input
        console.hidden_input = lambda prompt="": PASSWORD
        self.addCleanup(setattr, console, "hidden_input", self._hidden_original)

    def run_cli(self, argv, *, stdin_text=None):
        full = ["--vault-dir", str(self.vault), *argv]
        out, err = io.StringIO(), io.StringIO()
        saved = (sys.stdout, sys.stderr, sys.stdin)
        sys.stdout, sys.stderr, sys.stdin = out, err, io.StringIO(stdin_text or "")
        try:
            code = cli.main(full)
        finally:
            sys.stdout, sys.stderr, sys.stdin = saved
        return code, out.getvalue(), err.getvalue()

    def init_vault(self):
        code, _, err = self.run_cli(["init"])
        self.assertEqual(code, 0, err)

    def add_secret(self, name, value, *extra):
        code, out, err = self.run_cli(
            ["add", "--name", name, "--value", value, "--platform", "deepseek", *extra]
        )
        self.assertEqual(code, 0, err or out)


class TestExport(BackupCliTestCase):
    def test_export_writes_encrypted_kvb(self):
        self.init_vault()
        self.add_secret("ds", SECRET)
        code, out, err = self.run_cli(["export", "--to", str(self.backup_path)])
        self.assertEqual(code, 0, err)
        blob = self.backup_path.read_bytes()
        self.assertTrue(blob.startswith(b"KVBK1\n"))
        self.assertIn("1 条", out)

    def test_export_includes_revoked(self):
        """revoked 记录一并带走 —— 备份是全量历史。"""
        self.init_vault()
        self.add_secret("ds", SECRET)
        self.run_cli(["revoke", "ds"])
        code, out, _ = self.run_cli(["export", "--to", str(self.backup_path)])
        self.assertEqual(code, 0)
        self.assertIn("1 条", out)

    def test_export_mismatched_password_refused(self):
        self.init_vault()
        answers = iter(["first-password", "second-password"])
        console.hidden_input = lambda prompt="": next(answers)
        code, _, err = self.run_cli(["export", "--to", str(self.backup_path)])
        self.assertNotEqual(code, 0)
        self.assertIn("不一致", err)

    def test_export_weak_password_refused(self):
        self.init_vault()
        console.hidden_input = lambda prompt="": "short"
        code, _, err = self.run_cli(["export", "--to", str(self.backup_path)])
        self.assertNotEqual(code, 0)
        self.assertIn("8 位", err)


class TestRestore(BackupCliTestCase):
    def _make_backup(self):
        self.init_vault()
        self.add_secret("ds", SECRET)
        self.add_secret("ds2", SECRET2)
        code, _, err = self.run_cli(["export", "--to", str(self.backup_path)])
        self.assertEqual(code, 0, err)

    def _init_vault_at(self, vault: Path):
        code, _, err = self.run_cli(["--vault-dir", str(vault), "init"])
        self.assertEqual(code, 0, err)

    def test_restore_into_fresh_vault(self):
        self._make_backup()
        fresh = Path(self._tmp.name) / "fresh"
        self._init_vault_at(fresh / "vault")
        code, out, err = self.run_cli(
            ["--vault-dir", str(fresh / "vault"), "restore", "--from", str(self.backup_path)],
            stdin_text="y\n",
        )
        self.assertEqual(code, 0, err)
        code, out, _ = self.run_cli(["--vault-dir", str(fresh / "vault"), "reveal", "ds", "-y", "--newline"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), SECRET)

    def test_restore_wrong_password_refused(self):
        self._make_backup()
        console.hidden_input = lambda prompt="": "wrong password!!"
        code, _, err = self.run_cli(
            ["restore", "--from", str(self.backup_path)], stdin_text="y\n",
        )
        self.assertNotEqual(code, 0)
        self.assertIn("口令错误", err)

    def test_restore_cancelled_by_confirm(self):
        self._make_backup()
        code, out, _ = self.run_cli(
            ["restore", "--from", str(self.backup_path)], stdin_text="n\n",
        )
        self.assertEqual(code, 0)
        self.assertIn("已取消", out)
        code, out, _ = self.run_cli(["list"])
        self.assertIn("共 2 条", out)  # 没动原库；取消只是不恢复

    def test_restore_twice_dedupes(self):
        self._make_backup()
        fresh = Path(self._tmp.name) / "fresh"
        self._init_vault_at(fresh / "vault")
        for _ in range(2):
            code, _, err = self.run_cli(
                ["--vault-dir", str(fresh / "vault"), "restore", "--from", str(self.backup_path)],
                stdin_text="y\n",
            )
            self.assertEqual(code, 0, err)
        code, out, _ = self.run_cli(["--vault-dir", str(fresh / "vault"), "list"])
        self.assertIn("共 2 条", out)

    def test_restore_missing_file(self):
        self.init_vault()
        code, _, err = self.run_cli(
            ["restore", "--from", str(Path(self._tmp.name) / "absent.kvb")],
        )
        self.assertNotEqual(code, 0)
        self.assertIn("不存在", err)


if __name__ == "__main__":
    unittest.main()
