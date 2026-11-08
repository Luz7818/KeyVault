/** masking.js 与 sha256.js 的独立向量测试（对拍基准：node:crypto）。 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";

import { sha256Hex, sha256Bytes } from "../src/core/sha256.js";
import {
  canonicalize, fingerprint, splitMask, preview, inferMaskStyle,
  stableStringify, MASK_RUN, KIND_TOKEN, KIND_JSON, KIND_PEM,
} from "../src/core/masking.js";

const cryptoHex = (input) => createHash("sha256").update(input).digest("hex");

test("sha256 与 node:crypto 对拍：ASCII 基准向量", () => {
  assert.equal(sha256Hex("abc"), cryptoHex("abc"));
  assert.equal(
    sha256Hex("abc"),
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
  );
});

test("sha256 与 node:crypto 对拍：中文与空字节", () => {
  const vectors = [
    "中文密钥测试——浏览器与桌面同指纹",
    "\u0000abc\u0000",
    "mix\u0000中文\u0000tail",
    "",
  ];
  for (const vector of vectors) {
    assert.equal(sha256Hex(vector), cryptoHex(vector), JSON.stringify(vector));
  }
});

test("sha256 与 node:crypto 对拍：块边界长度扫描", () => {
  // 手写实现最怕填充边界：55/56/63/64/65……逐一与 crypto 对拍。
  for (const length of [0, 1, 55, 56, 57, 63, 64, 65, 127, 128, 1000]) {
    const input = "k".repeat(length);
    assert.equal(sha256Hex(input), cryptoHex(input), `length=${length}`);
  }
});

test("sha256Bytes 与 node:crypto 对拍：全字节域", () => {
  const bytes = Uint8Array.from({ length: 256 }, (_, i) => i);
  assert.equal(sha256Bytes(bytes).length, 8);
  assert.equal(
    sha256Bytes(bytes).reduce((acc, word) => acc + word.toString(16).padStart(8, "0"), ""),
    cryptoHex(Buffer.from(bytes)),
  );
});

test("canonicalize 幂等：token / json / pem", () => {
  const samples = [
    [KIND_TOKEN, "  sk-abc123\r\n\t "],
    [KIND_JSON, '{ "b": 1, "a": "中文" }'],
    [KIND_PEM, "-----BEGIN X-----\r\nMIIE\r\n-----END X-----\n\n"],
  ];
  for (const [kind, value] of samples) {
    const once = canonicalize(value, kind);
    assert.equal(canonicalize(once, kind), once, kind);
  }
});

test("canonicalize：token 去两端空白，pem 规范换行，json 键排序紧凑", () => {
  assert.equal(canonicalize("  sk-abc123\r\n\t "), "sk-abc123");
  assert.equal(
    canonicalize("-----BEGIN X-----\r\nMIIE\r\n-----END X-----\n\n", KIND_PEM),
    "-----BEGIN X-----\nMIIE\n-----END X-----\n",
  );
  const json = canonicalize('{ "b": 1, "a": "中文", "c": [2, 1] }', KIND_JSON);
  assert.equal(json, '{"a":"中文","b":1,"c":[2,1]}');
});

test("canonicalize：非法 JSON 回退为去空白", () => {
  assert.equal(canonicalize("  not-json{ ", KIND_JSON), "not-json{");
});

test("fingerprint：同一逻辑值不同写法同指纹，异值异指纹", () => {
  assert.equal(fingerprint("  sk-abc123\n"), fingerprint("sk-abc123"));
  assert.equal(fingerprint('{ "a": 1, "b": 2 }', KIND_JSON), fingerprint('{"b":2,"a":1}', KIND_JSON));
  assert.notEqual(fingerprint("sk-abc123"), fingerprint("sk-abc124"));
  assert.equal(fingerprint("sk-abc123"), sha256Hex("sk-abc123"));
});

test("split_mask：短值（len<=head+tail）返回两个空串全掩", () => {
  assert.deepEqual(splitMask("short", 3, 4), ["", ""]);
  assert.deepEqual(splitMask("1234567", 3, 4), ["", ""]); // 恰好等于阈值也全掩
  assert.deepEqual(splitMask("", 0, 4), ["", ""]);
});

test("split_mask：长值保头尾，换行转义，keep=0 侧为空", () => {
  assert.deepEqual(splitMask("sk-abcdefgh12345678", 3, 4), ["sk-", "5678"]);
  assert.deepEqual(splitMask("abcdef", 0, 4), ["", "cdef"]);
  assert.deepEqual(splitMask("ab\ncdefgh", 0, 4), ["", "efgh"]); // \n 已转成 \\n 两位再取尾
  assert.deepEqual(splitMask("ab\ncdefghij", 0, 4), ["", "ghij"]);
  assert.deepEqual(splitMask("abcdefgh", 0, 0), ["", ""]);
  assert.deepEqual(splitMask("ab\rcdef", 2, 2), ["ab", "ef"]); // \r 直接删除
});

test("preview：头尾与掩码字符合成，默认 4 连", () => {
  assert.equal(preview("AKIA", "WXYZ"), "AKIA****WXYZ");
  assert.equal(preview("", ""), "****");
  assert.equal(MASK_RUN, 4);
  assert.equal(preview("sk-", "c31f", "#", 6), "sk-######c31f");
});

test("infer_mask_style：保住可识别前缀", () => {
  assert.deepEqual(inferMaskStyle("sk-abcdef1234567890"), [3, 4]);
  assert.deepEqual(inferMaskStyle("ghp_0123456789abcdef0123456789abcdef0123"), [4, 4]);
  assert.deepEqual(inferMaskStyle("AKIA1234567890123456"), [0, 4]);
  assert.deepEqual(inferMaskStyle("0123456789abcdef0123456789abcdef"), [0, 4]);
});

test("stableStringify：键按码点排序、紧凑分隔符、数组保序", () => {
  assert.equal(stableStringify({ b: 1, a: { d: 2, c: [3, 1, 2] } }), '{"a":{"c":[3,1,2],"d":2},"b":1}');
  assert.equal(stableStringify([]), "[]");
  assert.equal(stableStringify({}), "{}");
  assert.equal(stableStringify(null), "null");
  assert.equal(stableStringify(true), "true");
  assert.equal(stableStringify({ k: "中文\"" }), '{"k":"中文\\""}');
});

test("stableStringify 与桌面 json.dumps(sort_keys, separators, ensure_ascii=False) 对拍", () => {
  // 桌面：python -c "import json;print(json.dumps({'b':1,'a':'中文','nested':{'z':0,'y':[1,2]}}, sort_keys=True, separators=(',',':'), ensure_ascii=False))"
  // 输出即下行字面量。
  const value = { b: 1, a: "中文", nested: { z: 0, y: [1, 2] } };
  assert.equal(stableStringify(value), '{"a":"中文","b":1,"nested":{"y":[1,2],"z":0}}');
});
