"""DPAPI 绑定的 Windows 集成测试。

覆盖计划 M0/M5 要求的：往返、非 ASCII、空值、15 字节值（现有系统曾因长度下限
漏掉一把 15 字符的真令牌）、错 entropy → 码 13、LOCAL_MACHINE 变体、self_test、
unprotect_into 的归零行为。
"""

from __future__ import annotations

import sys
import unittest

import tests  # noqa: F401  —— 触发 sys.path 注入

from kv.crypto import dpapi
from kv.errors import DpapiError

WINDOWS = sys.platform == "win32"


@unittest.skipUnless(WINDOWS, "DPAPI 只在 Windows 上可用")
class TestDpapiRoundTrip(unittest.TestCase):
    def assert_round_trip(self, payload: bytes) -> None:
        blob = dpapi.protect(payload)
        self.assertIsInstance(blob, bytes)
        if payload:
            self.assertNotIn(payload, blob, "明文不得出现在密文里")
        self.assertEqual(dpapi.unprotect(blob), payload)

    def test_ascii_token(self):
        self.assert_round_trip(b"sk-ant-api03-" + b"A" * 48)

    def test_15_byte_value_is_accepted(self):
        """回归：不设长度下限。一把 15 字符的真 token 必须能存。"""
        self.assert_round_trip(b"Ab3xK9mQ2pL7vR1")

    def test_non_ascii(self):
        self.assert_round_trip("密钥-值-测试-东南大学".encode("utf-8"))

    def test_empty_value(self):
        self.assert_round_trip(b"")

    def test_binary_pem_like(self):
        self.assert_round_trip(b"-----BEGIN PRIVATE KEY-----\nMIIEvA\n-----END PRIVATE KEY-----\n")

    def test_blob_overhead_is_bounded(self):
        payload = b"x" * 64
        blob = dpapi.protect(payload)
        self.assertLess(len(blob) - len(payload), 512, "DPAPI 头不该异常膨胀")

    def test_two_encryptions_of_same_value_differ(self):
        """DPAPI 带随机盐，同一个值两次加密结果不同 —— 所以去重必须靠 sha256 列，
        不能靠比对密文。"""
        payload = b"sk-dedupe-check-value"
        self.assertNotEqual(dpapi.protect(payload), dpapi.protect(payload))


@unittest.skipUnless(WINDOWS, "DPAPI 只在 Windows 上可用")
class TestDpapiFailures(unittest.TestCase):
    def test_wrong_entropy_gives_code_13(self):
        blob = dpapi.protect(b"secret-value", entropy=b"keyvault.v1")
        with self.assertRaises(DpapiError) as ctx:
            dpapi.unprotect(blob, entropy=b"some.other.app")
        self.assertEqual(ctx.exception.code, 13)

    def test_corrupt_blob_raises_not_crashes(self):
        blob = bytearray(dpapi.protect(b"secret-value"))
        blob[-1] ^= 0xFF
        with self.assertRaises(DpapiError):
            dpapi.unprotect(bytes(blob))

    def test_truncated_blob_raises_not_crashes(self):
        blob = dpapi.protect(b"secret-value")
        with self.assertRaises(DpapiError):
            dpapi.unprotect(blob[:8])

    def test_describe_failure_translates_code_13(self):
        text = dpapi.describe_failure(13)
        self.assertIn("另一个 Windows 账户", text)
        self.assertIn("13", text)

    def test_error_message_never_contains_the_value(self):
        payload = b"sk-TOPSECRETVALUE1234567890"
        blob = bytearray(dpapi.protect(payload))
        blob[-1] ^= 0xFF
        with self.assertRaises(DpapiError) as ctx:
            dpapi.unprotect(bytes(blob))
        rendered = str(ctx.exception)
        self.assertNotIn("TOPSECRETVALUE", rendered)
        self.assertNotIn(payload.decode(), rendered)

    def test_protect_rejects_str(self):
        with self.assertRaises(DpapiError):
            dpapi.protect("not-bytes")  # type: ignore[arg-type]


@unittest.skipUnless(WINDOWS, "DPAPI 只在 Windows 上可用")
class TestDpapiFlags(unittest.TestCase):
    def test_local_machine_round_trip(self):
        blob = dpapi.protect(b"machine-scope", local_machine=True)
        self.assertEqual(dpapi.unprotect(blob), b"machine-scope")

    def test_local_machine_blob_differs_from_user_blob(self):
        """同一个值在机器级与用户级下产出不同 blob —— 两者不通用。"""
        blob = dpapi.protect(b"machine-scope", local_machine=True)
        user_blob = dpapi.protect(b"machine-scope", local_machine=False)
        self.assertNotEqual(blob, user_blob)
        self.assertEqual(dpapi.unprotect(blob), b"machine-scope")

    def test_self_test_returns_canary(self):
        self.assertEqual(dpapi.self_test(), dpapi.CANARY_TEXT)

    def test_self_test_fails_with_mismatched_entropy(self):
        blob = dpapi.protect(dpapi.CANARY_TEXT.encode(), entropy=b"keyvault.v1")
        with self.assertRaises(DpapiError):
            dpapi.unprotect(blob, entropy=b"keyvault.v2")


@unittest.skipUnless(WINDOWS, "DPAPI 只在 Windows 上可用")
class TestUnprotectInto(unittest.TestCase):
    def test_writes_into_caller_buffer(self):
        payload = b"sk-unprotect-into-target"
        blob = dpapi.protect(payload)
        out = bytearray()
        dpapi.unprotect_into(blob, out)
        self.assertEqual(bytes(out), payload)
        self.assertIsInstance(out, bytearray)

    def test_reuses_and_shrinks_existing_buffer(self):
        blob = dpapi.protect(b"short")
        out = bytearray(b"a-very-long-previous-content")
        dpapi.unprotect_into(blob, out)
        self.assertEqual(bytes(out), b"short")

    def test_zeroing_clears_the_buffer(self):
        """调用方的归零惯用法必须真的把内容擦掉 —— 这是 reveal() 上下文管理器的契约。"""
        payload = b"sk-ZEROME-ZEROME-ZEROME"
        blob = dpapi.protect(payload)
        out = bytearray()
        dpapi.unprotect_into(blob, out)
        self.assertIn(b"ZEROME", bytes(out))
        for i in range(len(out)):
            out[i] = 0
        out.clear()
        self.assertEqual(len(out), 0)
        self.assertNotIn(b"ZEROME", bytes(out))


@unittest.skipUnless(WINDOWS, "DPAPI 只在 Windows 上可用")
class TestProtectorImplementation(unittest.TestCase):
    def test_dpapi_protector_satisfies_round_trip(self):
        protector = dpapi.DpapiProtector()
        blob = protector.protect(b"via-protector")
        self.assertEqual(protector.unprotect(blob), b"via-protector")
        out = bytearray()
        protector.unprotect_into(blob, out)
        self.assertEqual(bytes(out), b"via-protector")


@unittest.skipIf(WINDOWS, "只在非 Windows 上验证惰性绑定")
class TestNonWindows(unittest.TestCase):
    def test_import_does_not_crash(self):
        self.assertTrue(hasattr(dpapi, "protect"))

    def test_use_raises_dpapi_error(self):
        with self.assertRaises(DpapiError):
            dpapi.protect(b"x")


if __name__ == "__main__":
    unittest.main()
