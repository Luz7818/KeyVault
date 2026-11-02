// masking + sha256 测试：与桌面版 masking.py 的语义对齐，sha256 与 node:crypto 对拍。
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";

import { sha256Hex } from "../src/core/sha256.js";
import { canonicalize, splitMask, preview, inferMaskStyle, stableStringify } from "../src/core/masking.js";

test("sha256 与 node:crypto 对拍（多组长度/多字节）", () => {
  for (const text of ["", "abc", "sk-abc123_def456ghi789jklmnopqrstu12",
    "中文密钥值测试", "x".repeat(1000), "a".repeat(55), "b".repeat(56), "c".repeat(64)]) {
    const expected = createHash("sha256").update(text, "utf8").digest("hex");
    assert.equal(sha256Hex(text), expected, `sha256 mismatch for len=${text.length}`);
  }
});

test("canonicalize：token 去首尾空白且幂等", () => {
  assert.equal(canonicalize("  sk-abc \n"), "sk-abc");
  assert.equal(canonicalize(canonicalize("  sk-abc \n")), canonicalize("sk-abc"));
});

test("canonicalize：JSON 键排序重序列化（去重正确性的根）", () => {
  const a = '{"b":1,"a":{"d":2,"c":3}}';
  const b = '{"a":{"c":3,"d":2},"b":1}';
  assert.equal(canonicalize(a, "json"), canonicalize(b, "json"));
  assert.equal(canonicalize(a, "json"), '{"a":{"c":3,"d":2},"b":1}');
  // 坏 JSON 退 strip
  assert.equal(canonicalize("  not json ", "json"), "not json");
});

test("canonicalize：PEM 行尾归一", () => {
  const pem = "-----BEGIN KEY-----\r\nabc\r\n-----END KEY-----";
  assert.equal(canonicalize(pem, "pem"),
    "-----BEGIN KEY-----\nabc\n-----END KEY-----\n");
});

test("splitMask 三态：正常 / 短值全掩 / keep 0", () => {
  assert.deepEqual(splitMask("sk-abcdefghijklmnopqrst", 3, 4), ["sk-", "qrst"]);
  assert.deepEqual(splitMask("short", 3, 4), ["", ""]);   // len <= head+tail → 全掩
  assert.deepEqual(splitMask("akid0123456789", 4, 4), ["akid", "6789"]);
  assert.deepEqual(splitMask("somevalue", 0, 0), ["", ""]);
});

test("preview 拼接与 MASK_RUN", () => {
  assert.equal(preview("sk-", "c31f"), "sk-****c31f");
  assert.equal(preview("", "", "•"), "••••");
});

test("inferMaskStyle：保住可识别前缀（sk- → 3,4）", () => {
  assert.deepEqual(inferMaskStyle("sk-abcdef123456"), [3, 4]);
  assert.deepEqual(inferMaskStyle("AKIAJKQ3EJQTLYQPICZA"), [0, 4]);
});

test("stableStringify 与 JSON.stringify(sort keys) 等价", () => {
  assert.equal(stableStringify({ b: 1, a: { d: 2, c: [3, { z: 1, y: 2 }] } }),
    '{"a":{"c":[3,{"y":2,"z":1}],"d":2},"b":1}');
});
