// detect.js 单测：检测组合入口（C4）——确定性、corrections 装载生效、
// promptable 口径（C1#5/C9#1：medium/ambiguous 为 true、none 为 false）、
// pageTitle 走 resolve 的窗口信号（对齐金标语料 window-rescues-bare-sk）。
// 期望值全部锚定桌面金标（tests/corpus/，python -I kv.py selftest --golden
// 92 条全 PASS 的同一套语义）。

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { Session, memoryKeyStore } from "../src/vault/crypto.js";
import { memoryBackend } from "../src/vault/memory-backend.js";
import * as store from "../src/vault/store.js";
import { detectText } from "../src/vault/detect.js";
import { fingerprint } from "../src/core/masking.js";

const MASTER = "master-pass-8";
const SK_TOKEN = `sk-${"A".repeat(48)}`; // 金标 bare-sk-must-be-ambiguous 的合成配方
const SK_PROJ_TOKEN = `sk-proj-${"A".repeat(48)}`; // 金标 openai-project-key-is-not-ambiguous
const HEX32 = "0123456789abcdef".repeat(2); // 金标 <32hex> 配方展开

async function makeVault() {
  const backend = memoryBackend();
  const session = new Session(memoryKeyStore());
  await store.setupVault(backend, MASTER);
  await store.unlock(backend, session, MASTER);
  return { backend, session };
}

describe("detectText 确定性与形状", () => {
  it("同一 backend+文本两次调用结果相等", async () => {
    const { backend } = await makeVault();
    const text = `DEEPSEEK_API_KEY=${SK_TOKEN}`;
    const first = await detectText(backend, text);
    const second = await detectText(backend, text);
    assert.deepEqual(second, first);
    assert.equal(first.length, 1);
  });

  it("结果字段齐全（C4 返回形状）", async () => {
    const { backend } = await makeVault();
    const findings = await detectText(backend, `DEEPSEEK_API_KEY=${SK_TOKEN}`);
    assert.equal(findings.length, 1);
    const finding = findings[0];
    for (const field of ["value", "keyName", "kind", "platform", "confidence",
      "evidence", "source", "maskStyle", "preview", "promptable"]) {
      assert.ok(field in finding, `缺字段 ${field}`);
    }
    assert.equal(finding.value, SK_TOKEN);
    assert.equal(finding.keyName, "DEEPSEEK_API_KEY");
    assert.equal(finding.platform, "deepseek"); // 金标 keyname-beats-*：键名压过形态
    assert.equal(finding.confidence, "high");
    assert.equal(finding.source, "keyname");
    assert.equal(finding.promptable, true);
  });

  it("空文本/无候选文本返回空数组", async () => {
    const { backend } = await makeVault();
    assert.deepEqual(await detectText(backend, ""), []);
    assert.deepEqual(await detectText(backend, "这里只有一个普通句子。"), []);
  });
});

describe("detectText promptable 口径（C1#5/C9#1）", () => {
  it("ambiguous（裸 sk- → openai-compatible）promptable=true", async () => {
    const { backend } = await makeVault();
    const [finding] = await detectText(backend, SK_TOKEN);
    assert.equal(finding.platform, "openai-compatible");
    assert.equal(finding.confidence, "ambiguous");
    assert.equal(finding.promptable, true);
  });

  it("medium（sk-proj- → openai-project）promptable=true", async () => {
    const { backend } = await makeVault();
    const [finding] = await detectText(backend, SK_PROJ_TOKEN);
    assert.equal(finding.platform, "openai-project");
    assert.equal(finding.confidence, "medium");
    assert.equal(finding.promptable, true);
  });

  it("none（裸 hex-32 → unknown）promptable=false", async () => {
    const { backend } = await makeVault();
    const [finding] = await detectText(backend, HEX32);
    assert.equal(finding.platform, "unknown");
    assert.equal(finding.confidence, "none");
    assert.equal(finding.promptable, false);
  });
});

describe("detectText corrections 装载", () => {
  it("exact 纠正把指定 sha 判为 manual", async () => {
    const { backend } = await makeVault();
    const sha = fingerprint(SK_TOKEN, "token");
    await store.addCorrection(backend, {
      kind: "exact", pattern: sha, platform: "deepseek", note: "用户指定",
    });
    const [finding] = await detectText(backend, SK_TOKEN);
    assert.equal(finding.platform, "deepseek");
    assert.equal(finding.confidence, "manual");
    assert.equal(finding.promptable, true);
  });

  it("keyname 纠正生效", async () => {
    const { backend } = await makeVault();
    await store.addCorrection(backend, {
      kind: "keyname", pattern: "^VENDOR_TOKEN$", platform: "vendor-x", note: "",
    });
    const [finding] = await detectText(backend, "VENDOR_TOKEN=somevalue123456");
    assert.equal(finding.platform, "vendor-x");
    assert.equal(finding.confidence, "manual");
  });

  it("无纠正时同一候选不误判（exact 只对命中 sha 生效）", async () => {
    const { backend } = await makeVault();
    await store.addCorrection(backend, {
      kind: "exact", pattern: fingerprint(HEX32, "token"), platform: "amap", note: "",
    });
    const findings = await detectText(backend, SK_TOKEN);
    assert.notEqual(findings[0].platform, "amap");
  });
});

describe("detectText pageTitle 窗口信号", () => {
  it("标题救回裸 sk-（金标 window-rescues-bare-sk）", async () => {
    const { backend } = await makeVault();
    const [finding] = await detectText(backend, SK_TOKEN, {
      pageTitle: "DeepSeek API开放平台 - Google Chrome",
    });
    assert.equal(finding.platform, "deepseek");
    assert.equal(finding.confidence, "high");
    assert.equal(finding.source, "window");
    assert.equal(finding.promptable, true);
  });

  it("标题与键名冲突时键名赢（金标 keyname-beats-window-title）", async () => {
    const { backend } = await makeVault();
    const [finding] = await detectText(backend, `OPENAI_API_KEY=${SK_TOKEN}`, {
      pageTitle: "DeepSeek API开放平台 - Google Chrome",
    });
    assert.equal(finding.platform, "openai");
    assert.notEqual(finding.platform, "deepseek");
  });

  it("无标题时裸 sk- 回落 ambiguous（金标 bare-sk-must-be-ambiguous）", async () => {
    const { backend } = await makeVault();
    const [finding] = await detectText(backend, SK_TOKEN, { pageTitle: null });
    assert.equal(finding.confidence, "ambiguous");
  });
});
