/**
 * 金标语料直读对拍 —— 不复制语料，直接用 node:fs 读仓库正本
 * ../../tests/corpus/{01-shape,02-context,03-negative,04-structures}.jsonl，
 * 按 kv/selftest.py 的 expand 配方与 evaluate 断言字段逐条对拍。
 *
 * 桌面语料任何一条改动，浏览器侧测试立刻同响——单源无副本。
 * 语料为零/路径不通时按「零用例不算通过」响亮失败（对齐 kv/selftest.py:73-77）。
 *
 * 断言字段与 kv/selftest.py evaluate()（selftest.py:116-133）完全一致：
 * expect / expect_reason / expect_count / expect_platform / expect_platforms /
 * expect_confidence / expect_source / expect_not / expect_candidates_min /
 * expect_refused / expect_kind / expect_extra_has / expect_pair / window_title。
 * window_title 喂 resolve 的页面标题参数，对应桌面第 5 级窗口信号。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { parse } from "../src/core/parse.js";
import { resolve } from "../src/core/resolve.js";
import { CorrectionSet } from "../src/core/corrections.js";

const CORPUS_DIR = fileURLToPath(new URL("../../tests/corpus/", import.meta.url));

// kv/selftest.py:32-48 的合成配方，逐条转写；顺序与桌面 dict 一致。
const SYNTHETIC = [
  ["<48位合成>", "A".repeat(48)],
  ["<48>", "A".repeat(48)],
  ["<32hex>", "0123456789abcdef".repeat(2)],
  ["<40hex>", "0123456789abcdef".repeat(2) + "01234567"],
  ["<64hex>", "0123456789abcdef".repeat(4)],
  ["<36位字母数字>", "abcdefghij0123456789".repeat(2).slice(0, 36)],
  ["<35位字母数字>", "abcdefghij0123456789".repeat(2).slice(0, 35)],
  ["<35>", "abcdefghij0123456789".repeat(2).slice(0, 35)],
  ["<36>", "abcdefghij0123456789".repeat(2).slice(0, 36)],
  ["<20位字母数字>", "abcdefghij0123456789".repeat(2).slice(0, 20)],
  ["<20>", "abcdefghij0123456789".repeat(2).slice(0, 20)],
  ["<16位大写>", "ABCDEFGHIJKLMNOP"],
  ["<40位字母数字>", "abcdefghij0123456789".repeat(2).slice(0, 40)],
  ["<合成 200 字符 base64>", "MIIEvA" + "A".repeat(194)],
  ["<24位>", "aB3xK9mQ2pL7vR1sT4uW6y"],
];

function expand(text) {
  let expanded = text;
  for (const [placeholder, value] of SYNTHETIC) {
    expanded = expanded.replaceAll(placeholder, value);
  }
  return expanded;
}

/** 与 kv/selftest.py load_cases 同构：排序遍历 *.jsonl，跳过空行与 # 注释。 */
function loadCases(dir) {
  const cases = [];
  const files = readdirSync(dir).filter((name) => name.endsWith(".jsonl")).sort();
  for (const name of files) {
    const lines = readFileSync(join(dir, name), "utf-8").split(/\r?\n/);
    for (const [index, line] of lines.entries()) {
      const stripped = line.trim();
      if (!stripped || stripped.startsWith("#")) continue;
      let parsed;
      try {
        parsed = JSON.parse(stripped);
      } catch (exc) {
        cases.push({ id: `${name}:${index + 1}`, _parse_error: String(exc.message) });
        continue;
      }
      if (parsed.id === undefined) parsed.id = `${name}:${index + 1}`;
      parsed._file = name;
      cases.push(parsed);
    }
  }
  return cases;
}

function stringify(value) {
  return Array.isArray(value) ? JSON.stringify(value) : String(value);
}

function makeCheck(caseId) {
  const failures = [];
  return {
    failures,
    eq(field, expected, actual) {
      const same = Array.isArray(expected)
        ? JSON.stringify(expected) === JSON.stringify(actual)
        : expected === actual;
      if (!same) failures.push({ caseId, field, expected: stringify(expected), actual: stringify(actual) });
    },
    fail(field, expected, actual) {
      failures.push({ caseId, field, expected, actual });
    },
  };
}

/** 跑一个用例，返回不符项。结构与 kv/selftest.py evaluate() 逐段对齐。 */
function evaluate(testCase) {
  if ("_parse_error" in testCase) {
    return [{ caseId: testCase.id, field: "json", expected: "可解析", actual: testCase._parse_error }];
  }

  const text = expand(testCase.input ?? "");
  const result = parse(text, { sourceHint: "golden" });
  const verdicts = result.candidates.map((cand) => resolve(
    cand,
    CorrectionSet.empty(),
    { windowTitle: testCase.window_title ?? null },
  ));
  const check = makeCheck(testCase.id);

  // ---- _check_acceptance：expect / expect_reason / expect_count ----
  if ("expect" in testCase) {
    check.eq("expect", testCase.expect, result.candidates.length > 0 ? "accepted" : "rejected");
    if (testCase.expect === "rejected") {
      if ("expect_reason" in testCase) {
        const reasons = [...new Set(result.rejected.map((item) => item.reason))].sort();
        if (!reasons.includes(testCase.expect_reason)) {
          check.fail("expect_reason", testCase.expect_reason, reasons.join(",") || "(无拒绝记录)");
        }
      }
      return check.failures;
    }
  }
  if ("expect_count" in testCase) {
    check.eq("expect_count", testCase.expect_count, result.candidates.length);
  }
  if (result.candidates.length === 0) return check.failures;

  // ---- _check_verdict：平台、置信度、判定来源 ----
  const first = verdicts[0];
  const platforms = verdicts.map((verdict) => verdict.platform);
  if ("expect_platforms" in testCase) {
    check.eq("expect_platforms", [...testCase.expect_platforms].sort(), [...platforms].sort());
  }
  if ("expect_platform" in testCase) {
    check.eq("expect_platform", testCase.expect_platform, first.platform);
  }
  if ("expect_confidence" in testCase) {
    check.eq("expect_confidence", testCase.expect_confidence, first.confidence);
  }
  if ("expect_source" in testCase) {
    check.eq("expect_source", testCase.expect_source, first.source);
  }
  if ("expect_not" in testCase && platforms.includes(testCase.expect_not)) {
    check.fail("expect_not", `不得出现 ${testCase.expect_not}`, platforms.join(","));
  }
  if ("expect_candidates_min" in testCase && first.candidates.length < testCase.expect_candidates_min) {
    check.fail("expect_candidates_min", `>=${testCase.expect_candidates_min}`, String(first.candidates.length));
  }
  if (testCase.expect_refused && first.confidence !== "none") {
    check.fail("expect_refused", "confidence=none（拒绝落盘）", first.confidence);
  }

  // ---- _check_structure：kind / extra / pair ----
  const firstCandidate = result.candidates[0];
  if ("expect_kind" in testCase) {
    check.eq("expect_kind", testCase.expect_kind, firstCandidate.kind);
  }
  if ("expect_extra_has" in testCase && !(testCase.expect_extra_has in firstCandidate.extra)) {
    check.fail("expect_extra_has", testCase.expect_extra_has,
      Object.keys(firstCandidate.extra).sort().join(",") || "(空)");
  }
  if (testCase.expect_pair) {
    const pairIds = new Set(
      result.candidates.map((cand) => cand.extra.pair_id).filter(Boolean),
    );
    if (pairIds.size !== 1 || result.candidates.length < 2) {
      check.fail("expect_pair", "两条记录共享一个 pair_id",
        `${result.candidates.length} 条 / ${pairIds.size} 个 pair_id`);
    }
  }
  return check.failures;
}

test("金标语料非空：零用例不算通过（对齐 kv/selftest.py:73-77）", () => {
  const cases = loadCases(CORPUS_DIR);
  assert.ok(
    cases.length > 0,
    `语料目录不可读或为空：${CORPUS_DIR}——零用例不算通过，检测器静默失效是最贵的失败模式`,
  );
});

test("金标语料直读与桌面 selftest 逐条对拍", async (t) => {
  const cases = loadCases(CORPUS_DIR);
  assert.ok(cases.length > 0, "语料为零不算通过");
  for (const testCase of cases) {
    await t.test(`golden ${testCase.id}`, () => {
      const failures = evaluate(testCase);
      assert.equal(
        failures.length,
        0,
        failures
          .map((item) => `${item.field}: 期望 ${item.expected}，实际 ${item.actual}`)
          .join("; "),
      );
    });
  }
});
