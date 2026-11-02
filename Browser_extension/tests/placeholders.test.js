// 占位符闸门：22 类占位、哈希形态、键名拒绝清单、连接串豁免顺序。
import { test } from "node:test";
import assert from "node:assert/strict";

import { classify, Context, isNonCredentialKey, isCredentialKeyName } from "../src/core/placeholders.js";

function reason(value, ctx) {
  const [code] = classify(value, ctx);
  return code;
}

test("真凭据放行（各形态一例）", () => {
  for (const value of [
    "sk-abc123_def456ghi789jklmno",                    // 裸 sk-
    "ghp_" + "A".repeat(36),                          // github
    "AKIAIOSFODNN7EXAMPLE",                           // aws id（EXAMPLE 结尾也不误杀）
    "xoxb-123456789-abcdefghij",                      // slack
    "postgres://admin:s3cret@db.example.com/prod",    // 连接串（真凭据豁免，先于 URL 规则）
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.SflKxwRJSM", // JWT
  ]) {
    assert.equal(classify(value), null, `应放行：${value}`);
  }
});

test("sk- 模板与重复字符填充", () => {
  assert.equal(reason("sk-xxx"), "placeholder");
  assert.equal(reason("sk-your-api-key"), "placeholder");
  assert.equal(reason("xxxx"), "placeholder");
  assert.equal(reason("********"), "placeholder");
  assert.equal(reason("00000000"), "placeholder");
});

test("your-key / changeme / dummy / insert-here 模板", () => {
  assert.equal(reason("your_api_key_here"), "placeholder");
  assert.equal(reason("CHANGEME-123"), "placeholder");
  assert.equal(reason("dummy-token-value-1234"), "placeholder");
  assert.equal(reason("insert_here_value_123"), "placeholder");
});

test("中文占位 / 尖括号 / shell 与 Windows 变量展开", () => {
  assert.equal(reason("请替换为你的密钥"), "placeholder");
  assert.equal(reason("<your-api-key>"), "placeholder");
  assert.equal(reason("${OPENAI_API_KEY}"), "placeholder");
  // 嵌在中间也拦
  assert.equal(reason("prefix-${OPENAI_API_KEY}"), "placeholder");
  assert.equal(reason("%API_KEY%1234567890"), "placeholder");
  assert.equal(reason("$API_KEY_TOKEN_1234"), "placeholder");
});

test("字面空值 / 类型标注 / 裸 URL / example.com", () => {
  assert.equal(reason("null"), "placeholder");
  assert.equal(reason("str 1234567890"), "placeholder");  // 类型标注（词边界后接空白）
  assert.equal(classify("string_value_not_real_1234"), null); // string_ 无词边界 → 不误杀
  assert.equal(reason("https://api.example.com/v1"), "placeholder");
  assert.equal(reason("connect https://example.com/x"), "placeholder");
});

test("哈希形态与 UUID/纯数字", () => {
  assert.equal(reason("a".repeat(40)), "not-a-secret");
  assert.equal(reason("b".repeat(64)), "not-a-secret");
  assert.equal(reason("c".repeat(128)), "not-a-secret");
  assert.equal(reason("123e4567-e89b-12d3-a456-426614174000"), "not-a-secret");
  assert.equal(reason("12345678901234567890"), "not-a-secret");
});

test("可枚举上下文的 hex 串（窄白名单）", () => {
  const lock = new Context({ path: "web/package-lock.json" });
  assert.equal(reason("a1b2c3d4e5f60718293a4b5c6d7e8f9a", lock), "not-a-secret");
  const git = new Context({ path: "repo/.git/objects/ab/cdef" });
  assert.equal(reason("a1b2c3d4e5f60718293a4b5c6d7e8f9a", git), "not-a-secret");
  const integrity = new Context({ lineText: "integrity sha256-xyz" });
  assert.equal(reason("a1b2c3d4e5f60718293a4b5c6d7e8f9a", integrity), "not-a-secret");
  // 同样的 hex 在普通上下文放行（可能是真高德 key）
  assert.equal(classify("a1b2c3d4e5f60718293a4b5c6d7e8f9a"), null);
});

test("键名拒绝清单：MODEL/BASE_URL/MAX_TOKENS 拒，AWS_ACCESS_KEY_ID 活", () => {
  assert.ok(isNonCredentialKey("MODEL"));
  assert.ok(isNonCredentialKey("LLM_BASE_URL"));
  assert.ok(isNonCredentialKey("MAX_TOKENS"));
  assert.ok(isNonCredentialKey("request_timeout"));
  assert.equal(isNonCredentialKey("AWS_ACCESS_KEY_ID"), false); // 不排 _id$
  assert.equal(isNonCredentialKey("DEEPSEEK_API_KEY"), false);
  assert.equal(isCredentialKeyName("AWS_ACCESS_KEY_ID"), true);
  assert.equal(isCredentialKeyName("LLM_BASE_URL"), false);
  assert.equal(reason("value-1234567890", new Context({ keyName: "MODEL" })), "not-a-secret");
});

test("空值与超长", () => {
  assert.equal(reason("   "), "empty");
  assert.equal(reason("x".repeat(20001)), "too-long");
});
