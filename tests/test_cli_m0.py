"""M0 端到端验收：init / doctor / add / list / show / reveal / rm / config。

对应计划里 M0 的验收判据。凡是要真 DPAPI 或真子进程的，都用 skipUnless 标出来。
"""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401

from kv import cli, console, errors, paths

WINDOWS = sys.platform == "win32"
REPO_ROOT = Path(__file__).resolve().parent.parent
KV_PY = REPO_ROOT / "kv.py"

SECRET = "sk-" + "A" * 44 + "c31f"


def line_with(output: str, label: str) -> str:
    """取含某个标签的那一行。

    断言「标签和值在同一行」而不是数空格 —— 标签列宽一改，几十个用例不该跟着崩。
    """
    for line in output.splitlines():
        if label in line:
            return line
    raise AssertionError(f"输出里没有含 {label!r} 的行：\n{output}")


def value_column(line: str, label: str) -> int:
    """值起始的显示列。标签长度不同，靠补齐让它们对齐到同一列。"""
    after = line.index(label) + len(label)
    rest = line[after:]
    return console.display_width(line[:after]) + (len(rest) - len(rest.lstrip(" ")))


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
        # 警告与提示走 stderr，正常输出走 stdout。多数断言关心的是「用户看到了什么」，
        # 所以给一个合并视图，省得每个用例都要想清楚消息在哪条流上。
        self.all = self.out + self.err
        return code, self.out, self.err

    def init_vault(self):
        code, out, err = self.run_cli(["init"])
        self.assertEqual(code, 0, err)
        return out

    def add_secret(self, name="deepseek-main", value=SECRET, *extra):
        """value 是第 2 个位置参数：要传额外 flag 就得先把 value 显式写出来。

        --platform 给了默认值，extra 里再给一次会覆盖它（argparse 后者胜）。
        """
        code, out, err = self.run_cli(
            ["add", "--name", name, "--value", value, "--platform", "deepseek", *extra]
        )
        self.assertEqual(code, 0, err or out)
        return out

    def raw_db_bytes(self) -> bytes:
        blobs = b""
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.vault / "vault.db") + suffix)
            if candidate.exists():
                blobs += candidate.read_bytes()
        return blobs


class TestInit(CliTestCase):
    def test_creates_vault_dir_and_self_excluding_gitignore(self):
        self.init_vault()
        gitignore = self.vault / ".gitignore"
        self.assertTrue(gitignore.exists())
        self.assertEqual(gitignore.read_text(encoding="utf-8"), "*\n!.gitignore\n")
        self.assertEqual(gitignore.read_text(encoding="utf-8").splitlines(), ["*", "!.gitignore"])

    def test_creates_database(self):
        self.init_vault()
        self.assertTrue((self.vault / "vault.db").exists())

    def test_init_is_idempotent(self):
        self.init_vault()
        code, _, err = self.run_cli(["init"])
        self.assertEqual(code, 0)
        self.assertIn("已经初始化", err)

    def test_init_nags_about_export_immediately(self):
        """R1：DPAPI 密钥丢失不可恢复，检测得到但恢复不了。
        首次导出必须是一等行为，所以 init 就要说出来。"""
        self.init_vault()
        self.assertIn("无法恢复", self.all)
        self.assertIn("export", self.all)

    def test_init_refuses_cloud_sync_dir(self):
        self.vault = Path(self._tmp.name) / "OneDrive" / "KeyVault"
        code, _, err = self.run_cli(["init"])
        self.assertEqual(code, errors.EXIT_USAGE)
        self.assertIn("云同步", err)

    def test_vault_dir_accepted_before_subcommand(self):
        """argparse 陷阱：子命令层的普通默认值会把顶层 --vault-dir 覆盖掉。"""
        code, out, err = self.capture(["--vault-dir", str(self.vault), "init"])
        self.assertEqual(code, 0, err)
        self.assertTrue((self.vault / "vault.db").exists(), out)

    def test_vault_dir_accepted_after_subcommand(self):
        code, _, err = self.capture(["init", "--vault-dir", str(self.vault)])
        self.assertEqual(code, 0, err)
        self.assertTrue((self.vault / "vault.db").exists())

    def capture(self, argv, stdin_text=None):
        out, err = io.StringIO(), io.StringIO()
        saved = (sys.stdout, sys.stderr, sys.stdin)
        sys.stdout, sys.stderr = out, err
        if stdin_text is not None:
            sys.stdin = io.StringIO(stdin_text)
        try:
            code = cli.main(argv)
        finally:
            sys.stdout, sys.stderr, sys.stdin = saved
        return code, out.getvalue(), err.getvalue()


class TestDoctor(CliTestCase):
    def test_reports_binding_ok(self):
        self.init_vault()
        code, out, _ = self.run_cli(["doctor"])
        self.assertEqual(code, 0)
        self.assertIn("正常", line_with(out, "DPAPI 绑定"))

    def test_reports_counts_and_versions(self):
        self.init_vault()
        self.add_secret()
        code, out, _ = self.run_cli(["doctor"])
        self.assertEqual(code, 0)
        self.assertIn("1", line_with(out, "记录数"))
        self.assertIn("1", line_with(out, "schema 版本"))

    def test_reports_gitignore_state(self):
        self.init_vault()
        _, out, _ = self.run_cli(["doctor"])
        self.assertIn("正确", line_with(out, "自排除 .gitignore"))

    def test_doctor_fields_are_column_aligned(self):
        """中文标签占 2 列 —— 不按显示宽度补齐，值就会参差不齐。"""
        self.init_vault()
        _, out, _ = self.run_cli(["doctor"])
        columns = {value_column(line_with(out, label), label)
                   for label in ("vault 目录", "已初始化", "SQLite", "DPAPI 绑定", "记录数")}
        self.assertEqual(len(columns), 1, f"值起始列不一致：{columns}")

    def test_uninitialized_vault_nags(self):
        code, _, err = self.run_cli(["doctor"])
        self.assertEqual(code, 0)
        self.assertIn("还没初始化", err)
        self.assertIn("否", line_with(self.out, "已初始化"))

    def test_commands_refuse_uninitialized_vault(self):
        code, _, err = self.run_cli(["list"])
        self.assertEqual(code, errors.EXIT_BINDING)
        self.assertIn("kv init", err)


class TestAdd(CliTestCase):
    def test_creates_record_with_metadata(self):
        self.init_vault()
        out = self.add_secret()
        self.assertIn("已保存 #1 deepseek-main", out)
        self.assertIn("deepseek", out)
        self.assertIn("创建时间", out)

    def test_masked_preview_shows_prefix_and_tail_only(self):
        self.init_vault()
        self.add_secret()
        _, out, _ = self.run_cli(["list"])
        self.assertIn("sk-****c31f", out)
        self.assertNotIn("A" * 20, out)
        self.assertNotIn(SECRET, out)

    def test_add_auto_detects_platform_from_shape(self):
        """M1：不给 --platform 也能认出来。"""
        self.init_vault()
        code, out, _ = self.run_cli(["add", "--name", "gh", "--value", "ghp_" + "B" * 36])
        self.assertEqual(code, 0, self.err)
        self.assertIn("github", line_with(out, "平台"))
        self.assertIn("medium", line_with(out, "平台"))

    def test_add_extracts_key_and_platform_from_curl(self):
        """从 curl 命令里抽出 key，并用 URL 主机名精确判定平台。"""
        self.init_vault()
        curl = f"curl https://api.deepseek.com/v1/chat -H 'Authorization: Bearer {SECRET}'"
        code, out, _ = self.run_cli(["add", "--name", "ds", "--value", curl])
        self.assertEqual(code, 0, self.err)
        self.assertIn("deepseek", line_with(out, "平台"))
        self.assertIn("exact", line_with(out, "平台"))
        # 存进去的是抽出来的 key，不是整条 curl 命令
        _, shown, _ = self.run_cli(["show", "ds"])
        self.assertIn("51", line_with(shown, "预览"))

    def test_bare_sk_stored_ambiguous_never_guessed(self):
        """M1 的核心承诺：裸 sk- 存成 openai-compatible，**绝不猜一个厂商**。

        猜错会把一个假事实写进明文、可搜索、永久的元数据列，半年后分不清
        哪条是确定的、哪条是它猜的。
        """
        self.init_vault()
        code, out, err = self.run_cli(["add", "--name", "amb", "--value", SECRET])
        self.assertEqual(code, 0, err)
        self.assertIn("openai-compatible", line_with(out, "平台"))
        self.assertIn("ambiguous", line_with(out, "平台"))
        self.assertIn("歧义", err)
        for vendor in ("deepseek", "siliconflow", "moonshot", "zhipu", "dashscope"):
            self.assertNotIn(vendor, line_with(out, "平台"))

    def test_bare_sk_can_be_pinned_by_window_title(self):
        self.init_vault()
        code, out, _ = self.run_cli([
            "add", "--name", "ds", "--value", SECRET,
            "--window-title", "DeepSeek API开放平台 - Google Chrome",
        ])
        self.assertEqual(code, 0, self.err)
        self.assertIn("deepseek", line_with(out, "平台"))
        self.assertIn("high", line_with(out, "平台"))

    def test_unrecognizable_value_refuses_without_force(self):
        """裸 hex-32 也是 MD5 / GUID —— 认不出来就拒绝，不静默存成 unknown。"""
        self.init_vault()
        code, _, err = self.run_cli(
            ["add", "--name", "hex", "--value", "0123456789abcdef" * 2]
        )
        self.assertEqual(code, errors.EXIT_USAGE)
        self.assertIn("不可信", err)

    def test_unrecognizable_value_accepts_force(self):
        self.init_vault()
        code, out, _ = self.run_cli(
            ["add", "--name", "hex", "--value", "0123456789abcdef" * 2,
             "--platform", "amap"]
        )
        self.assertEqual(code, 0, self.err)
        self.assertIn("已保存", out)

    def test_multi_credential_input_refuses_and_lists(self):
        """悄悄只存第一条是比报错严重得多的失败 —— 必须拒绝并列出找到了什么。"""
        self.init_vault()
        text = f"DEEPSEEK_API_KEY={SECRET}\nGITHUB_TOKEN=ghp_" + "B" * 36
        code, _, err = self.run_cli(["add", "--name", "multi", "--value", text])
        self.assertEqual(code, errors.EXIT_USAGE)
        self.assertIn("2 个凭据", err)
        self.assertIn("kv detect", err)
        _, listing, _ = self.run_cli(["list"])
        self.assertIn("（空）", listing)

    def test_explicit_platform_overrides_detection(self):
        self.init_vault()
        code, out, _ = self.run_cli(
            ["add", "--name", "pinned", "--value", SECRET, "--platform", "deepseek"]
        )
        self.assertEqual(code, 0, self.err)
        self.assertIn("deepseek", line_with(out, "平台"))
        self.assertIn("manual", line_with(out, "平台"))

    def test_duplicate_value_dedupes(self):
        self.init_vault()
        self.add_secret("first")
        code, out, _ = self.run_cli(
            ["add", "--name", "second", "--value", SECRET, "--platform", "deepseek"]
        )
        self.assertEqual(code, 0)
        self.assertIn("已经在库里", out)
        _, listing, _ = self.run_cli(["list"])
        self.assertIn("共 1 条", listing)

    def test_duplicate_name_different_value_errors(self):
        self.init_vault()
        self.add_secret("taken")
        code, _, err = self.run_cli(
            ["add", "--name", "taken", "--value", "sk-" + "B" * 44, "--platform", "openai"]
        )
        self.assertEqual(code, errors.EXIT_USAGE)
        self.assertIn("已经有一条", err)

    def test_value_from_stdin(self):
        self.init_vault()
        code, out, _ = self.run_cli(
            ["add", "--name", "piped", "--stdin", "--platform", "deepseek"],
            stdin_text=SECRET + "\n",
        )
        self.assertEqual(code, 0, out)
        _, listing, _ = self.run_cli(["list"])
        self.assertIn("piped", listing)

    def test_stdin_value_is_canonicalized(self):
        """尾部换行必须被规范化掉，否则每次管道输入都产生一条重复行。"""
        self.init_vault()
        self.run_cli(["add", "--name", "a", "--stdin", "--platform", "deepseek"],
                     stdin_text=SECRET + "\r\n")
        code, out, _ = self.run_cli(
            ["add", "--name", "b", "--value", SECRET, "--platform", "deepseek"]
        )
        self.assertIn("已经在库里", out)

    def test_value_flag_warns_about_shell_history(self):
        self.init_vault()
        _, _, err = self.run_cli(["add", "--name", "w", "--value", SECRET, "--platform", "x"])
        self.assertIn("shell 历史", err)

    def test_tags_note_and_source_url(self):
        self.init_vault()
        self.add_secret("t", SECRET, "--tag", "prod", "--tag", "ci",
                        "--note", "给 CI 用的", "--source-url", "https://platform.deepseek.com/")
        _, out, _ = self.run_cli(["show", "t"])
        self.assertIn("ci, prod", out)
        self.assertIn("给 CI 用的", out)
        self.assertIn("platform.deepseek.com", out)

    def test_note_containing_a_credential_refused(self):
        """R4：note 是明文列，而它恰恰是用户会粘贴东西的地方。"""
        self.init_vault()
        code, _, err = self.run_cli(
            ["add", "--name", "n", "--value", SECRET, "--platform", "deepseek",
             "--note", "备用 sk-" + "A" * 40]
        )
        self.assertEqual(code, errors.EXIT_USAGE)
        self.assertIn("明文可搜索列", err)

    def test_empty_value_refused(self):
        self.init_vault()
        code, _, err = self.run_cli(["add", "--name", "e", "--stdin", "--platform", "x"],
                                    stdin_text="   \n")
        self.assertEqual(code, errors.EXIT_USAGE)
        self.assertIn("值为空", err)

    def test_key_name_recorded(self):
        self.init_vault()
        self.add_secret("kn", SECRET, "--key-name", "DEEPSEEK_API_KEY")
        _, out, _ = self.run_cli(["show", "kn"])
        self.assertIn("DEEPSEEK_API_KEY", out)


class TestListShowReveal(CliTestCase):
    def setUp(self):
        super().setUp()
        self.init_vault()
        self.add_secret("deepseek-main", SECRET, "--tag", "prod")
        self.add_secret("github-ci", "ghp_" + "B" * 36, "--platform", "github", "--tag", "ci")
        self.add_secret("amap-rest", "0123456789abcdef" * 2, "--platform", "amap",
                        "--key-name", "AMAP_REST_KEY")

    def test_list_shows_all(self):
        _, out, _ = self.run_cli(["list"])
        for name in ("deepseek-main", "github-ci", "amap-rest"):
            self.assertIn(name, out)
        self.assertIn("共 3 条", out)

    def test_list_never_shows_a_value(self):
        _, out, _ = self.run_cli(["list"])
        self.assertNotIn(SECRET, out)
        self.assertNotIn("B" * 20, out)

    def test_list_filter_by_platform(self):
        _, out, _ = self.run_cli(["list", "--platform", "github"])
        self.assertIn("github-ci", out)
        self.assertNotIn("deepseek-main", out)

    def test_list_filter_by_tag(self):
        _, out, _ = self.run_cli(["list", "--tag", "prod"])
        self.assertIn("deepseek-main", out)
        self.assertIn("共 1 条", out)

    def test_list_query(self):
        _, out, _ = self.run_cli(["list", "--query", "AMAP_REST"])
        self.assertIn("amap-rest", out)
        self.assertIn("共 1 条", out)

    def test_list_ls_alias(self):
        _, out, _ = self.run_cli(["ls"])
        self.assertIn("共 3 条", out)

    def test_list_json_has_no_value(self):
        _, out, _ = self.run_cli(["list", "--json"])
        self.assertNotIn(SECRET, out)
        self.assertNotIn("value_blob", out)
        self.assertIn('"platform": "deepseek"', out)

    def test_list_with_no_match_says_so(self):
        code, out, _ = self.run_cli(["list", "--query", "zzz-no-such-record"])
        self.assertEqual(code, 0)
        self.assertIn("（空）", out)

    def test_list_on_absent_vault_reports_binding_exit_code(self):
        code, out, err = self.run_cli(["list"], vault=False)
        self.assertEqual(code, errors.EXIT_BINDING)
        self.assertEqual(out, "")
        self.assertIn("kv init", err)

    def test_show_prints_metadata_and_fingerprint(self):
        _, out, _ = self.run_cli(["show", "deepseek-main"])
        self.assertIn("deepseek-main", out)
        self.assertIn("判定依据", out)
        self.assertIn("指纹", out)
        self.assertNotIn(SECRET, out)

    def test_show_missing_name(self):
        code, _, err = self.run_cli(["show", "nope"])
        self.assertEqual(code, errors.EXIT_NOT_FOUND)
        self.assertIn("nope", err)

    def test_reveal_prints_value_and_audits(self):
        code, out, _ = self.run_cli(["reveal", "deepseek-main", "--yes"])
        self.assertEqual(code, 0)
        self.assertIn(SECRET, out)
        _, shown, _ = self.run_cli(["show", "deepseek-main"])
        self.assertIn("最后 reveal", shown)

    def test_reveal_without_yes_cancels_on_no(self):
        code, out, _ = self.run_cli(["reveal", "deepseek-main"], stdin_text="n\n")
        self.assertEqual(code, 0)
        self.assertIn("已取消", out)
        self.assertNotIn(SECRET, out)

    def test_reveal_with_yes_proceeds(self):
        code, out, _ = self.run_cli(["reveal", "deepseek-main"], stdin_text="y\n")
        self.assertEqual(code, 0)
        self.assertIn(SECRET, out)

    def test_reveal_writes_audit_row(self):
        before = self.audit_count()
        self.run_cli(["reveal", "deepseek-main", "--yes"])
        self.assertEqual(self.audit_count(), before + 1)

    def audit_count(self) -> int:
        from kv.core.vault import VaultStore

        return len(VaultStore(self.vault / "vault.db").list_audit(limit=10000))


class TestEmptyVault(CliTestCase):
    def test_list_on_initialized_empty_vault(self):
        self.init_vault()
        code, out, _ = self.run_cli(["list"])
        self.assertEqual(code, 0)
        self.assertIn("（空）", out)

    def test_config_on_empty_vault(self):
        self.init_vault()
        code, out, _ = self.run_cli(["config"])
        self.assertEqual(code, 0)
        self.assertIn("还没有任何配置项", out)


class TestRmConfig(CliTestCase):
    def test_rm_with_yes(self):
        self.init_vault()
        self.add_secret()
        code, out, _ = self.run_cli(["rm", "deepseek-main", "--yes"])
        self.assertEqual(code, 0)
        self.assertIn("已删除", out)
        _, listing, _ = self.run_cli(["list"])
        self.assertIn("（空）", listing)

    def test_rm_cancels_on_no(self):
        self.init_vault()
        self.add_secret()
        code, out, _ = self.run_cli(["rm", "deepseek-main"], stdin_text="n\n")
        self.assertEqual(code, 0)
        self.assertIn("已取消", out)
        _, listing, _ = self.run_cli(["list"])
        self.assertIn("deepseek-main", listing)

    def test_config_set_and_get(self):
        self.init_vault()
        code, _, _ = self.run_cli(["config", "--set", "mask_char=*"])
        self.assertEqual(code, 0)
        _, out, _ = self.run_cli(["config", "--get", "mask_char"])
        self.assertEqual(out.strip(), "*")

    def test_config_list(self):
        self.init_vault()
        self.run_cli(["config", "--set", "a=1"])
        self.run_cli(["config", "--set", "b=2"])
        _, out, _ = self.run_cli(["config"])
        self.assertIn("a", out)
        self.assertIn("b", out)

    def test_config_set_bad_syntax(self):
        self.init_vault()
        code, _, err = self.run_cli(["config", "--set", "no-equals-sign"])
        self.assertEqual(code, errors.EXIT_USAGE)
        self.assertIn("KEY=VALUE", err)


@unittest.skipUnless(WINDOWS, "静息加密与真 DPAPI 绑定要 Windows")
class TestAtRestViaCli(CliTestCase):
    def test_value_absent_from_raw_db_file(self):
        """M0 硬指标：用值去 grep 原始 DB 文件必须返回空。"""
        self.init_vault()
        self.add_secret()
        raw = self.raw_db_bytes()
        self.assertGreater(len(raw), 0)
        self.assertNotIn(SECRET.encode(), raw)
        self.assertNotIn(b"A" * 44, raw)

    def test_metadata_present_in_raw_db_file(self):
        """元数据明文是设计的一部分 —— 不加密才能不解密就搜索。"""
        self.init_vault()
        self.add_secret("findable-name", SECRET, "--note", "一条中文备注")
        raw = self.raw_db_bytes().decode("latin-1")
        self.assertIn("findable-name", raw)
        self.assertIn("deepseek", raw)

    def test_doctor_binding_ok_with_real_dpapi(self):
        self.init_vault()
        _, out, _ = self.run_cli(["doctor"])
        self.assertIn("正常", line_with(out, "DPAPI 绑定"))


class TestSubprocess(unittest.TestCase):
    """真子进程：验证 cwd 无关性与 cp936 下的编码。"""

    def run_kv(self, argv, cwd=None):
        return subprocess.run(
            [sys.executable, "-I", str(KV_PY), *argv],
            capture_output=True, cwd=cwd, check=False, timeout=60,
        )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.addCleanup(self._tmp.cleanup)

    def test_version_from_foreign_cwd(self):
        with tempfile.TemporaryDirectory() as other:
            result = self.run_kv(["--version"], cwd=other)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        self.assertIn(b"kv", result.stdout)

    def test_init_and_list_from_foreign_cwd(self):
        """python -I kv.py 必须从任意 cwd 可用 —— shim 负责修 sys.path。"""
        with tempfile.TemporaryDirectory() as other:
            first = self.run_kv(["init", "--vault-dir", str(self.vault)], cwd=other)
            self.assertEqual(first.returncode, 0, first.stderr.decode("utf-8", "replace"))
            second = self.run_kv(
                ["add", "--vault-dir", str(self.vault), "--name", "cwd-test",
                 "--value", "sk-" + "C" * 44, "--platform", "deepseek"],
                cwd=other,
            )
            self.assertEqual(second.returncode, 0, second.stderr.decode("utf-8", "replace"))
            third = self.run_kv(["list", "--vault-dir", str(self.vault)], cwd=other)
        self.assertEqual(third.returncode, 0, third.stderr.decode("utf-8", "replace"))
        self.assertIn(b"cwd-test", third.stdout)

    def test_chinese_output_is_utf8_when_redirected(self):
        """R10：本机控制台 CP 是 936、仓库路径本身是中文。
        重定向到管道时 setup_stdio() 必须已经把输出切成 UTF-8。"""
        self.run_kv(["init", "--vault-dir", str(self.vault)])
        self.run_kv(["add", "--vault-dir", str(self.vault), "--name", "东南大学-密钥",
                     "--value", "sk-" + "D" * 44, "--platform", "deepseek"])
        result = self.run_kv(["list", "--vault-dir", str(self.vault)])
        decoded = result.stdout.decode("utf-8")
        self.assertIn("东南大学", decoded)
        self.assertIn("共 1 条", decoded)
        self.assertNotIn("\ufffd", decoded)

    def test_chinese_path_in_doctor(self):
        self.run_kv(["init", "--vault-dir", str(self.vault)])
        result = self.run_kv(["doctor", "--vault-dir", str(self.vault)])
        decoded = result.stdout.decode("utf-8")
        self.assertIn("DPAPI 绑定", decoded)

    def test_usage_error_exit_code(self):
        result = self.run_kv(["no-such-command"])
        self.assertEqual(result.returncode, 2)

    def test_missing_vault_exit_code(self):
        result = self.run_kv(["list", "--vault-dir", str(self.vault / "absent")])
        self.assertEqual(result.returncode, errors.EXIT_BINDING)


class TestMaskCharConfig(unittest.TestCase):
    """把 mask_char 配成 '•'，验证它在 UTF-8 输出下真的被用上。

    '•' (U+2022) 在 gbk 下不可编码 —— 这正是 R10。setup_stdio() 把输出切成
    UTF-8，所以配置成 '•' 是安全的；而 init_mask_char 的探测保证万一 stdout
    不是 UTF-8，会自动回退成 '*'。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.addCleanup(self._tmp.cleanup)

    def kv(self, *argv):
        return subprocess.run(
            [sys.executable, "-I", str(KV_PY), "--vault-dir", str(self.vault), *argv],
            capture_output=True, check=False, timeout=60,
        )

    def test_default_mask_char_is_asterisk(self):
        self.kv("init")
        self.kv("add", "--name", "b", "--value", "sk-" + "E" * 44 + "c31f",
                "--platform", "deepseek")
        listed = self.kv("list")
        self.assertIn("sk-****c31f", listed.stdout.decode("utf-8"))

    def test_changing_mask_char_takes_effect_without_remask(self):
        """回归：曾经存合成好的掩码串，于是改配置看起来什么都不做。
        现在存的是头尾两段，渲染时才合成 —— 改完立刻生效。"""
        self.kv("init")
        self.kv("add", "--name", "b", "--value", "sk-" + "E" * 44 + "c31f",
                "--platform", "deepseek")
        before = self.kv("list").stdout.decode("utf-8")
        self.assertIn("sk-****c31f", before)

        step = self.kv("config", "--set", "mask_char=#")
        self.assertEqual(step.returncode, 0, step.stderr.decode("utf-8", "replace"))
        after = self.kv("list").stdout.decode("utf-8")
        self.assertIn("sk-####c31f", after)

        self.kv("config", "--set", "mask_char=\u2022")
        bullet = self.kv("list").stdout.decode("utf-8")
        self.assertIn("sk-\u2022\u2022\u2022\u2022c31f", bullet)

    def test_show_and_list_agree_on_the_preview(self):
        self.kv("init")
        self.kv("config", "--set", "mask_char=#")
        self.kv("add", "--name", "b", "--value", "sk-" + "E" * 44 + "c31f",
                "--platform", "deepseek")
        listing = self.kv("list").stdout.decode("utf-8")
        shown = self.kv("show", "b").stdout.decode("utf-8")
        self.assertIn("sk-####c31f", listing)
        self.assertIn("sk-####c31f", shown)


class TestPathsModule(unittest.TestCase):
    def test_gitignore_body_constant_matches_what_init_writes(self):
        self.assertEqual(paths.GITIGNORE_BODY, "*\n!.gitignore\n")


if __name__ == "__main__":
    unittest.main()
