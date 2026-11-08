// scan.js 单测：网页泄露扫描引擎（kv/ops/scan.py 网页版）。
// 命中形状 {textName,lineNo,name,platform}、rotate 前旧值仍命中（revoked，
// repo.py:300 all_scan_fingerprints 同语义）、锁定态 LockedError、无关文本
// 零命中、同 secret 同行去重、单消息三条上限（C6）。

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { Session, memoryKeyStore } from "../src/vault/crypto.js";
import { memoryBackend } from "../src/vault/memory-backend.js";
import * as store from "../src/vault/store.js";
import { scanTexts, MAX_TEXTS_PER_CALL, MAX_CONTENT_CHARS, MAX_TOTAL_CHARS } from "../src/vault/scan.js";

const MASTER = "master-pass-8";
const VALUE_A = "LiveKey-Alpha-7788ZZ";
const VALUE_B = "Rotated-Gamma-0011QQ";
const VALUE_C = "Solo-Delta-5566HH";

async function makeVault() {
  const backend = memoryBackend();
  const session = new Session(memoryKeyStore());
  await store.setupVault(backend, MASTER);
  await store.unlock(backend, session, MASTER);
  return { backend, session };
}

function spec(value, overrides = {}) {
  return {
    value, platform: "acme", confidence: "manual", evidence: "测试录入",
    source: "manual", keyName: "SCAN_KEY", origin: "manual", ...overrides,
  };
}

describe("scan 命中与状态", () => {
  it("贴入含已存 key 的文本命中 {textName,lineNo,secretId,name,platform,status,sha256Prefix}", async () => {
    const { backend, session } = await makeVault();
    const added = await store.add(backend, session, spec(VALUE_A));
    const hits = await scanTexts(backend, session, [{
      name: "notes.txt",
      content: "第一行无关\n这里写着 LiveKey-Alpha-7788ZZ 别外传\n第三行无关",
    }]);
    assert.equal(hits.length, 1);
    const hit = hits[0];
    assert.equal(hit.textName, "notes.txt");
    assert.equal(hit.lineNo, 2);
    assert.equal(hit.secretId, added.secretId);
    assert.equal(hit.name, "scan-key");
    assert.equal(hit.platform, "acme");
    assert.equal(hit.status, "active");
    assert.equal(hit.sha256Prefix.length, 12);
  });

  it("rotate 前的旧值仍命中（revoked）；新值命中（active）", async () => {
    const { backend, session } = await makeVault();
    const added = await store.add(backend, session, spec(VALUE_A));
    await store.rotate(backend, session, added.secretId, VALUE_B);

    const hits = await scanTexts(backend, session, [
      { name: "old-leak.txt", content: `旧值 ${VALUE_A} 还躺在 git 历史里` },
      { name: "new-file.txt", content: `新值 ${VALUE_B}` },
    ]);
    const byStatus = new Map(hits.map((h) => [h.status, h]));
    assert.equal(byStatus.get("revoked").textName, "old-leak.txt");
    assert.equal(byStatus.get("revoked").secretId, added.secretId);
    assert.equal(byStatus.get("active").textName, "new-file.txt");
  });

  it("revoke 后值仍命中（status=revoked）", async () => {
    const { backend, session } = await makeVault();
    const added = await store.add(backend, session, spec(VALUE_A));
    await store.revoke(backend, session, added.secretId);
    const hits = await scanTexts(backend, session, [{ name: "x.txt", content: VALUE_A }]);
    assert.equal(hits.length, 1);
    assert.equal(hits[0].status, "revoked");
  });

  it("无关文本零命中", async () => {
    const { backend, session } = await makeVault();
    await store.add(backend, session, spec(VALUE_A));
    const hits = await scanTexts(backend, session, [
      { name: "clean.txt", content: "这里没有任何密钥\n只有普通句子" },
    ]);
    assert.deepEqual(hits, []);
  });

  it("同 secret 同行多值只出一条（C6）", async () => {
    const { backend, session } = await makeVault();
    const added = await store.add(backend, session, spec(VALUE_A));
    await store.rotate(backend, session, added.secretId, VALUE_B);
    const hits = await scanTexts(backend, session, [
      { name: "both.txt", content: `一行里同时有 ${VALUE_A} 和 ${VALUE_B}` },
    ]);
    assert.equal(hits.length, 1); // 同一 secret 的新旧两个值同在一行 → 一条
  });

  it("不同 secret 各出一条；行号按 1 起算（含 \\r\\n）", async () => {
    const { backend, session } = await makeVault();
    await store.add(backend, session, spec(VALUE_A));
    await store.add(backend, session, spec(VALUE_C, { keyName: "OTHER_KEY" }));
    const hits = await scanTexts(backend, session, [{
      name: "multi.txt", content: `第一行\r\n第二行有 ${VALUE_C}\r\n第三行有 ${VALUE_A}`,
    }]);
    assert.equal(hits.length, 2);
    const byName = new Map(hits.map((h) => [h.name, h.lineNo]));
    assert.equal(byName.get("scan-key"), 3);
    assert.equal(byName.get("other-key"), 2);
  });

  it("一次调用记一条 scan 审计（texts/hits 计数）", async () => {
    const { backend, session } = await makeVault();
    await store.add(backend, session, spec(VALUE_A));
    await scanTexts(backend, session, [{ name: "a.txt", content: VALUE_A }]);
    const { rows } = await store.auditList(backend);
    const scanRow = rows.find((r) => r.event === "scan");
    assert.ok(scanRow);
    assert.equal(scanRow.detail.texts, 1);
    assert.equal(scanRow.detail.hits, 1);
  });
});

describe("scan 边界与约束（C6）", () => {
  it("锁定态抛 LockedError", async () => {
    const { backend, session } = await makeVault();
    await store.add(backend, session, spec(VALUE_A));
    await session.lock();
    await assert.rejects(
      () => scanTexts(backend, session, [{ name: "x.txt", content: VALUE_A }]),
      (error) => error.name === "LockedError",
    );
  });

  it("单条 content 超 1_000_000 字符报错", async () => {
    const { backend, session } = await makeVault();
    const big = "x".repeat(MAX_CONTENT_CHARS + 1);
    await assert.rejects(
      () => scanTexts(backend, session, [{ name: "big.txt", content: big }]),
      /最多 1000000 字符/,
    );
  });

  it("条目超 100 报错", async () => {
    const { backend, session } = await makeVault();
    const texts = Array.from({ length: MAX_TEXTS_PER_CALL + 1 }, (_, i) => ({
      name: `t${i}.txt`, content: "short",
    }));
    await assert.rejects(() => scanTexts(backend, session, texts), /100/);
  });

  it("总字符超 4_000_000 报错", async () => {
    const { backend, session } = await makeVault();
    const chunk = "y".repeat(41_000); // 100 条 × 41k = 4.1M > 4M，单条不超限
    const texts = Array.from({ length: 100 }, (_, i) => ({
      name: `c${i}.txt`, content: chunk,
    }));
    await assert.rejects(() => scanTexts(backend, session, texts), /总字符最多 4000000/);
  });

  it("content 非字符串的条目跳过不炸", async () => {
    const { backend, session } = await makeVault();
    await store.add(backend, session, spec(VALUE_A));
    const hits = await scanTexts(backend, session, [
      { name: "bad.txt", content: null },
      { name: "good.txt", content: `value ${VALUE_A}` },
    ]);
    assert.equal(hits.length, 1);
    assert.equal(hits[0].textName, "good.txt");
  });
});
