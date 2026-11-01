"""kv host install/uninstall/status：manifest 生成与注册表动作。

winreg 动作整体替换成内存记录（真实 HKCU 不进测试）；manifest 写进临时
目录（--manifest-dir 隐藏参数）。宿主可执行文件用 --host-path 指向临时
文件（开发态默认路径会明确报错，单独测）。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401
from tests.test_cli_m0 import CliTestCase

from kv.host import install as installmod
from kv.host import registry

FAKE_EXE = "C:\\Program Files\\KeyVault\\kv-host.exe"


class RegistryDouble:
    """registry 模块的内存替身：register/unregister/lookup 三件套。"""

    def __init__(self):
        self.keys: dict[str, str] = {}

    def install(self):
        self._saved = (registry.register, registry.unregister, registry.lookup)
        registry.register = self._register
        registry.unregister = self._unregister
        registry.lookup = self._lookup

    def uninstall(self):
        registry.register, registry.unregister, registry.lookup = self._saved

    def _register(self, browser, manifest):
        self.keys[registry.key_path(browser)] = str(manifest)

    def _unregister(self, browser):
        return self.keys.pop(registry.key_path(browser), None) is not None

    def _lookup(self, browser):
        return self.keys.get(registry.key_path(browser))


class HostCliTestCase(CliTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.manifest_dir = Path(self._tmp.name) / "host"
        self.host_path = Path(self._tmp.name) / "kv-host.exe"
        self.host_path.write_bytes(b"MZ")  # 占位，install 只查存在性
        self.double = RegistryDouble()
        self.double.install()
        self.addCleanup(self.double.uninstall)

    def _install_args(self, extra: list[str] | None = None) -> list[str]:
        return [
            "host", "install",
            "--host-path", str(self.host_path),
            "--manifest-dir", str(self.manifest_dir),
            *(extra or []),
        ]


class TestHostInstall(HostCliTestCase):

    def test_install_writes_manifest_and_registers(self):
        code, out, err = self.run_cli(self._install_args(
            ["--extension-id", "abcdefghijklmnopabcdefghijklmnop"]))
        self.assertEqual(code, 0, err)
        manifest = self.manifest_dir / f"{registry.HOST_NAME}.json"
        self.assertTrue(manifest.exists())
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(data["name"], registry.HOST_NAME)
        self.assertEqual(data["type"], "stdio")
        self.assertEqual(data["allowed_origins"],
                         ["chrome-extension://abcdefghijklmnopabcdefghijklmnop/"])
        self.assertIn(registry.key_path("chrome"), self.double.keys)
        self.assertIn(registry.key_path("edge"), self.double.keys)
        self.assertIn("chrome://extensions", out)  # 下一步指引

    def test_install_single_browser(self):
        code, out, err = self.run_cli(self._install_args(
            ["--browser", "edge", "--extension-id", "abcdefghijklmnopabcdefghijklmnop"]))
        self.assertEqual(code, 0, err)
        self.assertNotIn(registry.key_path("chrome"), self.double.keys)
        self.assertIn(registry.key_path("edge"), self.double.keys)

    def test_install_rejects_bad_extension_id(self):
        code, out, err = self.run_cli(self._install_args(
            ["--extension-id", "not-an-id"]))
        self.assertEqual(code, 1)
        self.assertIn("格式不对", err)

    def test_install_rejects_missing_host_binary(self):
        self.host_path.unlink()
        code, out, err = self.run_cli(self._install_args(
            ["--extension-id", "abcdefghijklmnopabcdefghijklmnop"]))
        self.assertEqual(code, 1)
        self.assertIn("不存在", err)

    def test_dev_mode_without_host_path_errors(self):
        code, out, err = self.run_cli(
            ["host", "install", "--extension-id", "abcdefghijklmnopabcdefghijklmnop"])
        self.assertEqual(code, 1)
        self.assertIn("开发态", err)


class TestHostUninstallStatus(HostCliTestCase):

    def test_uninstall_removes_keys_idempotently(self):
        self.run_cli(self._install_args(
            ["--extension-id", "abcdefghijklmnopabcdefghijklmnop"]))
        code, out, err = self.run_cli(["host", "uninstall"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.double.keys, {})
        code, out, err = self.run_cli(["host", "uninstall"])
        self.assertEqual(code, 0, err)
        self.assertIn("本就未注册", out)

    def test_status_reports_registered_and_missing_manifest(self):
        code, out, err = self.run_cli(["host", "status"])
        self.assertEqual(code, 0, err)
        self.assertIn("未注册", out)
        self.run_cli(self._install_args(
            ["--extension-id", "abcdefghijklmnopabcdefghijklmnop"]))
        code, out, err = self.run_cli(["host", "status"])
        self.assertEqual(code, 0, err)
        self.assertIn("manifest 正常", out)
        self.assertIn("abcdefghijklmnopabcdefghijklmnop", out)


class TestIdValidation(unittest.TestCase):
    """install 模块的 ID 校验单测（不走 CLI）。"""

    def test_valid_ids_deduped_and_lowered(self):
        ids = installmod._validated_ids(
            ["ABCDEFGHIJKLMNOPABCDEFGHIJKLMNOP", "abcdefghijklmnopabcdefghijklmnop"])
        self.assertEqual(ids, ["abcdefghijklmnopabcdefghijklmnop"])

    def test_empty_ids_raise(self):
        from kv import errors

        with self.assertRaises(errors.UsageError):
            installmod._validated_ids([])


class TestDefaultHostPath(unittest.TestCase):
    """default_host_path：冻结态找同目录 kv-host.exe（绝不把 kv-cli 注册成宿主）。"""

    def tearDown(self):
        sys.frozen = self._old_frozen
        sys.executable = self._old_executable

    def _frozen(self, executable: str):
        self._old_frozen = getattr(sys, "frozen", False)
        self._old_executable = sys.executable
        sys.frozen = True
        sys.executable = executable

    def test_frozen_cli_picks_sibling_kv_host(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            sibling = Path(tmp) / "kv-host.exe"
            sibling.write_bytes(b"MZ")
            self._frozen(str(Path(tmp) / "kv-cli.exe"))
            self.assertEqual(installmod.default_host_path(), sibling)

    def test_frozen_host_returns_itself(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            me = Path(tmp) / "kv-host.exe"
            me.write_bytes(b"MZ")
            self._frozen(str(me))
            self.assertEqual(installmod.default_host_path(), me)

    def test_frozen_without_sibling_errors(self):
        import tempfile

        from kv import errors

        with tempfile.TemporaryDirectory() as tmp:
            self._frozen(str(Path(tmp) / "kv-cli.exe"))
            with self.assertRaises(errors.UsageError):
                installmod.default_host_path()

    def test_dev_mode_errors(self):
        from kv import errors

        self._old_frozen = getattr(sys, "frozen", False)
        self._old_executable = sys.executable
        sys.frozen = False
        with self.assertRaises(errors.UsageError):
            installmod.default_host_path()


if __name__ == "__main__":
    unittest.main()
