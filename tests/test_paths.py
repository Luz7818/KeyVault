"""paths：vault 目录定位、云同步拒绝、自排除 .gitignore、git 归属判定。"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401

from kv import errors, paths

HAS_GIT = shutil.which("git") is not None


class TestVaultDir(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get(paths.ENV_OVERRIDE)
        self._saved_local = os.environ.get("LOCALAPPDATA")
        self.addCleanup(self._restore)

    def _restore(self):
        for key, value in ((paths.ENV_OVERRIDE, self._saved), ("LOCALAPPDATA", self._saved_local)):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_env_override_wins(self):
        os.environ[paths.ENV_OVERRIDE] = str(Path(tempfile.gettempdir()) / "kv-override")
        self.assertEqual(
            paths.vault_dir(), (Path(tempfile.gettempdir()) / "kv-override").resolve()
        )

    def test_default_is_localappdata_not_appdata(self):
        """%APPDATA% 会被域漫游同步 —— 一份密钥库跟着你在域内每台机器上跑。"""
        os.environ.pop(paths.ENV_OVERRIDE, None)
        os.environ["LOCALAPPDATA"] = str(Path(tempfile.gettempdir()) / "local-dir")
        result = paths.vault_dir()
        self.assertEqual(result.name, "KeyVault")
        self.assertIn("local-dir", str(result))

    def test_missing_localappdata_raises_with_hint(self):
        os.environ.pop(paths.ENV_OVERRIDE, None)
        os.environ.pop("LOCALAPPDATA", None)
        with self.assertRaises(errors.UsageError) as ctx:
            paths.vault_dir()
        self.assertIn(paths.ENV_OVERRIDE, ctx.exception.hint)

    def test_db_path_is_inside_vault_dir(self):
        os.environ[paths.ENV_OVERRIDE] = str(Path(tempfile.gettempdir()) / "kv-x")
        self.assertEqual(paths.vault_db(), paths.vault_dir() / "vault.db")


class TestCloudSyncDetection(unittest.TestCase):
    def test_detects_known_markers(self):
        for marker in ("OneDrive", "Dropbox", "Google Drive", "坚果云", "BaiduSyncdisk", "nutstore"):
            with self.subTest(marker=marker):
                hit = paths.is_cloud_synced(Path("C:/Users/me") / marker / "KeyVault")
                self.assertTrue(hit, marker)

    def test_case_insensitive(self):
        self.assertTrue(paths.is_cloud_synced(Path("C:/users/me/onedrive/vault")))

    def test_localappdata_is_safe(self):
        self.assertEqual(paths.is_cloud_synced(Path("C:/Users/me/AppData/Local/KeyVault")), "")

    def test_chinese_project_path_is_safe(self):
        """本项目就在中文路径上，不能因此误报。"""
        self.assertEqual(
            paths.is_cloud_synced(Path("D:/东南大学/项目/Project/KeyVault")), ""
        )

    def test_refuse_raises_without_force(self):
        with self.assertRaises(errors.UsageError) as ctx:
            paths.refuse_unsafe_dir(Path("C:/Users/me/OneDrive/backup.kvb"), what="导出容器")
        self.assertIn("OneDrive", str(ctx.exception))
        self.assertIn("--force", ctx.exception.hint)

    def test_refuse_allows_with_force(self):
        paths.refuse_unsafe_dir(
            Path("C:/Users/me/OneDrive/backup.kvb"), what="导出容器", force=True
        )

    def test_refuse_allows_safe_path(self):
        paths.refuse_unsafe_dir(Path("C:/Users/me/AppData/Local/b.kvb"), what="导出容器")


class TestSelfExcludingGitignore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_writes_exact_body(self):
        target = paths.ensure_self_excluding_gitignore(self.root)
        self.assertEqual(target.read_text(encoding="utf-8"), "*\n!.gitignore\n")
        self.assertEqual(target.read_text(encoding="utf-8").splitlines(), ["*", "!.gitignore"])

    def test_idempotent(self):
        paths.ensure_self_excluding_gitignore(self.root)
        paths.ensure_self_excluding_gitignore(self.root)
        self.assertEqual(
            (self.root / ".gitignore").read_text(encoding="utf-8"), "*\n!.gitignore\n"
        )

    def test_refuses_to_overwrite_different_content(self):
        """不要改窄它 —— 那样 fingerprints、revoked 和将来新增的任何文件都会变成可提交状态。"""
        (self.root / ".gitignore").write_text("*.env\n", encoding="utf-8")
        with self.assertRaises(errors.UsageError) as ctx:
            paths.ensure_self_excluding_gitignore(self.root)
        self.assertIn("不要改窄", ctx.exception.hint)


@unittest.skipUnless(HAS_GIT, "需要 git")
class TestGitHelpers(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self._git("init", "-q")
        self._git("config", "user.email", "t@t.t")
        self._git("config", "user.name", "t")

    def _git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )

    def test_toplevel_found(self):
        nested = self.root / "a" / "b"
        nested.mkdir(parents=True)
        self.assertEqual(paths.git_toplevel(nested).resolve(), self.root.resolve())

    def test_toplevel_none_outside_repo(self):
        with tempfile.TemporaryDirectory() as other:
            self.assertIsNone(paths.git_toplevel(Path(other)))

    def test_tracked_file_detected(self):
        target = self.root / ".env"
        target.write_text("A=1\n", encoding="utf-8")
        self._git("add", "-f", ".env")
        self.assertTrue(paths.is_git_tracked(target))

    def test_untracked_file_not_flagged(self):
        target = self.root / ".env"
        target.write_text("A=1\n", encoding="utf-8")
        self.assertFalse(paths.is_git_tracked(target))

    def test_ignored_file_detected(self):
        (self.root / ".gitignore").write_text(".env\n", encoding="utf-8")
        target = self.root / ".env"
        target.write_text("A=1\n", encoding="utf-8")
        self.assertTrue(paths.is_git_ignored(target))
        self.assertFalse(paths.is_git_tracked(target))

    def test_helpers_do_not_raise_outside_a_repo(self):
        with tempfile.TemporaryDirectory() as other:
            stray = Path(other) / ".env"
            stray.write_text("A=1\n", encoding="utf-8")
            self.assertFalse(paths.is_git_tracked(stray))
            self.assertFalse(paths.is_git_ignored(stray))


if __name__ == "__main__":
    unittest.main()
