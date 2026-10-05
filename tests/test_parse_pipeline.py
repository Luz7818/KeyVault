"""parse 流水线：解析器顺序、去重、以及各解析器单独的行为。

顺序是本文件的核心：**结构化解析器必须在 bare 之前**，否则一条 curl 命令会被
当成一串垃圾「裸 key」。generic_scan 只在前面全部落空时才跑 —— 它噪声最大。
"""

from __future__ import annotations

import random
import unittest

import tests  # noqa: F401

from kv.model import Candidate, Rejection
from kv.parse import awscsv, bare, curl, dotenv, jsonblob, pipeline

SK = "sk-" + "A" * 48
GHP = "ghp_" + "B" * 36
HEX32 = "0123456789abcdef" * 2


def values(result):
    return [c.value.decode() for c in result.candidates]


def kinds(result):
    return [c.kind for c in result.candidates]


class TestPipelineOrdering(unittest.TestCase):
    def test_curl_does_not_become_a_garbage_bare_token(self):
        text = f"curl https://api.deepseek.com/v1 -H 'Authorization: Bearer {SK}'"
        result = pipeline.parse(text)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(values(result)[0], SK)
        self.assertNotIn("curl", values(result)[0])

    def test_json_does_not_become_a_bare_token(self):
        text = f'{{"api_key":"{SK}","base_url":"https://api.deepseek.com/v1"}}'
        result = pipeline.parse(text)
        self.assertEqual(values(result), [SK])

    def test_generic_scan_runs_only_when_nothing_else_matched(self):
        """散文里的 key 靠 generic_scan 捞；但结构化解析有产出时它不该再跑一遍。"""
        prose = f"我的令牌是 {GHP} 别告诉别人"
        result = pipeline.parse(prose)
        self.assertEqual(values(result), [GHP])

        structured = f"MY_TOKEN={GHP}"
        result2 = pipeline.parse(structured)
        self.assertEqual(len(result2.candidates), 1)
        self.assertEqual(result2.candidates[0].key_name, "MY_TOKEN")

    def test_multiline_pem_is_found_by_generic_scan(self):
        """PEM 天然跨行 —— 按行找永远匹配不到，反而会把中间的 base64 当裸 token。"""
        text = "-----BEGIN PRIVATE KEY-----\n" + "M" * 200 + "\n-----END PRIVATE KEY-----"
        result = pipeline.parse(text)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(kinds(result), ["pem"])


class TestDedupe(unittest.TestCase):
    def test_same_value_and_key_name_collapses(self):
        text = f"API_KEY={SK}\nAPI_KEY={SK}"
        self.assertEqual(len(pipeline.parse(text).candidates), 1)

    def test_same_value_different_key_names_both_survive(self):
        """那是两条不同的事实（两个环境变量名）。合并成一条记录是 save 层
        靠 UNIQUE(sha256) 做的事，不是 parse 层的事。"""
        text = f"PRIMARY_KEY={SK}\nBACKUP_KEY={SK}"
        self.assertEqual(len(pipeline.parse(text).candidates), 2)

    def test_overlapping_parsers_do_not_double_count(self):
        text = f"curl https://api.deepseek.com/v1 -d '{{\"api_key\":\"{SK}\"}}'"
        result = pipeline.parse(text)
        self.assertEqual(len(values(result)), len(set(values(result))))


class TestCurlParser(unittest.TestCase):
    def test_bearer_header(self):
        result = pipeline.parse(f"curl https://api.openai.com/v1 -H 'Authorization: Bearer {SK}'")
        candidate = result.candidates[0]
        self.assertEqual(candidate.value.decode(), SK)
        self.assertEqual(candidate.hostnames, ("api.openai.com",))
        self.assertEqual(candidate.key_name, "AUTHORIZATION")

    def test_custom_header(self):
        result = pipeline.parse(f"curl https://api.anthropic.com/v1 -H 'x-api-key: {SK}'")
        self.assertEqual(result.candidates[0].key_name, "X_API_KEY")

    def test_non_credential_headers_ignored(self):
        text = "curl https://api.openai.com/v1 -H 'Content-Type: application/json' -H 'Accept: */*'"
        self.assertEqual(pipeline.parse(text).candidates, ())

    def test_backslash_continuation(self):
        text = (f"curl https://api.deepseek.com/v1 \\\n"
                f"  -H 'Content-Type: application/json' \\\n"
                f"  -H 'Authorization: Bearer {SK}'")
        result = pipeline.parse(text)
        self.assertEqual(values(result), [SK])
        self.assertEqual(result.candidates[0].hostnames, ("api.deepseek.com",))

    def test_powershell_backtick_continuation(self):
        text = f'curl https://api.moonshot.cn/v1 `\n  -H "Authorization: Bearer {SK}"'
        self.assertEqual(values(pipeline.parse(text)), [SK])

    def test_u_flag_extracts_the_password(self):
        result = pipeline.parse(f"curl -u deploy:Sup3rS3cretPass https://host.example/")
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].value.decode(), "Sup3rS3cretPass")
        self.assertEqual(result.candidates[0].key_name, "HTTP_BASIC_PASSWORD")

    def test_multiple_hosts_all_collected(self):
        text = (f"curl https://api.deepseek.com/v1 -H 'Authorization: Bearer {SK}' "
                f"--referer https://platform.deepseek.com/")
        hosts = pipeline.parse(text).candidates[0].hostnames
        self.assertIn("api.deepseek.com", hosts)

    def test_unbalanced_quotes_do_not_crash(self):
        text = f"curl https://api.deepseek.com -H 'Authorization: Bearer {SK}"
        result = pipeline.parse(text)
        self.assertIsInstance(result.candidates, tuple)

    def test_looks_like_curl(self):
        self.assertTrue(curl.looks_like_curl("curl https://x"))
        self.assertTrue(curl.looks_like_curl("  CURL -H x"))
        self.assertFalse(curl.looks_like_curl("not a command"))


class TestDotenvParser(unittest.TestCase):
    def test_export_prefix_and_quotes(self):
        text = f'export DEEPSEEK_API_KEY="{SK}"\nexport GITHUB_TOKEN=\'{GHP}\''
        self.assertEqual(sorted(values(pipeline.parse(text))), sorted([SK, GHP]))

    def test_comments_skipped(self):
        text = f"# 注释\nAPI_KEY={SK}\n\n# 另一条注释\n"
        self.assertEqual(values(pipeline.parse(text)), [SK])

    def test_trailing_comment_stripped_from_unquoted_value(self):
        text = f"API_KEY={SK} # 这是注释"
        self.assertEqual(values(pipeline.parse(text)), [SK])

    def test_hash_inside_quotes_survives(self):
        text = f'SLACK_TOKEN="xoxb-1234567890-abcdefghij" # 机器人'
        self.assertEqual(values(pipeline.parse(text)), ["xoxb-1234567890-abcdefghij"])

    def test_crlf(self):
        text = f"DEEPSEEK_API_KEY={SK}\r\nGITHUB_TOKEN={GHP}\r\n"
        self.assertEqual(len(pipeline.parse(text).candidates), 2)

    def test_block_host_goes_into_context_not_direct(self):
        """块级主机名是**弱证据**，必须落进 context_hostnames。

        落进 hostnames 的话它会压过键名，于是一块含 deepseek base_url 的 .env
        会把同块里的 GITLAB_TOKEN 判成 deepseek。
        """
        text = f"LLM_BASE_URL=https://api.deepseek.com/v1\nLLM_API_KEY={SK}\nLLM_MODEL=x-1"
        result = pipeline.parse(text)
        self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        self.assertEqual(candidate.hostnames, ())
        self.assertEqual(candidate.context_hostnames, ("api.deepseek.com",))
        self.assertTrue(candidate.extra.get("hosts_from_block"))

    def test_block_host_propagates_to_every_credential_candidate(self):
        """原来只在「恰好一个凭据候选」时传播，于是最常见的写法反而丢了信号：
        base_url 旁边有别家的 key 时，自家的 LLM_API_KEY 也拿不到主机名。"""
        text = (f"BASE_URL=https://api.deepseek.com/v1\n"
                f"LLM_API_KEY={SK}\nGITLAB_TOKEN={GHP}")
        result = pipeline.parse(text)
        self.assertEqual(len(result.candidates), 2)
        by_key = {c.key_name: c for c in result.candidates}
        self.assertEqual(by_key["LLM_API_KEY"].context_hostnames, ("api.deepseek.com",))
        self.assertEqual(by_key["GITLAB_TOKEN"].context_hostnames, ("api.deepseek.com",))
        # 直接关联的主机名仍然是空的 —— 强度分级靠的就是这个区别
        self.assertEqual(by_key["GITLAB_TOKEN"].hostnames, ())

    def test_curl_host_stays_direct_not_context(self):
        """同一条 curl 命令里的 URL 是强证据：这把 key 就是发给那个 endpoint 的。"""
        text = f"curl https://api.deepseek.com/v1 -H 'Authorization: Bearer {SK}'"
        candidate = pipeline.parse(text).candidates[0]
        self.assertEqual(candidate.hostnames, ("api.deepseek.com",))
        self.assertEqual(candidate.context_hostnames, ())

    def test_non_credential_keys_rejected_with_reason(self):
        text = "LLM_MODEL=deepseek-chat\nLLM_TIMEOUT=30\nAMAP_TIMEOUT=5"
        result = pipeline.parse(text)
        self.assertEqual(result.candidates, ())
        self.assertTrue(all(r.reason == "not-a-secret" for r in result.rejected))

    def test_line_numbers_recorded(self):
        text = f"# c\nA=1\nAPI_KEY={SK}"
        spans = [c.span for c in pipeline.parse(text).candidates]
        self.assertEqual(spans, ["line 3"])

    def test_looks_like_dotenv(self):
        self.assertTrue(dotenv.looks_like_dotenv("A=1\nB=2"))
        self.assertFalse(dotenv.looks_like_dotenv("A=1"))


class TestJsonParser(unittest.TestCase):
    def test_base_url_disambiguates_sibling_key(self):
        text = f'{{"base_url":"https://api.deepseek.com/v1","api_key":"{SK}"}}'
        candidate = pipeline.parse(text).candidates[0]
        self.assertEqual(candidate.value.decode(), SK)
        self.assertEqual(candidate.hostnames, ("api.deepseek.com",))
        self.assertEqual(candidate.key_name, "API_KEY")
        self.assertEqual(candidate.extra.get("model", None), None)

    def test_extra_carries_non_secret_siblings(self):
        text = (f'{{"api_key":"{SK}","base_url":"https://api.siliconflow.cn/v1",'
                f'"model":"deepseek-v3"}}')
        candidate = pipeline.parse(text).candidates[0]
        self.assertEqual(candidate.extra.get("model"), "deepseek-v3")
        self.assertIn("base_url", candidate.extra)
        self.assertNotIn(SK, str(candidate.extra))

    def test_service_account_is_one_json_record(self):
        text = ('{"type":"service_account","project_id":"p","private_key_id":"k",'
                '"private_key":"-----BEGIN PRIVATE KEY-----\\n' + "M" * 64 +
                '\\n-----END PRIVATE KEY-----\\n","client_email":"a@p.iam.gserviceaccount.com",'
                '"token_uri":"https://oauth2.googleapis.com/token"}')
        result = pipeline.parse(text)
        self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        self.assertEqual(candidate.kind, "json")
        self.assertEqual(candidate.extra.get("project_id"), "p")
        self.assertIn("oauth2.googleapis.com", candidate.hostnames)

    def test_service_account_not_split_into_rows(self):
        """拆成 private_key / client_email 多条会把一个凭据碎片化成能各自轮换、
        彼此漂移的行。"""
        text = ('{"type":"service_account","project_id":"p",'
                '"private_key":"-----BEGIN PRIVATE KEY-----\\n' + "M" * 64 +
                '\\n-----END PRIVATE KEY-----\\n","client_email":"a@p.iam.gserviceaccount.com"}')
        self.assertEqual(len(pipeline.parse(text).candidates), 1)

    def test_json_embedded_in_prose(self):
        text = f'配置是 {{"api_key":"{SK}","base_url":"https://api.deepseek.com/v1"}} 存好的'
        result = pipeline.parse(text)
        self.assertEqual(values(result), [SK])

    def test_list_of_objects(self):
        text = (f'[{{"api_key":"{SK}","base_url":"https://api.deepseek.com/v1"}},'
                f'{{"api_key":"{GHP}"}}]')
        result = pipeline.parse(text)
        self.assertEqual(sorted(values(result)), sorted([SK, GHP]))

    def test_non_credential_json_yields_nothing(self):
        self.assertEqual(pipeline.parse('{"model":"x","timeout":30}').candidates, ())

    def test_pretty_printed_json(self):
        text = '{\n  "api_key": "' + SK + '",\n  "base_url": "https://api.deepseek.com/v1"\n}'
        self.assertEqual(values(pipeline.parse(text)), [SK])

    def test_looks_like_json(self):
        self.assertTrue(jsonblob.looks_like_json("{"))
        self.assertTrue(jsonblob.looks_like_json("[1,2]"))
        self.assertFalse(jsonblob.looks_like_json("sk-abc"))


class TestAwsCsv(unittest.TestCase):
    HEADER = "User name,Access key ID,Secret access key\n"

    def test_pair_emitted_with_shared_id(self):
        text = self.HEADER + "deploy,AKIAABCDEFGHIJKLMNOP," + "s" * 40 + "\n"
        result = pipeline.parse(text)
        self.assertEqual(len(result.candidates), 2)
        ids = {c.extra.get("pair_id") for c in result.candidates}
        self.assertEqual(len(ids), 1)
        roles = {c.extra.get("aws_pair_role") for c in result.candidates}
        self.assertEqual(roles, {"secret", "identifier"})

    def test_secret_and_identifier_are_separate_values(self):
        text = self.HEADER + "deploy,AKIAABCDEFGHIJKLMNOP," + "s" * 40 + "\n"
        got = {c.key_name: c.value.decode() for c in pipeline.parse(text).candidates}
        self.assertEqual(got["AWS_SECRET_ACCESS_KEY"], "s" * 40)
        self.assertEqual(got["AWS_ACCESS_KEY_ID"], "AKIAABCDEFGHIJKLMNOP")

    def test_multiple_rows(self):
        text = self.HEADER + "a,AKIAAAAAAAAAAAAAAAAA," + "s" * 40 + "\nb,AKIABBBBBBBBBBBBBBBB," + "t" * 40 + "\n"
        self.assertEqual(len(pipeline.parse(text).candidates), 4)

    def test_non_aws_csv_ignored(self):
        self.assertFalse(awscsv.looks_like_aws_csv("name,value\na,1"))
        self.assertEqual(pipeline.parse("name,value\na,1").candidates, ())


class TestBareParser(unittest.TestCase):
    def test_single_token(self):
        self.assertEqual(values(pipeline.parse(SK)), [SK])

    def test_rejects_text_with_whitespace(self):
        self.assertEqual(list(bare.parse_bare("two words here")), [])

    def test_rejects_json(self):
        self.assertEqual(list(bare.parse_bare('{"a":1}')), [])

    def test_rejects_key_value(self):
        self.assertEqual(list(bare.parse_bare(f"KEY={SK}")), [])

    def test_rejects_non_ascii_prose(self):
        """规则表里每一种凭据形态都是可打印 ASCII —— 这是最窄的散文过滤。"""
        self.assertEqual(list(bare.parse_bare("这是一段说明文字")), [])

    def test_connection_string_kind(self):
        result = pipeline.parse("postgres://u:S3cretPassw0rd@db.internal:5432/app")
        self.assertEqual(kinds(result), ["connstring"])


class TestRejections(unittest.TestCase):
    def test_rejection_never_carries_the_value(self):
        result = pipeline.parse("API_KEY=sk-xxxxxxxx\nTOKEN=${TOKEN}\nNOTE=<占位>")
        self.assertEqual(result.candidates, ())
        self.assertTrue(result.rejected)
        for rejection in result.rejected:
            self.assertIsInstance(rejection, Rejection)
            blob = f"{rejection.span}|{rejection.reason}|{rejection.detail}"
            self.assertNotIn("sk-xxxxxxxx", blob)
            self.assertNotIn("${TOKEN}", blob)

    def test_rejections_are_deduped(self):
        result = pipeline.parse("A=sk-xxxxxxxx\nB=sk-yyyyyyyy\nC=sk-zzzzzzzz")
        reasons = [(r.reason, r.detail) for r in result.rejected]
        self.assertEqual(len(reasons), len(set(r[1] for r in reasons)) or len(reasons))

    def test_span_locates_without_revealing(self):
        result = pipeline.parse(f"OK={GHP}\nBAD=sk-xxxxxxxx")
        spans = [r.span for r in result.rejected]
        self.assertTrue(any("line 2" in s for s in spans))


class TestRobustness(unittest.TestCase):
    """轻量模糊：解析器不许抛异常，也不许把值写进异常消息。"""

    ADVERSARIAL = [
        "", " ", "\n\n\n", "\x00", "\x00" * 1000, "a" * 30000,
        '"unbalanced', "'unbalanced", "{unclosed json", "[1,2,",
        "curl", "curl -H", "curl -H '", "=", "===", "KEY=", "KEY==value",
        "\ufeff" + SK, SK + "\ufeff", "é" * 50, "密钥=值",
        f"nested '{SK}\" double", f'{SK}\n{SK}\n{SK}',
        "-----BEGIN PRIVATE KEY-----", "-----BEGIN", "-----END PRIVATE KEY-----",
        "://", "://@", "a://b:c@", "%" * 50, "${" * 50, "<" * 50,
        "\r" * 100, "\r\n" * 100, "\t\t\t", SK.replace("A", "\n"),
        "x" * 20001, "0" * 20001,
    ]

    def test_no_exception_escapes(self):
        for text in self.ADVERSARIAL:
            with self.subTest(len=len(text), head=text[:12]):
                try:
                    result = pipeline.parse(text)
                except Exception as exc:  # noqa: BLE001 —— 就是要抓任何异常
                    self.fail(f"parse 抛了 {type(exc).__name__}: {exc}")
                self.assertIsInstance(result.candidates, tuple)

    def test_random_bytes_do_not_crash(self):
        rng = random.Random(20261004)
        alphabet = "abcXYZ019-_.,:;=/'\"{}[]<>$%\\|\n\r\t " + "密钥"
        for _ in range(400):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 200)))
            try:
                pipeline.parse(text)
            except Exception as exc:  # noqa: BLE001
                self.fail(f"parse 在随机输入上抛了 {type(exc).__name__}")

    def test_value_never_appears_in_a_rejection(self):
        secret = "sk-" + "Q" * 40
        for text in (f"API_KEY={secret}", secret, f'{{"api_key":"{secret}"}}'):
            result = pipeline.parse(text)
            blob = repr(result.rejected)
            self.assertNotIn("Q" * 20, blob)


if __name__ == "__main__":
    unittest.main()
