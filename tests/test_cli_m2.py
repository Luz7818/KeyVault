"""M2 端到端 CLI 验收：copy / watch / review / fix / set-platform / rename / tag / corrections。

对应计划里 M2 的验收判据。真剪切板 / 真 DPAPI 的测试用 skipUnless 标出。
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401

from kv import cli, console, errors

WINDOWS = sys.platform == "win32"
SECRET = "sk-" + "A" * 44 + "c31f"


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.addCleanup(self._tmp.cleanup)

    def run_cli(self, argv, *, stdin_text=None, vault=True):
        full = list(argv)
        if vault:
            full = [full[0], "--vault-dir", str(self.vault), *full[1:]]
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

    def add_secret(self, name="deepseek-main", value=SECRET, *extra):
        code, out, err = self.run_cli(
            ["add", "--name", name, "--value", value, "--platform", "deepseek", *extra]
        )
        self.assertEqual(code, 0, err or out)
        return out


class TestSetPlatform(CliTestCase):
    def test_set_platform_changes_record(self):
        self.init_vault()
        self.add_secret("amb", SECRET)
        # add 时 bare sk- 存成 openai-compatible / ambiguous
        code, out, _ = self.run_cli(["set-platform", "amb", "deepseek"])
        self.assertEqual(code, 0, self.err)
        self.assertIn("deepseek", out)

    def test_set_platform_unknown_platform_refused(self):
        self.init_vault()
        self.add_secret()
        code, _, err = self.run_cli(["set-platform", "deepseek-main", "no-such-platform"])
        self.assertEqual(code, errors.EXIT_USAGE)
        self.assertIn("不在检测表里", err)

    def test_set_platform_remember_keyname(self):
        self.init_vault()
        self.add_secret("amb", SECRET, "--key-name", "DEEPSEEK_API_KEY")
        code, out, _ = self.run_cli([
            "set-platform", "amb", "deepseek", "--remember", "keyname",
        ])
        self.assertEqual(code, 0, self.err)
        self.assertIn("记住", out)

    def test_set_platform_with_allow_unknown(self):
        self.init_vault()
        self.add_secret()
        code, out, _ = self.run_cli([
            "set-platform", "deepseek-main", "my-custom-platform",
            "--allow-unknown-platform",
        ])
        self.assertEqual(code, 0, self.err)
        self.assertIn("my-custom-platform", out)


class TestRename(CliTestCase):
    def test_rename_changes_name_and_creates_alias(self):
        self.init_vault()
        self.add_secret("old-name")
        code, out, _ = self.run_cli(["rename", "old-name", "new-name"])
        self.assertEqual(code, 0, self.err)
        self.assertIn("new-name", out)
        self.assertIn("别名", out)
        # 旧名仍可访问（作为别名）
        code2, out2, _ = self.run_cli(["show", "old-name"])
        self.assertEqual(code2, 0)
        self.assertIn("new-name", out2)

    def test_rename_to_existing_name_errors(self):
        self.init_vault()
        self.add_secret("first")
        self.add_secret("second", value="sk-" + "B" * 44)
        code, _, err = self.run_cli(["rename", "first", "second"])
        self.assertNotEqual(code, 0)


class TestTag(CliTestCase):
    def test_add_and_remove_tags(self):
        self.init_vault()
        self.add_secret()
        code, out, _ = self.run_cli(["tag", "deepseek-main", "--add", "prod", "--add", "ci"])
        self.assertEqual(code, 0, self.err)
        self.assertIn("ci, prod", out)
        code2, out2, _ = self.run_cli(["tag", "deepseek-main", "--remove", "ci"])
        self.assertEqual(code2, 0)
        self.assertIn("prod", out2)
        self.assertNotIn("ci", out2.split("标签")[1] if "标签" in out2 else "")


class TestCorrections(CliTestCase):
    def test_corrections_empty(self):
        self.init_vault()
        code, out, _ = self.run_cli(["corrections"])
        self.assertEqual(code, 0)
        self.assertIn("还没有", out)

    def test_corrections_after_remember(self):
        self.init_vault()
        self.add_secret("amb", SECRET, "--key-name", "DEEPSEEK_API_KEY")
        self.run_cli(["set-platform", "amb", "deepseek", "--remember", "keyname"])
        code, out, _ = self.run_cli(["corrections"])
        self.assertEqual(code, 0)
        self.assertIn("keyname", out)
        self.assertIn("deepseek", out)

    def test_corrections_delete(self):
        self.init_vault()
        self.add_secret("amb", SECRET, "--key-name", "DEEPSEEK_API_KEY")
        self.run_cli(["set-platform", "amb", "deepseek", "--remember", "keyname"])
        # 找到纠正规则的 id
        _, listing, _ = self.run_cli(["corrections"])
        self.assertIn("1", listing)
        code, out, _ = self.run_cli(["corrections", "--delete", "1"])
        self.assertEqual(code, 0, self.err)
        self.assertIn("已删除", out)


class TestFix(CliTestCase):
    def test_fix_with_no_unresolved(self):
        self.init_vault()
        self.add_secret()  # manual confidence → not unresolved
        code, out, _ = self.run_cli(["fix"])
        self.assertEqual(code, 0)
        self.assertIn("没有识别不出", out)


class TestReview(CliTestCase):
    def test_review_empty_inbox(self):
        self.init_vault()
        code, out, _ = self.run_cli(["review"])
        self.assertEqual(code, 0)
        self.assertIn("待审队列是空的", out)


class TestSweep(CliTestCase):
    def test_sweep_with_no_due_wipes(self):
        self.init_vault()
        code, out, _ = self.run_cli(["sweep"])
        self.assertEqual(code, 0)
        self.assertIn("没有过期", out)


@unittest.skipUnless(WINDOWS, "真剪切板操作要 Windows")
class TestCopyViaCli(CliTestCase):
    """需要真 Win32 剪切板的端到端测试。"""

    def test_copy_puts_value_on_clipboard(self):
        self.init_vault()
        self.add_secret()
        code, out, _ = self.run_cli(["copy", "deepseek-main", "--no-wipe"])
        self.assertEqual(code, 0, self.err)
        self.assertIn("复制到剪切板", out)
        self.assertIn("sk-", out)  # preview shows mask, not value
        self.assertNotIn(SECRET, out)


if __name__ == "__main__":
    unittest.main()
