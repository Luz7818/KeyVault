"""占位符闸门与「窄白名单」不变量。

这里守的是现有工程踩过三次的那类坑：一个过宽的「不是凭据」判断会静默放走真密钥，
而输出仍然显示「一切正常」。所以每条断言都成对出现 —— 该拒的拒，**不该拒的必须放行**。
"""

from __future__ import annotations

import unittest

import tests  # noqa: F401

from kv.parse import placeholders
from kv.parse.placeholders import (
    REASON_NOT_A_SECRET,
    REASON_PLACEHOLDER,
    Context,
    classify,
    is_credential_key_name,
    is_non_credential_key,
)

HEX32 = "0123456789abcdef" * 2
SHA1 = "0123456789abcdef" * 2 + "01234567"
REAL_SK = "sk-" + "A" * 44 + "c31f"


def reason(value: str, **ctx) -> str | None:
    got = classify(value, Context(**ctx) if ctx else None)
    return got[0] if got else None


class TestPlaceholdersRejected(unittest.TestCase):
    CASES = [
        "sk-xxxxxxxx", "sk-xxxx", "sk-your-key-here", "sk-test-12345",
        "your-api-key", "your_api_key_here", "YOUR-API-KEY",
        "<你的令牌>", "<api-key-here>",
        "${OPENAI_API_KEY}", "prefix-${OPENAI_API_KEY}-suffix",
        "%MY_API_KEY%", "$OPENAI_API_KEY",
        "change_me", "changeme", "change-me", "replace_me",
        "dummy-value", "fake-key", "sample123456", "placeholder-token",
        "todo-fill-this", "tbd", "insert-here", "insert_here",
        "xxxxxxxxxxxxxxxxxxxx", "0000000000000000", "******",
        "none", "null", "nil", "undefined", "true", "false", "nan",
        "str", "int", "Optional[str]",
        "https://api.example.com/v1", "http://example.org",
        "https://platform.deepseek.com/api_keys",
        "请替换为你的密钥", "请填写", "示例值", "占位符",
    ]

    def test_each_is_rejected_as_placeholder(self):
        for value in self.CASES:
            with self.subTest(value=value):
                self.assertEqual(reason(value), REASON_PLACEHOLDER, value)

    ALLOWED_DETAILS = {
        "sk- 模板", "重复字符填充", "your-key 模板", "change_me 模板",
        "dummy/sample 模板", "insert-here 模板", "中文占位提示", "尖括号占位",
        "shell 变量展开", "含 shell 变量展开", "含 Windows 环境变量展开",
        "Windows 环境变量展开", "shell 变量引用", "字面空值", "类型标注",
        "裸 URL（不含凭据）", "example.com 域名", "只有键名没有值",
    }

    def test_detail_comes_from_a_fixed_vocabulary(self):
        """拒绝原因必须来自固定词表 —— 那样它在结构上就不可能是用户输入的回显。

        这比「断言 detail 不含 value」更强：像 change_me 这种输入，它本身就是
        占位符模式的名字，出现在 detail 里是对的。
        """
        for value in self.CASES:
            with self.subTest(value=value):
                got = classify(value)
                self.assertIsNotNone(got, value)
                self.assertIn(got[1], self.ALLOWED_DETAILS)

    def test_real_secret_reaching_classify_is_accepted_not_described(self):
        self.assertIsNone(classify(REAL_SK))


class TestRealValuesMustPass(unittest.TestCase):
    """反向断言：闸门不许误杀真凭据。每一条都对应一个具体的误杀风险。"""

    def test_real_sk_key_passes(self):
        self.assertIsNone(reason(REAL_SK))

    def test_15_char_token_passes(self):
        """回归：不设长度下限。曾经卡过 15 字符，一把真令牌刚好漏网。"""
        self.assertIsNone(reason("Ab3xK9mQ2pL7vR1"))

    def test_13_char_token_passes(self):
        self.assertIsNone(reason("Ab3xK9mQ2pL"))

    def test_bare_hex32_passes_without_context(self):
        """裸 hex-32 **必须放行** —— 它可能是真高德 Key。

        判它「不是凭据」只发生在可枚举的上下文里。现有工程加过一条
        「看起来像普通标识符」的宽规则，把一把 32 位 hex 的真高德 Key 放走了。
        """
        self.assertIsNone(reason(HEX32))

    def test_connection_string_beats_the_url_placeholder_rule(self):
        """顺序陷阱：user:pass@host 是真凭据，不能被「这是个网址」放过。"""
        value = "postgres://app_user:S3cretPassw0rd@db.internal:5432/app"
        self.assertIsNone(reason(value))
        self.assertIsNone(reason("https://x-access-token:" + "g" * 36 + "@github.com/o/r.git"))

    def test_bare_console_url_is_still_rejected(self):
        """反面对照：不含凭据的 URL 确实该被拒。"""
        self.assertEqual(reason("https://platform.deepseek.com/api_keys"), REASON_PLACEHOLDER)

    def test_value_containing_the_word_key_passes(self):
        self.assertIsNone(reason("keyboard-mouse-1234"))

    def test_sk_with_real_suffix_passes(self):
        self.assertIsNone(reason("sk-proj-" + "A" * 40))
        self.assertIsNone(reason("sk-ant-api03-" + "A" * 40))


class TestNotASecret(unittest.TestCase):
    def test_hash_shapes_always_rejected(self):
        for value in (SHA1, "0123456789abcdef" * 4, "a" * 128):
            with self.subTest(len=len(value)):
                self.assertEqual(reason(value), REASON_NOT_A_SECRET)

    def test_uuid_rejected(self):
        self.assertEqual(
            reason("550e8400-e29b-41d4-a716-446655440000"), REASON_NOT_A_SECRET
        )

    def test_pure_decimal_rejected(self):
        self.assertEqual(reason("1735689600"), REASON_NOT_A_SECRET)

    def test_hex_rejected_only_in_enumerable_contexts(self):
        enumerable = [
            Context(path="repo/.git/objects/ab/cdef1234"),
            Context(path="repo/package-lock.json"),
            Context(path="repo/yarn.lock"),
            Context(path="repo/go.sum"),
            Context(line_text='"resolved": "x", "integrity": "sha512-..."'),
            Context(line_text="checksum: abc"),
        ]
        for ctx in enumerable:
            with self.subTest(ctx=ctx.path or ctx.line_text[:20]):
                self.assertEqual(classify(HEX32, ctx)[0], REASON_NOT_A_SECRET)

        not_enumerable = [
            Context(),
            Context(path="repo/src/config.py"),
            Context(path="repo/.env"),
            Context(line_text=f"AMAP_REST_KEY={HEX32}"),
        ]
        for ctx in not_enumerable:
            with self.subTest(ctx=ctx.path or ctx.line_text[:20]):
                self.assertIsNone(classify(HEX32, ctx), f"{ctx} 不该被判为 not-a-secret")

    def test_windows_backslash_path_is_normalised(self):
        ctx = Context(path=r"D:\repo\.git\objects\ab\cd")
        self.assertTrue(ctx.in_dot_git())
        self.assertEqual(classify(HEX32, ctx)[0], REASON_NOT_A_SECRET)

    def test_empty_and_overlong(self):
        self.assertEqual(classify("   ")[0], "empty")
        self.assertEqual(classify("a" * 20001)[0], "too-long")


class TestKeyNameDenyList(unittest.TestCase):
    """键名过滤必须是拒绝清单。允许清单会误杀任何没被想到的键名。"""

    NON_CREDENTIAL = [
        "MODEL", "LLM_MODEL", "TESTFORGE_MODEL", "TIMEOUT", "AMAP_TIMEOUT",
        "RETRIES", "REGION", "VERSION", "HOST", "PORT", "TRANSPORT",
        "TESTFORGE_TRANSPORT", "LLM_BASE_URL", "SILICONFLOW_BASE_URL",
        "TESTFORGE_API_BASE", "CACHE_DIR", "TESTFORGE_CACHE_DIR",
        "MAX_TOKENS", "TESTFORGE_MAX_TOKENS", "MIN_CALL_INTERVAL_SEC",
        "DEBUG", "LOG_LEVEL", "ENVIRONMENT", "CORS_ORIGINS",
        "DEFAULT_REGION", "ALLOWED_HOSTS",
    ]

    CREDENTIAL = [
        "API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN",
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "SESSION_SECRET",
        "ADMIN_PASSWORD", "VERCEL_TOKEN", "AMAP_SECURITY_CODE",
        "MY_VENDOR_CREDENTIAL", "LLM_API_KEY", "SILICONFLOW_API_KEY",
        "CLIENT_SECRET", "APP_SECRET", "SIGNING_KEY", "ENCRYPTION_KEY",
        "UNKNOWN_THING_2026",
    ]

    def test_non_credential_keys_are_denied(self):
        for key in self.NON_CREDENTIAL:
            with self.subTest(key=key):
                self.assertTrue(is_non_credential_key(key), key)

    def test_credential_keys_are_never_denied(self):
        """这条是那个历史 bug 的回归守卫。AWS_ACCESS_KEY_ID 曾被 `_id$` 误杀。"""
        for key in self.CREDENTIAL:
            with self.subTest(key=key):
                self.assertFalse(is_non_credential_key(key), key)

    def test_deny_list_propagates_into_classify(self):
        self.assertEqual(classify("deepseek-chat", Context(key_name="LLM_MODEL"))[0],
                         REASON_NOT_A_SECRET)
        self.assertIsNone(classify("deepseek-chat", Context(key_name="LLM_API_KEY")))

    def test_credential_hint_is_loose_by_design(self):
        """is_credential_key_name 只用来决定「要不要传播主机名」，判错的代价是
        少传播一次（保守），不是误杀。所以它宁可放过。"""
        self.assertTrue(is_credential_key_name("DEEPSEEK_API_KEY"))
        self.assertTrue(is_credential_key_name("SOMETHING_SECRET"))
        self.assertFalse(is_credential_key_name("LLM_MODEL"))
        self.assertFalse(is_credential_key_name(None))


if __name__ == "__main__":
    unittest.main()
