/** resolve.js 八级判定测试：分级顺序、用户纠正、歧义诚实落点、拒绝落盘。 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { parse } from "../src/core/parse.js";
import { Candidate } from "../src/core/candidate.js";
import {
  resolve, describe, isTrusted, Verdict,
  CONFIDENCE_MANUAL, CONFIDENCE_EXACT, CONFIDENCE_HIGH,
  CONFIDENCE_MEDIUM, CONFIDENCE_AMBIGUOUS, CONFIDENCE_NONE,
} from "../src/core/resolve.js";
import {
  CorrectionSet, suggestCorrectionScope, groupCorrection,
} from "../src/core/corrections.js";

const SK48 = `sk-${"A".repeat(48)}`;
const HEX32 = "0123456789abcdef0123456789abcdef";
const ALNUM36 = "abcdefghij0123456789abcdefghij012345678";

const mkCand = (value, opts = {}) => new Candidate({ value, ...opts });

const resolveText = (text, { corrections = null, windowTitle = null } = {}) => {
  const { candidates } = parse(text, { sourceHint: "golden" });
  assert.ok(candidates.length > 0, "前置：语料应产出候选");
  return resolve(candidates[0], corrections, { windowTitle });
};

const rows = (specs) => CorrectionSet.fromRows(
  specs.map(([kind, pattern, platform]) => ({ kind, pattern, platform, createdAt: "2026-01-01T00:00:00Z" })),
);

test("第 0 级：用户对具体值的纠正 -> manual，压过一切内置信号", () => {
  const cand = mkCand(SK48);
  const corr = rows([["exact", cand.sha256(), "moonshot"]]);
  const verdict = resolve(cand, corr);
  assert.equal(verdict.platform, "moonshot");
  assert.equal(verdict.confidence, CONFIDENCE_MANUAL);
  assert.equal(verdict.source, "correction");
  assert.ok(verdict.evidence.includes("2026-01-01"));
  assert.ok(verdict.trusted);
});

test("第 1 级压过第 2 级：主机名击败冲突的键名", () => {
  const text = `{"base_url":"https://api.deepseek.com/v1","openai_api_key":"${SK48}"}`;
  const verdict = resolveText(text);
  assert.equal(verdict.platform, "deepseek");
  assert.equal(verdict.confidence, CONFIDENCE_EXACT);
  assert.equal(verdict.source, "hostname");
  assert.equal(verdict.evidence, "主机名 api.deepseek.com");
  assert.deepEqual(verdict.maskStyle, [3, 4]);
});

test("第 2 级压过第 3 级：键名击败歧义形态", () => {
  const verdict = resolveText(`DEEPSEEK_API_KEY=${SK48}`);
  assert.equal(verdict.platform, "deepseek");
  assert.equal(verdict.confidence, CONFIDENCE_HIGH);
  assert.equal(verdict.source, "keyname");
});

test("第 3 级：独有形态 -> medium", () => {
  const verdict = resolveText(`sk-ant-api03-${"A".repeat(48)}`);
  assert.equal(verdict.platform, "anthropic");
  assert.equal(verdict.confidence, CONFIDENCE_MEDIUM);
  assert.equal(verdict.source, "shape");
  assert.equal(verdict.kind, "token");
});

test("第 3 级：PEM 与连接串带 kind 与 maskStyle", () => {
  const pem = resolveText(
    "-----BEGIN PRIVATE KEY-----\nMIIEvAQQ\n-----END PRIVATE KEY-----",
  );
  assert.equal(pem.platform, "pem-private-key");
  assert.equal(pem.kind, "pem");
  assert.deepEqual(pem.maskStyle, [0, 0]);

  const conn = resolveText("postgres://user:pass@db.example.com/x");
  assert.equal(conn.platform, "connection-string");
  assert.equal(conn.kind, "connstring");
  assert.deepEqual(conn.maskStyle, [0, 0]);
});

test("第 4 级：块级主机名救回裸 sk-（弱证据排第三）", () => {
  const text = [
    "LLM_BASE_URL=https://api.deepseek.com/v1",
    `LLM_API_KEY=${SK48}`,
    "LLM_MODEL=deepseek-chat",
    "LLM_TIMEOUT=30",
  ].join("\n");
  const verdict = resolveText(text);
  assert.equal(verdict.platform, "deepseek");
  assert.equal(verdict.confidence, CONFIDENCE_HIGH);
  assert.equal(verdict.source, "context");
  assert.equal(verdict.evidence, "同一块配置里的主机名 api.deepseek.com");
});

test("第 4 级不能改判同块 GITLAB_TOKEN（第 2 级已定）", () => {
  const text = [
    "LLM_BASE_URL=https://api.deepseek.com/v1",
    `LLM_API_KEY=${SK48}`,
    `GITLAB_TOKEN=glpat-${"b".repeat(20)}`,
  ].join("\n");
  const { candidates } = parse(text, { sourceHint: "golden" });
  const verdicts = candidates.map((cand) => resolve(cand));
  assert.deepEqual(
    verdicts.map((verdict) => [verdict.platform, verdict.source]),
    [["deepseek", "context"], ["gitlab", "keyname"]],
  );
});

test("第 5 级排在形态之后：ghp_ 不会被 DeepSeek 标题改判", () => {
  const verdict = resolveText(`ghp_${ALNUM36}`, { windowTitle: "DeepSeek API开放平台" });
  assert.equal(verdict.platform, "github");
  assert.equal(verdict.confidence, CONFIDENCE_MEDIUM);
  assert.equal(verdict.source, "shape");
});

test("第 5 级救回裸 sk-：厂商控制台标题", () => {
  const deepseek = resolveText(SK48, { windowTitle: "DeepSeek API开放平台 - Google Chrome" });
  assert.equal(deepseek.platform, "deepseek");
  assert.equal(deepseek.confidence, CONFIDENCE_HIGH);
  assert.equal(deepseek.source, "window");

  const openai = resolveText(SK48, { windowTitle: "OpenAI API keys - ChatGPT" });
  assert.equal(openai.platform, "openai");
  assert.equal(openai.source, "window");

  const amap = resolveText(HEX32, { windowTitle: "高德开放平台 - 应用管理 - Google Chrome" });
  assert.equal(amap.platform, "amap");
  assert.equal(amap.confidence, CONFIDENCE_HIGH);
  assert.equal(amap.source, "window");
});

test("第 5 级标题冲突时回到歧义诚实落点，候选≥4", () => {
  const verdict = resolveText(SK48, { windowTitle: "DeepSeek 和 OpenAI 的对比评测" });
  assert.equal(verdict.platform, "openai-compatible");
  assert.equal(verdict.confidence, CONFIDENCE_AMBIGUOUS);
  assert.ok(verdict.candidates.length >= 4);
  assert.ok(!verdict.candidates.includes("openai-compatible"));
});

test("第 6 级：裸 sk- 歧义落 openai-compatible，候选恰为全组、绝不猜厂商", () => {
  const verdict = resolveText(SK48);
  assert.equal(verdict.platform, "openai-compatible");
  assert.equal(verdict.confidence, CONFIDENCE_AMBIGUOUS);
  assert.deepEqual(verdict.candidates, [
    "dashscope", "deepseek", "moonshot", "openai", "siliconflow", "zhipu",
  ]);
  assert.ok(verdict.candidates.length >= 5);
  assert.ok(!["openai", "deepseek"].includes(verdict.platform));
});

test("第 6 级：hex-32 无佐证判 none（拒绝落盘），有键名即救回", () => {
  const refused = resolveText(HEX32);
  assert.equal(refused.platform, "unknown");
  assert.equal(refused.confidence, CONFIDENCE_NONE);
  assert.equal(isTrusted(refused), false);

  const rescued = resolveText(`AMAP_REST_KEY=${HEX32}`);
  assert.equal(rescued.platform, "amap");
  assert.equal(rescued.confidence, CONFIDENCE_HIGH);
  assert.equal(rescued.source, "keyname");

  const byHost = resolveText(`{"base_url":"https://restapi.amap.com/v3","api_key":"${HEX32}"}`);
  assert.equal(byHost.platform, "amap");
  assert.equal(byHost.confidence, CONFIDENCE_EXACT);
});

test("第 7 级：什么都没命中 -> unknown/none", () => {
  const verdict = resolveText("Ab3xK9mQ2pL7v");
  assert.equal(verdict.platform, "unknown");
  assert.equal(verdict.confidence, CONFIDENCE_NONE);
  assert.equal(verdict.source, "none");
  assert.equal(verdict.evidence, "没有任何规则匹配");
});

test("用户主机名纠正：精确与 .后缀命中，冲突即歧义", () => {
  const cand = mkCand(SK48, { hostnames: ["api.deepseek.com"] });
  const exact = resolve(cand, rows([["hostname", "api.deepseek.com", "deepseek"]]));
  assert.equal(exact.platform, "deepseek");
  assert.equal(exact.confidence, CONFIDENCE_MANUAL);

  const suffix = resolve(cand, rows([["hostname", "deepseek.com", "siliconflow"]]));
  assert.equal(suffix.platform, "siliconflow");
  assert.equal(suffix.confidence, CONFIDENCE_MANUAL);

  // 冲突需要两条不同 pattern 后缀命中同一主机（同名 pattern 桌面即后者覆盖前者）。
  const conflicted = mkCand(SK48, { hostnames: ["sub.api.deepseek.com"] });
  const conflict = resolve(conflicted, rows([
    ["hostname", "api.deepseek.com", "openai"],
    ["hostname", "deepseek.com", "siliconflow"],
  ]));
  assert.equal(conflict.platform, "unknown");
  assert.equal(conflict.confidence, CONFIDENCE_AMBIGUOUS);
  assert.deepEqual(conflict.candidates, ["openai", "siliconflow"]);
});

test("用户键名与窗口纠正；窗口冲突时静默落回内置分级", () => {
  const keynamed = resolve(mkCand(SK48, { keyName: "MY_KEY" }), rows([["keyname", "^MY_KEY$", "zhipu"]]));
  assert.equal(keynamed.platform, "zhipu");
  assert.equal(keynamed.confidence, CONFIDENCE_MANUAL);

  const windowed = resolve(mkCand(SK48), rows([["window", "deepseek", "deepseek"]]), { windowTitle: "DeepSeek 官网" });
  assert.equal(windowed.platform, "deepseek");
  assert.equal(windowed.confidence, CONFIDENCE_MANUAL);

  const conflict = resolve(mkCand(SK48), rows([
    ["window", "deepseek", "deepseek"],
    ["window", "deepseek", "openai"],
  ]), { windowTitle: "DeepSeek 平台" });
  assert.equal(conflict.confidence, CONFIDENCE_AMBIGUOUS); // 窗口冲突返回 null，落回第 6 级
});

test("组默认纠正：歧义组整体落到指定厂商（需用户明确要求）", () => {
  const verdict = resolve(mkCand(SK48), rows([["group", "openai_compatible", "moonshot"]]));
  assert.equal(verdict.platform, "moonshot");
  assert.equal(verdict.confidence, CONFIDENCE_MANUAL);
  assert.equal(verdict.evidence, "组默认纠正 openai_compatible -> moonshot");
});

test("块级主机名的用户纠正同样给 manual", () => {
  const cand = mkCand(SK48, { contextHostnames: ["api.deepseek.com"] });
  const verdict = resolve(cand, rows([["hostname", "deepseek.com", "deepseek"]]));
  assert.equal(verdict.platform, "deepseek");
  assert.equal(verdict.confidence, CONFIDENCE_MANUAL);
  assert.equal(verdict.source, "correction");
});

test("pick_by_specificity 的形态收窄：同级多命中时按值形态裁决", () => {
  // api.openai.com 同时挂在 openai(30) 与 openai-project(88) 上：
  // 裸 sk- 形态不符 openai-project，收窄后归 openai。
  const verdict = resolveText(`{"api_key":"${SK48}","url":"https://api.openai.com/v1"}`);
  assert.equal(verdict.platform, "openai");
  assert.equal(verdict.confidence, CONFIDENCE_EXACT);
  // 形态符合时 specificity 高者胜：AIza 值 + googleapis 主机名（google 85）。
  const google = resolveText('{"api_key":"' + "AIza" + "C".repeat(35) + '","url":"https://api.openai.com/v1"}');
  assert.equal(google.platform, "google");
});

test("suggestCorrectionScope：键名 > 主机名，组默认不静默选中", () => {
  const keyed = suggestCorrectionScope(mkCand(SK48, { keyName: "MY_KEY" }), null);
  assert.deepEqual(keyed, ["keyname", "^MY_KEY$", "以后凡是键名 MY_KEY 都判成这个平台"]);

  const hosted = suggestCorrectionScope(mkCand(SK48, { hostnames: ["API.Example.com"] }), null);
  assert.deepEqual(hosted, ["hostname", "api.example.com", "以后凡是主机名 API.Example.com 都判成这个平台"]);

  assert.equal(suggestCorrectionScope(mkCand(SK48), null), null);
  assert.deepEqual(
    groupCorrection("openai_compatible", "deepseek")[2],
    "此后该组（dashscope, deepseek, moonshot, openai, openai-compatible, siliconflow, zhipu）"
    + "的裸值一律默认 deepseek；个别记录可能是错的，用 kv set-platform 修",
  );
  assert.deepEqual(groupCorrection("openai_compatible", "deepseek")[0], "group");
});

test("describe：人话摘要带分级符号与候选清单", () => {
  const ambiguous = resolve(mkCand(SK48));
  assert.match(describe(ambiguous), /^openai-compatible\?  候选 dashscope, deepseek/);
  const exact = resolveText(`DEEPSEEK_API_KEY=${SK48}`);
  assert.equal(describe(exact), "deepseek+");
  assert.ok(new Verdict({ confidence: CONFIDENCE_NONE }) instanceof Verdict);
});
