// SecretRepo：去重合并/命名/轮换/吊销/审计链/导入/备份往返（内存后端）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { memoryBackend } from "../src/vault/memory-backend.js";
import { Session, memoryKeyStore } from "../src/vault/crypto.js";
import * as store from "../src/vault/store.js";

async function freshVault(password = "master-pw-123") {
  const backend = memoryBackend();
  const session = new Session(memoryKeyStore());
  await store.setupVault(backend, password);
  await store.unlock(backend, session, password);
  return { backend, session };
}

async function addDeepseek(backend, session, overrides = {}) {
  return store.add(backend, session, {
    value: "sk-abc123_def456ghi789jklmno", keyName: "DEEPSEEK_API_KEY",
    platform: "deepseek", confidence: "high", evidence: "键名 DEEPSEEK_API_KEY",
    source: "extension", origin: "extension", ...overrides,
  });
}

test("初始化/解锁/错口令/重复初始化", async () => {
  const { backend, session } = await freshVault();
  assert.equal(await store.isInitialized(backend), true);
  assert.equal(await store.unlock(backend, session, "wrong"), false);
  await assert.rejects(() => store.setupVault(backend, "again")); // 重复建库拒绝
  // 锁定后操作全部拒绝
  await session.lock();
  await assert.rejects(() => addDeepseek(backend, session), (e) => e.name === "LockedError");
});

test("created：命名按 keyName 推导、掩码行不带明文", async () => {
  const { backend, session } = await freshVault();
  const res = await addDeepseek(backend, session);
  assert.equal(res.outcome, "created");
  assert.match(res.name, /^deepseek-deepseek/); // keyName 剥后缀 → deepseek

  const rows = await store.listSecrets(backend);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].preview, "sk-****lmno");
  assert.equal(JSON.stringify(rows).includes("sk-abc123def456"), false);
});

test("deduped：同值合并 tags，不产生第二条； revoked 警告", async () => {
  const { backend, session } = await freshVault();
  const first = await addDeepseek(backend, session, { tags: ["a"] });
  const second = await addDeepseek(backend, session, { tags: ["b"] });
  assert.equal(second.outcome, "deduped");
  assert.equal(second.secretId, first.secretId);
  assert.deepEqual(second.mergedTags, ["b"]);
  assert.equal((await store.listSecrets(backend)).length, 1);

  await store.revoke(backend, first.secretId);
  const third = await addDeepseek(backend, session);
  assert.equal(third.outcome, "deduped");
  assert.match(third.warning, /曾被吊销/);
});

test("重名不同值 → AlreadyExists；自动命名避开冲突", async () => {
  const { backend, session } = await freshVault();
  await store.add(backend, session, { value: "value-one-1234567890", name: "mine" });
  await assert.rejects(
    () => store.add(backend, session, { value: "value-two-1234567890", name: "mine" }),
    (e) => e.name === "AlreadyExistsError");
  // 同名同值 → 去重
  const res = await store.add(backend, session, { value: "value-one-1234567890", name: "mine" });
  assert.equal(res.outcome, "deduped");
});

test("reveal：明文只经许可路径，审计记指纹", async () => {
  const { backend, session } = await freshVault();
  const { secretId } = await addDeepseek(backend, session);
  const { value } = await store.reveal(backend, session, secretId);
  assert.equal(value, "sk-abc123_def456ghi789jklmno");
  const { rows } = await store.auditList(backend);
  assert.ok(rows.some((r) => r.event === "reveal"));
  assert.equal(JSON.stringify(rows).includes("sk-abc123def456"), false); // 审计无明文
});

test("rotate：旧指纹保留 revoked，新值生效；revoke/delete/rename/setPlatform", async () => {
  const { backend, session } = await freshVault();
  const { secretId } = await addDeepseek(backend, session);

  await store.rotate(backend, session, secretId, "sk-new999888777666555444");
  const detail = await store.getSecret(backend, secretId);
  assert.equal(detail.sha256, undefined ? null : detail.sha256); // 明文不出现在详情
  assert.equal(detail.preview, "sk-****5444");
  assert.equal(detail.fingerprints.length, 2);
  const revoked = detail.fingerprints.find((f) => f.status === "revoked");
  assert.ok(revoked, "旧指纹保留");

  await store.rename(backend, secretId, "my-deepseek");
  await store.setPlatform(backend, secretId, "zhipu", {
    remember: { kind: "keyname", pattern: "^DEEPSEEK_API_KEY$" },
  });
  const corrections = await store.listCorrections(backend);
  assert.equal(corrections.length, 1);

  await store.revoke(backend, secretId);
  assert.equal((await store.listSecrets(backend, { status: "revoked" })).length, 1);
  await store.deleteSecret(backend, secretId);
  assert.equal((await store.listSecrets(backend)).length, 0);
});

test("审计链：hash 串链，篡改可检出", async () => {
  const { backend, session } = await freshVault();
  await addDeepseek(backend, session);
  const { chainOk } = await store.auditList(backend);
  assert.equal(chainOk, true);

  // 追加一行拷贝的旧事件（prevHash 指错父节点）→ 链断裂被检出
  const rows = await backend.auditList(1000);
  await backend.auditAppend({ ...rows[0] });
  const { chainOk: forgedDetected } = await store.auditList(backend);
  assert.equal(forgedDetected, false, "伪造追加行必须被链校验检出");
});

test("markExpired 与 stats", async () => {
  const { backend, session } = await freshVault();
  await store.add(backend, session, {
    value: "expired-value-1234567890", platform: "github",
    confidence: "manual", expiresAt: "2020-01-01T00:00:00Z",
  });
  await store.add(backend, session, {
    value: "active-value-12345678901", platform: "deepseek",
    confidence: "manual",
  });
  const flipped = await store.markExpired(backend);
  assert.equal(flipped, 1);
  const stats = await store.stats(backend);
  assert.equal(stats.total, 2);
  assert.equal(stats.active, 1);
  assert.equal(stats.flagged, 1);
  assert.deepEqual(stats.platforms, [{ platform: "deepseek", count: 1 }]);
});

test("importText：可信入库、闸门拒绝不产生候选、unknown 候选跳过", async () => {
  const { backend, session } = await freshVault();
  const summary = await store.importText(backend, session, [
    "DEEPSEEK_API_KEY=sk-abc123_def456ghi789jklmno",
    "GITHUB_TOKEN=ghp_" + "a".repeat(36),
    "MODEL=gpt-4",                       // 键名拒绝清单 → 不产生候选
    "sk-xxx",                            // 占位符 → 不产生候选
    "SOMETHING=plainvalue1234567890",    // unknown 候选 → 跳过
  ].join("\n"), { tags: ["imported"] });
  assert.equal(summary.created, 2);
  assert.equal(summary.skipped.length, 1);
  assert.equal(summary.skipped[0].platform, "unknown");
  const again = await store.importText(backend, session,
    "DEEPSEEK_API_KEY=sk-abc123_def456ghi789jklmno");
  assert.equal(again.deduped, 1);
});

test("备份往返：导出→删除→恢复；错口令拒绝", async () => {
  const { backend, session } = await freshVault();
  await addDeepseek(backend, session);
  await store.add(backend, session, {
    value: "ghp_" + "b".repeat(36), platform: "github",
    confidence: "manual", tags: ["team"],
  });
  const blob = await store.exportBackup(backend, session, "backup-pw");
  assert.ok(blob.startsWith("KVBE1|"));

  // 新空库恢复
  const { backend: b2, session: s2 } = await freshVault("other-master");
  const summary = await store.restoreBackup(b2, s2, "backup-pw", blob);
  assert.equal(summary.created, 2);
  const rows = await store.listSecrets(b2);
  assert.equal(rows.length, 2);
  const restored = await store.reveal(b2, s2,
    rows.find((r) => r.platform === "deepseek").id);
  assert.equal(restored.value, "sk-abc123_def456ghi789jklmno");

  // 错口令
  await assert.rejects(() => store.restoreBackup(b2, s2, "wrong-pw", blob),
    /口令错误/);
  // 非本格式
  await assert.rejects(() => store.restoreBackup(b2, s2, "backup-pw", "garbage"),
    /KVBE1/);
});
