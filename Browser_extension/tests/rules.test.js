// 规则表完整性：24 条、歧义推导、shape_fits 的实测语义。
import { test } from "node:test";
import assert from "node:assert/strict";

import * as rules from "../src/core/rules.js";

test("规则表共 24 条，platform id 无重复", () => {
  assert.equal(rules.RULES.length, 24);
  assert.equal(new Set(rules.allPlatforms()).size, 24);
});

test("SK 形态歧义组：六家共享裸 sk-，openai-project 不在组里", () => {
  const members = [...rules.SHAPE_GROUPS.get(rules.SK_SHAPE)].sort();
  assert.deepEqual(members, ["dashscope", "deepseek", "moonshot", "openai",
    "siliconflow", "zhipu"]);
  assert.equal(rules.BY_PLATFORM.get("openai-project").shape[0].startsWith("^sk-proj-"), true);
  assert.deepEqual(rules.ambiguousWith("openai"), [
    "dashscope", "deepseek", "moonshot", "siliconflow", "zhipu"]);
  assert.deepEqual(rules.ambiguousWith("openai-project"), []); // 形态独有
});

test("GROUP_LABEL：openai_compatible 的诚实落点是 openai-compatible 标签规则", () => {
  assert.equal(rules.GROUP_LABEL.get(rules.GROUP_OPENAI_COMPATIBLE), "openai-compatible");
  assert.deepEqual(rules.groupMembers(rules.GROUP_OPENAI_COMPATIBLE).slice().sort(), [
    "dashscope", "deepseek", "moonshot", "openai", "openai-compatible",
    "siliconflow", "zhipu"].sort());
});

test("shape_fits：窗口证据不能推翻值证据（sk-proj 误判回归）", () => {
  const bareSk = "sk-abc123def456ghi789";
  assert.equal(rules.shapeFits("openai", bareSk), true);        // SK_SHAPE 命中
  assert.equal(rules.shapeFits("openai-project", bareSk), false); // sk-proj- 形态不符
  // 单条命中不接受形态否决：Vercel token 不带 vercel_ 前缀也必须放行
  assert.equal(rules.shapeFits("github", "ghp_" + "a".repeat(36)), true);
});

test("HOST_INDEX 小写索引与 display/maskStyle 查询", () => {
  assert.ok(rules.HOST_INDEX.get("api.deepseek.com")[0].platform === "deepseek");
  assert.equal(rules.displayName("deepseek"), "DeepSeek");
  assert.equal(rules.displayName("no-such"), "no-such");
  assert.deepEqual(rules.maskStyleFor("anthropic"), [8, 4]);
  assert.deepEqual(rules.maskStyleFor("no-such"), [0, 4]);
});
