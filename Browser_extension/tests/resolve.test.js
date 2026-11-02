// 分级判定：七级优先级 + 歧义/needs_context/窗口形态收窄的实测语义。
import { test } from "node:test";
import assert from "node:assert/strict";

import { resolve, describe } from "../src/core/resolve.js";
import { CorrectionSet } from "../src/core/corrections.js";
import { Candidate } from "../src/core/candidate.js";

function cand(value, { keyName = null, hostnames = [], contextHostnames = [] } = {}) {
  return new Candidate({ value, keyName, hostnames, contextHostnames, source: "extension" });
}

test("第 1 级 hostname：直接关联 → exact", () => {
  const v = resolve(cand("sk-abc123_def456ghi789jklmno", { hostnames: ["api.deepseek.com"] }));
  assert.equal(v.platform, "deepseek");
  assert.equal(v.confidence, "exact");
});

test("第 2 级 keyname：键名 → high", () => {
  const v = resolve(cand("sk-abc123_def456ghi789jklmno", { keyName: "DEEPSEEK_API_KEY" }));
  assert.equal(v.platform, "deepseek");
  assert.equal(v.confidence, "high");
});

test("第 3 级 shape：独有形态 → medium（glpat/gitlab、AKIA/aws）", () => {
  const v = resolve(cand("glpat-abcdefghijklmnopqrst"));
  assert.equal(v.platform, "gitlab");
  assert.equal(v.confidence, "medium");
  const aws = resolve(cand("AKIAIOSFODNN7EXAMPLE"));
  assert.equal(aws.platform, "aws-access-key-id");
});

test("裸 sk- 歧义：诚实落 openai-compatible 并列出候选，绝不猜厂商", () => {
  const v = resolve(cand("sk-abc123_def456ghi789jklmno"));
  assert.equal(v.platform, "openai-compatible");
  assert.equal(v.confidence, "ambiguous");
  assert.ok(v.candidates.includes("deepseek") && v.candidates.includes("openai"));
  assert.ok(describe(v).includes("候选"));
});

test("sk-proj- 形态独有：直接判 openai-project，不进歧义组", () => {
  const v = resolve(cand("sk-proj-abcdefghijklmnopqrst"));
  assert.equal(v.platform, "openai-project");
  assert.equal(v.confidence, "medium");
});

test("needs_context：裸 hex-32 拒绝猜测 → none", () => {
  const v = resolve(cand("a1b2c3d4e5f60718293a4b5c6d7e8f9a"));
  assert.equal(v.platform, "unknown");
  assert.equal(v.confidence, "none");
  // 有键名佐证则可判
  const withKey = resolve(cand("a1b2c3d4e5f60718293a4b5c6d7e8f9a", { keyName: "AMAP_KEY" }));
  assert.equal(withKey.platform, "amap");
});

test("第 4 级 context hostname：救回同块裸 sk-，但不改判已定的 GITLAB_TOKEN", () => {
  const saved = resolve(cand("sk-abc123_def456ghi789jklmno",
    { contextHostnames: ["api.deepseek.com"] }));
  assert.equal(saved.platform, "deepseek");
  assert.equal(saved.confidence, "high");

  const untouched = resolve(cand("glpat-abcdefghijklmnopqrst",
    { contextHostnames: ["api.deepseek.com"] }));
  assert.equal(untouched.platform, "gitlab"); // 第 3 级已定，context 不推翻
});

test("第 5 级 window/页面标题：救回歧义 sk-；形态不符不收窄", () => {
  const deepseek = resolve(cand("sk-abc123_def456ghi789jklmno"),
    null, { windowTitle: "DeepSeek Platform - API Keys" });
  assert.equal(deepseek.platform, "deepseek");
  assert.equal(deepseek.confidence, "high");

  const openai = resolve(cand("sk-abc123_def456ghi789jklmno"),
    null, { windowTitle: "OpenAI API keys - ChatGPT" });
  assert.equal(openai.platform, "openai");

  // 标题写着 OpenAI 也救不了 ghp_（第 3 级已判 github）——值证据不被环境推翻
  const github = resolve(cand("ghp_" + "a".repeat(36)),
    null, { windowTitle: "OpenAI API keys" });
  assert.equal(github.platform, "github");

  // 标题命中 openai-project 关键词但形态不符 → 不收窄，仍走标题可判项
  const proj = resolve(cand("sk-abc123_def456ghi789jklmno"),
    null, { windowTitle: "platform.openai.com project keys" });
  assert.equal(proj.platform, "openai"); // openai-project 形态不符被 shape_fits 排除
});

test("第 0 级用户纠正：覆盖一切（exact/hostname/keyname/组默认）", () => {
  const sha = "a".repeat(64);
  const exactCorr = CorrectionSet.fromRows([
    { kind: "exact", pattern: sha, platform: "zhipu", createdAt: "2026-11-02T00:00:00" },
  ]);
  // sha256 必须与候选真实指纹一致：用同一候选对象验证
  const target = cand("sk-abc123_def456ghi789jklmno");
  const corr2 = CorrectionSet.fromRows([
    { kind: "exact", pattern: target.sha256(), platform: "zhipu", createdAt: "2026-11-02" },
  ]);
  const v = resolve(target, corr2);
  assert.equal(v.platform, "zhipu");
  assert.equal(v.confidence, "manual");

  const group = CorrectionSet.fromRows([
    { kind: "group", pattern: "openai_compatible", platform: "deepseek" },
  ]);
  const grouped = resolve(cand("sk-abc123_def456ghi789jklmno"), group);
  assert.equal(grouped.platform, "deepseek");
  assert.equal(grouped.confidence, "manual");

  // 无关 sha 的 exact 纠正不影响别人
  const unrelated = resolve(cand("sk-abc123_def456ghi789jklmno"), exactCorr);
  assert.equal(unrelated.platform, "openai-compatible");
});

test("全部未命中 → unknown/none", () => {
  const v = resolve(cand("plainvalue1234567890"));
  assert.equal(v.platform, "unknown");
  assert.equal(v.confidence, "none");
});

test("hostname 后缀匹配：api.deepseek.com 也命中 googleapis 类宽域规则需 shape 收窄", () => {
  // googleapis.com 宽域 + AIza 形态 → google（形态收窄掉宽域的其他可能）
  const v = resolve(cand("AIza" + "C".repeat(35), { hostnames: ["googleapis.com"] }));
  assert.equal(v.platform, "google");
});
