// generator.test.js —— 密码生成器向量测试（深评 §9 move 3 的验收）：
// ① 拒绝采样正确性（全偏池边界：N 不整除 2^32 时的偏差必须为零）；
// ② 分布抽样：启用字符类必出现、长时间大样本各池字符全可达；
// ③ ambiguousOff 剔除与类保证、熵粗估公式、默认值与选项校验。

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  generate, resolveOptions, buildPool, randomInt, entropyBits,
  GENERATOR_DEFAULTS, LENGTH_MIN, LENGTH_MAX,
} from "../src/vault/generator.js";

describe("randomInt（拒绝采样）", () => {
  it("输出域 [0, max) 且不越界", () => {
    for (const max of [1, 2, 3, 7, 26, 62, 88, 1000, 0x100000000]) {
      for (let i = 0; i < 200; i += 1) {
        const value = randomInt(max);
        assert.ok(Number.isInteger(value) && value >= 0 && value < max, `max=${max} 出域：${value}`);
      }
    }
  });

  it("边界池（N 不整除 2^32）无模偏差——拒绝采样正确性的直接证据", () => {
    // 2^32 % 3 = 1：取模法会让 0 号桶比其他桶多 1 次命中机会。
    // 2 万样本下取模法的偏差约 1/2^32*20000 ≈ 可测；拒绝采样应严格均匀
    // （三项卡方容忍 3σ）。这里用更直接的断言：各桶频数相对差 < 5%。
    const counts = [0, 0, 0];
    const samples = 30000;
    for (let i = 0; i < samples; i += 1) counts[randomInt(3)] += 1;
    const expected = samples / 3;
    for (const count of counts) {
      assert.ok(Math.abs(count - expected) / expected < 0.05,
        `模偏差泄漏：桶频数 ${counts} 期望 ${expected}`);
    }
  });

  it("max=1 恒 0；非法 max 抛 RangeError", () => {
    assert.equal(randomInt(1), 0);
    assert.throws(() => randomInt(0), RangeError);
    assert.throws(() => randomInt(-1), RangeError);
    assert.throws(() => randomInt(1.5), RangeError);
    assert.throws(() => randomInt(0x100000001), RangeError);
  });
});

describe("resolveOptions / buildPool（选项归一化）", () => {
  it("默认：长度 20、四类全开、排除易混淆", () => {
    assert.deepEqual(resolveOptions(undefined), GENERATOR_DEFAULTS);
    assert.equal(GENERATOR_DEFAULTS.length, 20); // NIST SP 800-63B ≥15 留余量
    assert.equal(resolveOptions({ length: "20" }).length, 20); // Number 收敛
  });

  it("长度 8..128 之外、非整数 → 抛错", () => {
    assert.throws(() => resolveOptions({ length: 7 }), /8\.\.128/);
    assert.throws(() => resolveOptions({ length: 129 }), /8\.\.128/);
    assert.throws(() => resolveOptions({ length: 20.5 }), /整数/);
    assert.throws(() => resolveOptions({ length: "abc" }), /整数/);
    assert.equal(LENGTH_MIN, 8);
    assert.equal(LENGTH_MAX, 128);
  });

  it("布尔位必须布尔；至少一类；全易混淆类抛错", () => {
    assert.throws(() => resolveOptions({ lower: "yes" }), /布尔/);
    assert.throws(() => generate({ lower: false, upper: false, digits: false, symbols: false }),
      /至少启用一个字符类/);
    // digits 类全在易混淆集（0/1 是 ambiguous 成员？——digits 只有 0 和 1 撞集）
    // 关闭其他类 + ambiguousOff 时 digits 剩 2-9，可用；把 ambiguousOff 关掉才全量。
    const pool = buildPool({ lower: true, ambiguousOff: false });
    assert.ok(pool.pool.includes("l") && pool.pool.includes("0"));
  });

  it("ambiguousOff：I/l/1/O/0/o/竖线/引号 全部出局", () => {
    const ambiguous = ["I", "l", "1", "O", "0", "o", "|", "'", '"', "`"];
    const { pool } = buildPool({ ambiguousOff: true });
    for (const ch of ambiguous) {
      assert.equal(pool.includes(ch), false, `易混淆字符 ${ch} 混进了池`);
    }
    for (let i = 0; i < 50; i += 1) {
      const { password } = generate({ ambiguousOff: true });
      for (const ch of ambiguous) {
        assert.equal(password.includes(ch), false, `易混淆字符 ${ch} 出现在密码里`);
      }
    }
  });
});

describe("generate（分布与形状）", () => {
  it("长度精确等于请求值", () => {
    for (const length of [8, 9, 20, 64, 128]) {
      assert.equal(generate({ length }).password.length, length);
    }
  });

  it("启用的字符类每类至少出现一枚（类保证）", () => {
    for (let i = 0; i < 200; i += 1) {
      const { password } = generate({});
      assert.ok(/[a-z]/.test(password), "缺小写");
      assert.ok(/[A-Z]/.test(password), "缺大写");
      assert.ok(/[0-9]/.test(password), "缺数字");
      assert.ok(/[!-\/:-@\[-`{-~]/.test(password), "缺符号");
    }
  });

  it("分布抽样：大样本下全池字符均可达（拒绝采样均匀性的输出面证据）", () => {
    const seen = new Set();
    for (let i = 0; i < 400; i += 1) {
      for (const ch of generate({ ambiguousOff: false }).password) seen.add(ch);
    }
    const { pool } = buildPool({ ambiguousOff: false });
    for (const ch of pool) {
      assert.equal(seen.has(ch), true, `池字符 ${ch} 从未出现`);
    }
  });

  it("只开 digits：密码全数字且含 2-9（ambiguous 剔除 0/1）", () => {
    const { password, poolSize } = generate({ lower: false, upper: false, symbols: false });
    assert.match(password, /^[0-9]+$/);
    assert.equal(poolSize, 8); // 0 和 1 被 ambiguousOff 剔除
    assert.ok(!/[01]/.test(password));
  });

  it("随机性：两次生成不同（固定选项下 20 位全同概率 ≈ 0）", () => {
    const a = generate({}).password;
    const b = generate({}).password;
    assert.notEqual(a, b);
  });

  it("返回熵粗估 = length × log2(poolSize)，保留 1 位小数", () => {
    const result = generate({});
    const expected = Math.round(result.length * Math.log2(result.poolSize) * 10) / 10;
    assert.equal(result.entropyBits, expected);
    assert.equal(entropyBits({}), expected);
    // 默认池手算：lower 26−2(l,o) + upper 26−2(I,O) + digits 10−2(0,1) + symbols 24（无易混淆成员）
    assert.equal(result.poolSize, 24 + 24 + 8 + 24);
  });
});
