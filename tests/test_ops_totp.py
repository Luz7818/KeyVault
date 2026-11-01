"""TOTP：RFC 6238 官方向量、种子宽容解析、CLI 端到端。

RFC 向量用 8 位口径（SHA1，种子即 ASCII "12345678901234567890"），
与 6 位默认是同一动态截断的两种取模——这是能找到的第三方真值。
"""

from __future__ import annotations

import re
import unittest
from datetime import datetime, timezone

import tests  # noqa: F401
from tests.test_cli_m0 import CliTestCase

from kv import errors
from kv.ops import totp as totpops

RFC_SEED = b"12345678901234567890"
RFC_VECTORS = (  # (unix 秒, RFC 6238 附录 B 的 8 位码)
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
)


def _at(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


class TestTotpGenerate(unittest.TestCase):

    def test_rfc6238_official_vectors(self):
        for seconds, expected in RFC_VECTORS:
            code, _ = totpops.generate(RFC_SEED, _at(seconds), digits=8)
            self.assertEqual(code, expected, f"t={seconds}")

    def test_default_is_six_digits_with_remaining(self):
        code, remaining = totpops.generate(RFC_SEED, _at(59))
        self.assertEqual(code, "287082")  # 同一截断值的 6 位取模
        self.assertEqual(len(code), 6)
        self.assertTrue(1 <= remaining <= totpops.PERIOD)

    def test_counter_rolls_at_period_edge(self):
        first, _ = totpops.generate(RFC_SEED, _at(59))
        second, _ = totpops.generate(RFC_SEED, _at(60))
        self.assertNotEqual(first, second)  # t=59 在第 1 步，t=60 翻到第 2 步


class TestParseSeed(unittest.TestCase):

    def test_accepts_case_space_dash_and_missing_padding(self):
        self.assertEqual(totpops.parse_seed("JBSWY3DPEHPK3PXP"),
                         totpops.parse_seed("jbsw y3dp-EHPK 3pxp"))

    def test_rejects_garbage_and_empty(self):
        for bad in ("hello world!!", "", "   "):
            with self.assertRaises(totpops.SeedError):
                totpops.parse_seed(bad)


class TestTotpCli(CliTestCase):
    """kv add 存种子 → kv totp 出码；TOTP 是对值的解释，不依赖 kind。"""

    def test_totp_end_to_end(self):
        self.init_vault()
        code, out, err = self.run_cli(
            ["add", "--name", "demo", "--value", "jbsw y3dp-EHPK3PXP", "--force"],
        )
        self.assertEqual(code, 0, err)
        code, out, err = self.run_cli(["totp", "demo"])
        self.assertEqual(code, 0, err)
        self.assertIsNotNone(re.search(r"\d{3} \d{3}", out))
        self.assertIn("后刷新", out)

    def test_totp_rejects_non_seed(self):
        self.init_vault()
        code, _out, _err = self.run_cli(
            ["add", "--name", "bad", "--value", "hello world!!", "--force"],
        )
        self.assertEqual(code, 0)
        code, _out, err = self.run_cli(["totp", "bad"])
        self.assertEqual(code, errors.EXIT_USAGE)
        self.assertIn("base32", err)

    def test_totp_on_absent_name_reports_exit_2(self):
        self.init_vault()
        code, _out, err = self.run_cli(["totp", "ghost"])
        self.assertEqual(code, errors.EXIT_NOT_FOUND)
        self.assertIn("ghost", err)


if __name__ == "__main__":
    unittest.main()
