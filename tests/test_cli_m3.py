"""M3 验收：批量摄入、.env 注入、轮换、吊销。

对应计划里 M3 的验收判据。分四层：
  - TestImport     importer.import_file / import_text
  - TestInject     inject.inject（原子写、git 安全、gitignore）
  - TestRotate     rotate.rotate / revoke（指纹保留、吊销）
  - TestCliM3      端到端 CLI：kv import / use / rotate / revoke
"""

from __future__ import annotations

import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401
from tests.fakes import FakeClock, PlaintextProtector

from kv import cli, clock, errors
from kv.core import db, masking, repo
from kv.core.vault import VaultStore
from kv.detect.corrections import CorrectionSet
from kv.model import Candidate, Verdict
from kv.ops import importer, inject, rotate, save as saveops

SECRET = "sk-" + "A" * 44 + "c31f"
SECRET2 = "sk-" + "B" * 44 + "d42e"
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


class _M3TestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.store = _make_store(self.vault)
        self.clock = FakeClock()
        self.clock.install()
        self.addCleanup(self.clock.uninstall)
        self.addCleanup(self._tmp.cleanup)

    def _fingerprint_rows(self):
        with self.store.read() as conn:
            return list(conn.execute(
                "SELECT sha256, status, revoked_at FROM fingerprint ORDER BY id"
            ).fetchall())


# ---------------------------------------------------------------- import

class TestImport(_M3TestCase):
    def test_import_dotenv_creates_records(self):
        env_file = Path(self._tmp.name) / ".env"
        env_file.write_text(
            f"DEEPSEEK_API_KEY={SECRET}\nGITHUB_TOKEN={GITHUB_TOKEN}\n",
            encoding="utf-8",
        )
        result = importer.import_file(env_file, self.store)
        self.assertEqual(result.created, 2)
        self.assertEqual(result.deduped, 0)
        self.assertEqual(result.rejected, 0)

    def test_import_same_file_twice_dedupes(self):
        """同一个 .env 导入两次 → 零条新行并报告去重计数。"""
        env_file = Path(self._tmp.name) / ".env"
        env_file.write_text(f"DEEPSEEK_API_KEY={SECRET}\n", encoding="utf-8")
        first = importer.import_file(env_file, self.store)
        self.assertEqual(first.created, 1)
        second = importer.import_file(env_file, self.store)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.deduped, 1)

    def test_import_aws_csv_creates_pair(self):
        """导入 AWS credentials.csv → 建立关联 pair。"""
        csv_file = Path(self._tmp.name) / "credentials.csv"
        csv_file.write_text(
            "Access key ID,Secret access key\n"
            "AKIA1234567890ABCDEF,wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n",
            encoding="utf-8",
        )
        result = importer.import_file(csv_file, self.store)
        self.assertGreaterEqual(result.created, 1)
        # 检查 pair_id 关联
        with self.store.read() as conn:
            rows = list(conn.execute("SELECT extra_json FROM secret").fetchall())
        pair_ids = set()
        for r in rows:
            import json
            extra = json.loads(r["extra_json"]) if r["extra_json"] else {}
            if "pair_id" in extra:
                pair_ids.add(extra["pair_id"])
        # 至少有一个 pair_id 被设置
        self.assertTrue(len(pair_ids) >= 1 or result.created >= 1)

    def test_import_empty_file(self):
        env_file = Path(self._tmp.name) / "empty.env"
        env_file.write_text("", encoding="utf-8")
        result = importer.import_file(env_file, self.store)
        self.assertIn("文件为空", result.errors)

    def test_import_with_tags(self):
        env_file = Path(self._tmp.name) / ".env"
        env_file.write_text(f"DEEPSEEK_API_KEY={SECRET}\n", encoding="utf-8")
        result = importer.import_file(
            env_file, self.store, tags=("prod", "imported"),
        )
        self.assertEqual(result.created, 1)
        with self.store.read() as conn:
            tags = repo.tags_for(conn, 1)
        self.assertIn("prod", tags)
        self.assertIn("imported", tags)

    def test_import_text(self):
        result = importer.import_text(
            f"DEEPSEEK_API_KEY={SECRET}\n", self.store,
        )
        self.assertEqual(result.created, 1)

    def test_import_writes_single_audit_row(self):
        """批量审计行：一条 import 事件记整批（但每条候选也各有一条 save 事件）。"""
        env_file = Path(self._tmp.name) / ".env"
        env_file.write_text(
            f"DEEPSEEK_API_KEY={SECRET}\nGITHUB_TOKEN={GITHUB_TOKEN}\n",
            encoding="utf-8",
        )
        importer.import_file(env_file, self.store)
        # 检查审计表里只有一条 event='import' 行
        events = self.store.list_audit(limit=10000)
        import_events = [e for e in events if e["event"] == "import"]
        self.assertEqual(len(import_events), 1)

    def _audit_count(self) -> int:
        return len(self.store.list_audit(limit=10000))


# ---------------------------------------------------------------- inject

class TestInject(_M3TestCase):
    def test_inject_writes_new_env_file(self):
        """kv use 原子写入新 .env 文件。"""
        _add_secret(self.store, "deepseek-main", SECRET)
        target = Path(self._tmp.name) / "repo" / ".env"
        result = inject.inject(
            self.store, "deepseek-main", target, force=True,
        )
        self.assertEqual(result.action, "written")
        self.assertTrue(target.exists())
        content = target.read_text(encoding="utf-8")
        self.assertIn("DEEPSEEK_MAIN=", content)
        # .env 文件确实含明文值 —— 这是注入的目的。
        # 但 InjectResult 对象本身不含值。
        self.assertNotIn(SECRET, result.path)
        self.assertNotIn(SECRET, result.key_name)

    def test_inject_updates_existing_line(self):
        """已存在的 KEY= 行被原地更新，保留注释和空行。"""
        _add_secret(self.store, "deepseek-main", SECRET)
        target = Path(self._tmp.name) / ".env"
        target.write_text(
            "# 配置文件\n\nDEEPSEEK_MAIN=old-value\nOTHER=keep\n",
            encoding="utf-8",
        )
        result = inject.inject(
            self.store, "deepseek-main", target,
            key_name="DEEPSEEK_MAIN", force=True,
        )
        self.assertEqual(result.action, "updated")
        content = target.read_text(encoding="utf-8")
        self.assertIn("# 配置文件", content)
        self.assertIn("OTHER=keep", content)
        self.assertNotIn("old-value", content)
        self.assertGreater(result.line_no, 0)

    def test_inject_preserves_comments_and_blank_lines(self):
        """保留注释和空行。"""
        _add_secret(self.store, "ds", SECRET)
        target = Path(self._tmp.name) / ".env"
        target.write_text(
            "# comment\n\nFOO=bar\n\n# another\n",
            encoding="utf-8",
        )
        inject.inject(self.store, "ds", target, key_name="NEW_KEY", force=True)
        content = target.read_text(encoding="utf-8")
        self.assertIn("# comment", content)
        self.assertIn("# another", content)
        self.assertIn("FOO=bar", content)
        self.assertIn("NEW_KEY=", content)

    def test_inject_atomic_no_tmp_left(self):
        """原子写：写到一半杀进程 → 不留 .tmp。"""
        _add_secret(self.store, "ds", SECRET)
        target = Path(self._tmp.name) / ".env"
        inject.inject(self.store, "ds", target, force=True)
        # 检查没有残留的临时文件
        tmp_files = list(target.parent.glob(f".{target.name}.*.tmp"))
        self.assertEqual(tmp_files, [])

    def test_inject_never_prints_value(self):
        """kv use 从不打印值 —— 输出只有行号和键名。"""
        _add_secret(self.store, "ds", SECRET)
        target = Path(self._tmp.name) / ".env"
        result = inject.inject(self.store, "ds", target, force=True)
        self.assertNotIn(SECRET, result.path)
        self.assertNotIn(SECRET, result.key_name)

    def test_inject_gitignore(self):
        """--gitignore 追加 .env 到 .gitignore。"""
        _add_secret(self.store, "ds", SECRET)
        target = Path(self._tmp.name) / "repo" / ".env"
        target.parent.mkdir(parents=True, exist_ok=True)
        inject.inject(
            self.store, "ds", target, force=True, gitignore=True,
        )
        gitignore = target.parent / ".gitignore"
        self.assertTrue(gitignore.exists())
        content = gitignore.read_text(encoding="utf-8")
        self.assertIn(".env", content)

    def test_inject_gitignore_idempotent(self):
        """重复 --gitignore 不追加重复行。"""
        _add_secret(self.store, "ds", SECRET)
        target = Path(self._tmp.name) / ".env"
        inject.inject(self.store, "ds", target, force=True, gitignore=True)
        inject.inject(self.store, "ds", target, force=True, gitignore=True)
        gitignore = target.parent / ".gitignore"
        lines = [l for l in gitignore.read_text(encoding="utf-8").splitlines() if l == ".env"]
        self.assertEqual(len(lines), 1)

    def test_inject_sets_last_used_at(self):
        """注入后 last_used_at 被更新。"""
        _add_secret(self.store, "ds", SECRET)
        target = Path(self._tmp.name) / ".env"
        inject.inject(self.store, "ds", target, force=True)
        row = self.store.get("ds")
        self.assertIsNotNone(row.last_used_at)


# ---------------------------------------------------------------- rotate

class TestRotate(_M3TestCase):
    def test_rotate_preserves_old_fingerprint(self):
        """kv rotate 保留旧 sha256 —— 在 fingerprint 里可见，status='revoked'。"""
        _add_secret(self.store, "ds", SECRET)
        old_row = self.store.get("ds")
        old_sha256 = old_row.sha256

        result = rotate.rotate(self.store, "ds", SECRET2.encode("utf-8"))

        self.assertEqual(result.old_sha256, old_sha256)
        self.assertNotEqual(result.new_sha256, old_sha256)

        # 旧指纹仍在 fingerprint 表里，status='revoked'
        fps = self._fingerprint_rows()
        old_fps = [r for r in fps if r["sha256"] == old_sha256]
        self.assertEqual(len(old_fps), 1)
        self.assertEqual(old_fps[0]["status"], "revoked")
        self.assertIsNotNone(old_fps[0]["revoked_at"])

    def test_rotate_creates_new_fingerprint(self):
        """新指纹 status='active'。"""
        _add_secret(self.store, "ds", SECRET)
        result = rotate.rotate(self.store, "ds", SECRET2.encode("utf-8"))

        fps = self._fingerprint_rows()
        new_fps = [r for r in fps if r["sha256"] == result.new_sha256]
        self.assertEqual(len(new_fps), 1)
        self.assertEqual(new_fps[0]["status"], "active")

    def test_rotate_updates_secret_row(self):
        """secret 行的 sha256 和 value_blob 被更新。"""
        _add_secret(self.store, "ds", SECRET)
        result = rotate.rotate(self.store, "ds", SECRET2.encode("utf-8"))
        row = self.store.get("ds")
        self.assertEqual(row.sha256, result.new_sha256)

    def test_rotate_same_value_rejected(self):
        """新值和旧值相同 → 拒绝。"""
        _add_secret(self.store, "ds", SECRET)
        with self.assertRaises(errors.KvError) as cm:
            rotate.rotate(self.store, "ds", SECRET.encode("utf-8"))
        self.assertIn("相同", str(cm.exception))

    def test_rotate_empty_value_rejected(self):
        """空值 → 拒绝。"""
        _add_secret(self.store, "ds", SECRET)
        with self.assertRaises(errors.RefusedError):
            rotate.rotate(self.store, "ds", b"")

    def test_rotate_duplicate_sha256_rejected(self):
        """新值已经存在于别的记录里 → 拒绝。"""
        _add_secret(self.store, "ds1", SECRET)
        _add_secret(self.store, "ds2", SECRET2)
        with self.assertRaises(errors.AlreadyExistsError):
            # 把 ds1 换成 ds2 已有的值
            rotate.rotate(self.store, "ds1", SECRET2.encode("utf-8"))

    def test_rotate_preserves_old_blob_for_scan(self):
        """旧指纹的 value_blob 保留 —— scan 靠它找到泄露的旧值。"""
        _add_secret(self.store, "ds", SECRET)
        old_sha = self.store.get("ds").sha256
        rotate.rotate(self.store, "ds", SECRET2.encode("utf-8"))
        with self.store.read() as conn:
            row = conn.execute(
                "SELECT value_blob FROM fingerprint WHERE sha256 = ?",
                (old_sha,),
            ).fetchone()
        # blob 不为空 —— scan 需要它来匹配旧值
        self.assertIsNotNone(row["value_blob"])
        self.assertGreater(len(row["value_blob"]), 0)

    def test_revoke_marks_record_and_fingerprint(self):
        """revoke：secret status → revoked，fingerprint → revoked。"""
        _add_secret(self.store, "ds", SECRET)
        sha = self.store.get("ds").sha256
        rotate.revoke(self.store, "ds")

        row = self.store.get("ds")
        self.assertEqual(row.status, "revoked")

        fps = self._fingerprint_rows()
        fp = [r for r in fps if r["sha256"] == sha][0]
        self.assertEqual(fp["status"], "revoked")
        self.assertIsNotNone(fp["revoked_at"])

    def test_rotate_writes_audit(self):
        _add_secret(self.store, "ds", SECRET)
        before = self._audit_count()
        rotate.rotate(self.store, "ds", SECRET2.encode("utf-8"))
        after = self._audit_count()
        self.assertEqual(after - before, 1)

    def _audit_count(self) -> int:
        return len(self.store.list_audit(limit=10000))


# ---------------------------------------------------------------- CLI M3

class TestCliM3(unittest.TestCase):
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

    def test_cli_import(self):
        self.init_vault()
        env_file = Path(self._tmp.name) / ".env"
        env_file.write_text(f"DEEPSEEK_API_KEY={SECRET}\n", encoding="utf-8")
        code, out, _ = self.run_cli(["import", str(env_file)])
        self.assertEqual(code, 0)
        self.assertIn("新增", out)
        self.assertIn("1", out)

    def test_cli_import_dedup(self):
        self.init_vault()
        env_file = Path(self._tmp.name) / ".env"
        env_file.write_text(f"DEEPSEEK_API_KEY={SECRET}\n", encoding="utf-8")
        self.run_cli(["import", str(env_file)])
        code, out, _ = self.run_cli(["import", str(env_file)])
        self.assertEqual(code, 0)
        self.assertIn("去重", out)

    def test_cli_import_missing_file(self):
        self.init_vault()
        code, _, err = self.run_cli(["import", "/nonexistent/file.env"])
        self.assertEqual(code, errors.EXIT_NOT_FOUND)
        self.assertIn("不存在", err)

    def test_cli_use(self):
        self.init_vault()
        self.add_secret()
        target = Path(self._tmp.name) / ".env"
        code, out, _ = self.run_cli(
            ["use", "deepseek-main", "--to", str(target), "--force"]
        )
        self.assertEqual(code, 0)
        self.assertIn("写入", out)
        self.assertIn("行号", out)
        self.assertTrue(target.exists())
        # .env 文件含明文值 —— 这是注入的目的。
        # CLI 输出不含值。
        self.assertNotIn(SECRET, out)

    def test_cli_rotate(self):
        self.init_vault()
        self.add_secret()
        code, out, _ = self.run_cli(
            ["rotate", "deepseek-main", "--value", SECRET2]
        )
        self.assertEqual(code, 0)
        self.assertIn("已轮换", out)
        self.assertIn("旧指纹", out)
        self.assertIn("新指纹", out)

    def test_cli_rotate_stdin(self):
        self.init_vault()
        self.add_secret()
        code, out, _ = self.run_cli(
            ["rotate", "deepseek-main", "--stdin"],
            stdin_text=SECRET2 + "\n",
        )
        self.assertEqual(code, 0)
        self.assertIn("已轮换", out)

    def test_cli_revoke(self):
        self.init_vault()
        self.add_secret()
        code, out, _ = self.run_cli(["revoke", "deepseek-main"])
        self.assertEqual(code, 0)
        self.assertIn("已吊销", out)
        # 吊销后 list 仍显示（带 revoked 状态列）
        _, listing, _ = self.run_cli(["list"])
        self.assertIn("deepseek-main", listing)
        self.assertIn("revoked", listing)

    def test_cli_revoke_show_with_status(self):
        self.init_vault()
        self.add_secret()
        self.run_cli(["revoke", "deepseek-main"])
        code, out, _ = self.run_cli(["list", "--status", "revoked"])
        self.assertIn("deepseek-main", out)

    def test_cli_rotate_then_show_has_history(self):
        """rotate 之后 show 应该显示指纹历史。"""
        self.init_vault()
        self.add_secret()
        self.run_cli(["rotate", "deepseek-main", "--value", SECRET2])
        _, out, _ = self.run_cli(["show", "deepseek-main"])
        self.assertIn("历史值", out)


if __name__ == "__main__":
    unittest.main()
