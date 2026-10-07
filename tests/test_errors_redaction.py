"""脱敏断言：异常消息不得含密钥值。

这是让「绝不打印机密」在**失败时**也成立的那个测试 —— 失败路径恰恰是最容易
把值拼进消息的时候。判据：str(exc) 与产生它的输入不共享任何 >=8 字符子串。
"""

from __future__ import annotations

import unittest

import tests  # noqa: F401

from kv import errors

SECRET = "sk-TOPSECRETVALUE-abcdefghij-0123456789"
SECRET_B = SECRET.encode()


def shared_substrings(left: str, right: str, min_len: int = 8) -> list[str]:
    found = []
    for size in range(min_len, min(len(left), len(right)) + 1):
        for start in range(0, len(left) - size + 1):
            piece = left[start : start + size]
            if piece in right:
                found.append(piece)
    return sorted(set(found), key=len, reverse=True)


class TestDescriptorNeverLeaks(unittest.TestCase):
    def test_descriptor_excludes_the_value(self):
        text = errors.descriptor(SECRET_B)
        self.assertEqual(shared_substrings(text, SECRET), [])
        self.assertIn("len=", text)
        self.assertIn("sha256=", text)

    def test_descriptor_includes_length(self):
        self.assertIn(f"len={len(SECRET_B)}", errors.descriptor(SECRET_B))

    def test_sha256_prefix_is_12_hex_by_default(self):
        prefix = errors.sha256_prefix(SECRET_B)
        self.assertEqual(len(prefix), 12)
        int(prefix, 16)

    def test_sha256_prefix_custom_width(self):
        self.assertEqual(len(errors.sha256_prefix(SECRET_B, 8)), 8)


class TestErrorMessagesNeverLeak(unittest.TestCase):
    """每条异常路径都过一遍。新增异常类型时往这个表里加一行。"""

    CASES = [
        ("KvError", lambda: errors.KvError(f"处理失败：{errors.descriptor(SECRET_B)}")),
        ("UsageError", lambda: errors.UsageError(f"--value 需要一个值，收到 {errors.descriptor(SECRET_B)}")),
        ("NotFoundError", lambda: errors.NotFoundError("没有叫这个名字的记录")),
        ("AlreadyExistsError", lambda: errors.AlreadyExistsError("这个名字已被占用")),
        ("LeaksFoundError", lambda: errors.LeaksFoundError("扫出 3 条泄漏")),
        ("DpapiError", lambda: errors.DpapiError(f"解密失败（{errors.descriptor(SECRET_B)}）", code=13)),
        ("BindingError", lambda: errors.BindingError("vault 来自另一个 Windows 账户")),
        ("VaultNotInitializedError", lambda: errors.VaultNotInitializedError("C:/x/vault.db")),
        ("GateError", lambda: errors.GateError("检测表在上次 golden 之后变过")),
        ("RefusedError", lambda: errors.RefusedError(f"识别结果不可信（{errors.descriptor(SECRET_B)}）")),
        ("BackupError", lambda: errors.BackupError("不是有效的 KeyVault 备份文件（.kvb）")),
        ("BackupAuthError", lambda: errors.BackupAuthError("口令错误，或备份文件已被篡改")),
    ]

    def test_no_error_message_shares_8_chars_with_the_secret(self):
        for label, factory in self.CASES:
            with self.subTest(error=label):
                rendered = str(factory())
                self.assertEqual(
                    shared_substrings(rendered, SECRET),
                    [],
                    f"{label} 的消息泄漏了密钥片段：{rendered}",
                )

    def test_all_cases_covered(self):
        """防止有人加了异常类却忘了加测试用例。"""
        subclasses = {
            name
            for name in dir(errors)
            if isinstance(getattr(errors, name), type)
            and issubclass(getattr(errors, name), errors.KvError)
            and name != "KvError"
        }
        covered = {label for label, _ in self.CASES}
        self.assertEqual(subclasses - covered, set(), "有异常类没被脱敏测试覆盖")


class TestExitCodes(unittest.TestCase):
    def test_exit_code_table(self):
        expected = {
            errors.KvError: 1,
            errors.UsageError: 1,
            errors.NotFoundError: 2,
            errors.AlreadyExistsError: 1,
            errors.LeaksFoundError: 3,
            errors.DpapiError: 4,
            errors.BindingError: 4,
            errors.VaultNotInitializedError: 4,
            errors.GateError: 5,
            errors.RefusedError: 1,
        }
        for cls, code in expected.items():
            with self.subTest(error=cls.__name__):
                self.assertEqual(cls.exit_code, code)

    def test_code_appears_in_str_when_set(self):
        self.assertIn("13", str(errors.DpapiError("解密失败", code=13)))

    def test_code_absent_from_str_when_unset(self):
        self.assertNotIn("系统错误码", str(errors.UsageError("用法错误")))


if __name__ == "__main__":
    unittest.main()
