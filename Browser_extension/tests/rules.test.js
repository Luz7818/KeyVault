/** rules.js 规则表测试：tableHash 对拍桌面 + 推导歧义组 + Python 正则换算。 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  RULES, SK_SHAPE, GROUP_OPENAI_COMPATIBLE, GROUP_LABEL, GROUPS,
  SHAPE_COMPILED, SHAPE_GROUPS, KEYNAME_COMPILED,
  tableHash, TABLE_HASH, compilePyRegex, shapeFits, displayName,
  allPlatforms, groupMembers, ambiguousWith, shapeGroupOf, maskStyleFor,
} from "../src/core/rules.js";

// 桌面 python -I kv.py selftest --golden（2026-11-08 实测）输出的检测表前缀，
// 桌面 CLI 以 TABLE_HASH[:16] 展示（kv/commands/detect.py:155）。
const DESKTOP_TABLE_HASH_PREFIX = "41e6c9afb20174f4";

test("规则表指纹与桌面逐字节一致", () => {
  assert.equal(RULES.length, 24);
  assert.equal(tableHash().slice(0, 16), DESKTOP_TABLE_HASH_PREFIX);
  assert.equal(TABLE_HASH, tableHash());
});

test("平台 id 唯一且覆盖关键平台", () => {
  const platforms = allPlatforms();
  assert.equal(new Set(platforms).size, platforms.length);
  for (const expected of [
    "anthropic", "github", "aws-access-key-id", "google", "gitlab", "slack",
    "vercel", "npm", "telegram", "modelscope", "wechat-appid", "stripe",
    "openai-project", "jwt", "pem-private-key", "connection-string", "amap",
    "openai", "deepseek", "siliconflow", "moonshot", "zhipu", "dashscope",
    "openai-compatible",
  ]) {
    assert.ok(platforms.includes(expected), expected);
  }
});

test("歧义组由共享 shape 推导：裸 sk- 六家共用", () => {
  assert.equal(SHAPE_GROUPS.size, 18); // 23 条带形态的规则，其中 6 条共用 SK_SHAPE
  assert.deepEqual(shapeGroupOf(SK_SHAPE), [
    "dashscope", "deepseek", "moonshot", "openai", "siliconflow", "zhipu",
  ]);
});

test("sk-proj- 是 OpenAI 独有形态，绝不进歧义菜单", () => {
  assert.deepEqual(ambiguousWith("openai-project"), []);
  assert.deepEqual(ambiguousWith("anthropic"), []);
  assert.deepEqual(ambiguousWith("openai"), [
    "dashscope", "deepseek", "moonshot", "siliconflow", "zhipu",
  ]);
  assert.deepEqual(ambiguousWith("openai-compatible"), []); // 无形态规则
});

test("group 标签按标签聚合，诚实落点是 openai-compatible", () => {
  assert.equal(GROUP_LABEL.get(GROUP_OPENAI_COMPATIBLE), "openai-compatible");
  assert.deepEqual(groupMembers(GROUP_OPENAI_COMPATIBLE), [
    "dashscope", "deepseek", "moonshot", "openai", "openai-compatible",
    "siliconflow", "zhipu",
  ]);
  assert.equal(GROUPS.get(GROUP_OPENAI_COMPATIBLE).size, 7);
  assert.deepEqual(groupMembers("no-such-group"), []);
});

test("shape_fits：窗口标题层用形态守门，环境证据不能推翻值证据", () => {
  assert.equal(shapeFits("openai", `sk-${"A".repeat(20)}`), true);
  assert.equal(shapeFits("openai-project", `sk-${"A".repeat(20)}`), false);
  assert.equal(shapeFits("openai-compatible", "随便什么"), true); // 无形态规则恒真
  assert.equal(shapeFits("amap", "0123456789abcdef0123456789abcdef"), true);
  assert.equal(shapeFits("amap", "0123456789abcdef0123456789abcde"), false);
});

test("Python 风格原串保留，供 tableHash 对拍", () => {
  assert.ok(RULES.some((rule) => rule.keynames.some((pattern) => pattern.startsWith("(?i)"))));
  assert.ok(RULES.some((rule) => rule.shape?.startsWith("(?s)")));
  const openai = RULES.find((rule) => rule.platform === "openai");
  assert.equal(openai.shape, String.raw`^sk-[A-Za-z0-9_\-]{16,}$`);
});

test("compilePyRegex：(?i) 大小写不敏感生效", () => {
  const gitlab = KEYNAME_COMPILED.some(([pattern]) => pattern.test("gitlab_token"));
  assert.ok(gitlab);
  const githubShape = SHAPE_COMPILED.find(([, rule]) => rule.platform === "github")[0];
  assert.equal(githubShape.test(`GHP_${"a".repeat(36)}`), true);
});

test("compilePyRegex：(?s) 让 . 跨行匹配 PEM 主体", () => {
  const pem = SHAPE_COMPILED.find(([, rule]) => rule.platform === "pem-private-key")[0];
  const block = "-----BEGIN PRIVATE KEY-----\nMIIEvA\n-----END PRIVATE KEY-----\n";
  assert.equal(pem.test(block), true);
});

test("compilePyRegex：$ 匹配串尾换行之前（Python 语义）", () => {
  // Python $ 匹配串尾或串尾换行之前；JS $ 只匹配串尾——换算必须保住这一点。
  const sk = compilePyRegex(String.raw`^sk-[A-Za-z0-9_\-]{16,}$`);
  assert.equal(sk.test(`sk-${"A".repeat(20)}\n`), true);
  assert.equal(sk.test(`sk-${"A".repeat(20)}\n\n`), false); // 只有单个串尾换行
  assert.equal(shapeFits("openai", `sk-${"A".repeat(20)}\n`), true);
});

test("compilePyRegex：转义的 \\$ 不被当作锚", () => {
  const literal = compilePyRegex(String.raw`^\$5\.00$`);
  assert.equal(literal.test("$5.00"), true);
  assert.equal(literal.test("$5.00\n"), true); // 结尾 $ 保留 Python 串尾换行语义
  assert.equal(literal.test("$5.00x"), false);
  assert.equal(literal.test("x$5.00"), false); // \$ 只是字面美元符
});

test("compilePyRegex：未知 Python 旗标响亮报错", () => {
  assert.throws(() => compilePyRegex("(?x)a b"), /旗标/);
});

test("display 与 mask 风格查询", () => {
  assert.equal(displayName("openai-compatible"), "OpenAI 兼容（未指定厂商）");
  assert.equal(displayName("no-such-platform"), "no-such-platform");
  assert.deepEqual(maskStyleFor("github"), [4, 4]);
  assert.deepEqual(maskStyleFor("pem-private-key"), [0, 0]);
  assert.deepEqual(maskStyleFor("no-such-platform"), [0, 4]);
});
