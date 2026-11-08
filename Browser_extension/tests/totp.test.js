// totp.js 单测：RFC 6238 附录 B SHA1 官方向量（8 位口径，桌面
// tests/test_ops_totp.py 同源）+ 6 位默认 + base32 宽容解析等价类。
// 另取桌面 kv/ops/totp.py 实跑值做一次跨实现对拍（种子 JBSWY3DPEHPK3PXP）。

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { parseSeed, generate, SeedError, PERIOD, DIGITS } from "../src/vault/totp.js";

const RFC_SEED = new TextEncoder().encode("12345678901234567890");
// (unix 秒, RFC 6238 附录 B 的 8 位 SHA1 码)
const RFC_VECTORS = [
  [59, "94287082"],
  [1111111109, "07081804"],
  [1111111111, "14050471"],
  [1234567890, "89005924"],
  [2000000000, "69279037"],
  [20000000000, "65353130"],
];

function at(seconds) {
  return new Date(seconds * 1000);
}

describe("totp RFC 6238 官方向量", () => {
  for (const [seconds, expected] of RFC_VECTORS) {
    it(`t=${seconds} → ${expected}（8 位口径）`, async () => {
      const { code } = await generate(RFC_SEED, at(seconds), { digits: 8 });
      assert.equal(code, expected);
    });
  }

  it("6 位默认是同一动态截断的取模（t=59 → 287082）", async () => {
    const { code, remaining } = await generate(RFC_SEED, at(59));
    assert.equal(code, "287082");
    assert.equal(code.length, 6);
    assert.ok(remaining >= 1 && remaining <= PERIOD, `remaining=${remaining} 应在 1..${PERIOD}`);
    assert.equal(remaining, 1); // t=59 落在第 1 步的最后一秒
  });

  it("计数器在周期边界翻转（t=59 与 t=60 不同码）", async () => {
    const first = (await generate(RFC_SEED, at(59))).code;
    const second = (await generate(RFC_SEED, at(60))).code;
    assert.notEqual(first, second);
  });

  it("接受 epoch 毫秒数字作为时钟", async () => {
    const byDate = (await generate(RFC_SEED, at(59))).code;
    const byNumber = (await generate(RFC_SEED, 59_000)).code;
    assert.equal(byDate, byNumber);
  });

  it("PERIOD/DIGITS 契约常量 = 30/6", () => {
    assert.equal(PERIOD, 30);
    assert.equal(DIGITS, 6);
  });
});

describe("totp 与桌面实现对拍（kv/ops/totp.py 实跑值）", () => {
  // 种子 "JBSWY3DPEHPK3PXP" = b"Hello!\xde\xad\xbe\xef"；
  // 桌面 python -I 实跑：t=0 → 282760（remaining 30）、t=59 → 996554、
  // t=1111111109 → 071271。
  const seed = parseSeed("JBSWY3DPEHPK3PXP");
  const CASES = [
    [0, "282760", 30],
    [59, "996554", 1],
    [1111111109, "071271", 1],
  ];
  for (const [seconds, expected, remainingExpected] of CASES) {
    it(`桌面基准 t=${seconds} → ${expected}`, async () => {
      const { code, remaining } = await generate(seed, at(seconds));
      assert.equal(code, expected);
      assert.equal(remaining, remainingExpected);
    });
  }
});

describe("totp base32 宽容解析", () => {
  it("大小写/空格/连字符等价（对齐桌面 parse_seed）", () => {
    const canonical = parseSeed("JBSWY3DPEHPK3PXP");
    assert.deepEqual([...parseSeed("jbsw y3dp-EHPK 3pxp")], [...canonical]);
    assert.deepEqual([...parseSeed("jbswy3dpehpk3pxp")], [...canonical]);
    assert.deepEqual([...parseSeed(" JBSW-Y3DP\tEHPK3PXP ")], [...canonical]);
  });

  it("容忍已有 padding 与无 padding，解出相同字节", () => {
    const canonical = parseSeed("JBSWY3DPEHPK3PXP");
    assert.deepEqual([...parseSeed("JBSWY3DPEHPK3PXP====")], [...canonical]);
    assert.deepEqual([...parseSeed("JBSWY3DP")], [...parseSeed("JBSWY3DP=")]);
  });

  it("解出预期字节（RFC 4648 测试向量 Hello!\\xde\\xad\\xbe\\xef）", () => {
    assert.deepEqual(
      [...parseSeed("JBSWY3DPEHPK3PXP")],
      [...new TextEncoder().encode("Hello!")].concat([0xde, 0xad, 0xbe, 0xef]),
    );
  });

  it("两种写法生成的验证码一致", async () => {
    const a = parseSeed("JBSWY3DPEHPK3PXP");
    const b = parseSeed("jbsw y3dp-EHPK 3pxp");
    assert.equal(
      (await generate(a, at(59))).code,
      (await generate(b, at(59))).code,
    );
  });

  it("拒绝垃圾、空串与纯空白", () => {
    for (const bad of ["hello world!!", "", "   "]) {
      assert.throws(() => parseSeed(bad), SeedError);
    }
  });

  it("拒绝长度非法的尾组（mod 8 ∈ {1,3,6}）与非法字符", () => {
    for (const bad of ["A", "ABC", "ABCDEF", "ABCDEFG0", "ABC1"]) {
      assert.throws(() => parseSeed(bad), SeedError, `应拒绝 ${bad}`);
    }
    // mod 8 = 7 是合法尾组（RFC 4648），不应拒绝
    assert.doesNotThrow(() => parseSeed("ABCDEFG"));
  });

  it("SeedError 带 name=SeedError", () => {
    try {
      parseSeed("!!");
      assert.fail("应抛 SeedError");
    } catch (error) {
      assert.equal(error instanceof SeedError, true);
      assert.equal(error.name, "SeedError");
    }
  });
});
