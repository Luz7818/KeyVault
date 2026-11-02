// TOTP：RFC 6238 附录 B 官方向量（SHA1/8 位）+ base32 宽容解析 + 6 位默认。
import { test } from "node:test";
import assert from "node:assert/strict";

import { parseSeed, generate, SeedError, PERIOD } from "../src/core/totp.js";

const RFC_SEED = parseSeed("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"); // "12345678901234567890" 的 base32

const VECTORS = [
  [59, "94287082"],
  [1111111109, "07081804"],
  [1111111111, "14050471"],
  [1234567890, "89005924"],
  [2000000000, "69279037"],
  [20000000000, "65353130"],
];

test("RFC 6238 官方向量（8 位口径）", async () => {
  for (const [seconds, expected] of VECTORS) {
    const [code, remaining] = await generate(RFC_SEED, new Date(seconds * 1000), { digits: 8 });
    assert.equal(code, expected, `t=${seconds}`);
    assert.ok(remaining >= 1 && remaining <= PERIOD);
  }
});

test("6 位默认：同一截断值的 6 位取模", async () => {
  const [code] = await generate(RFC_SEED, new Date(59 * 1000));
  assert.equal(code, "287082");
});

test("周期边界翻 counter", async () => {
  const [t59] = await generate(RFC_SEED, new Date(59 * 1000));
  const [t60] = await generate(RFC_SEED, new Date(60 * 1000));
  assert.notEqual(t59, t60);
});

test("base32 宽容解析：大小写/空格/连字符等价；垃圾拒绝", () => {
  assert.deepEqual(parseSeed("jbsw y3dp-EHPK3pxp"), parseSeed("JBSWY3DPEHPK3PXP"));
  assert.throws(() => parseSeed("hello world!!"), SeedError);
  assert.throws(() => parseSeed("   "), SeedError);
});
