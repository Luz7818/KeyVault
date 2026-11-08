// store.js 单测：初始化/解锁/锁定语义、add 的 created/deduped 合并
// （对齐 kv/ops/save.py:82-135）、suggest_name（save.py:63-79）、rotate
// （rotate.py，旧指纹 revoked + revokedAt、同值拒绝、重复拒绝）、各操作审计行
// 与哈希链（C4 公式）、备份 KVBE1 往返（backup.py:100-105 口令下限）、
// destroyVault 鉴权清库（C1#30）、importText 经 detect.js（C4/C9#2）。

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { Session, memoryKeyStore, b64ToBytes, deriveRawKey, importRawKey, decryptText } from "../src/vault/crypto.js";
import { memoryBackend } from "../src/vault/memory-backend.js";
import * as store from "../src/vault/store.js";

const MASTER = "master-pass-8";
const V1 = "Zk9mLiveValue-4tX1";
const V2 = "Qw7nRotatedValue-88zXy";
const V3 = "ThirdValue-Pl3AsE-9931";

async function makeVault() {
  const backend = memoryBackend();
  const session = new Session(memoryKeyStore());
  await store.setupVault(backend, MASTER);
  assert.equal(await store.unlock(backend, session, MASTER), true);
  return { backend, session };
}

function baseSpec(overrides = {}) {
  return {
    value: V1, platform: "deepseek", confidence: "high", evidence: "键名命中",
    source: "keyname", keyName: "K1", origin: "extension", actor: "test", ...overrides,
  };
}

// audit.py:28-31 的黑名单：所有审计 detail 的键名都不许落进去
const FORBIDDEN_DETAIL_KEYS = new Set([
  "value", "secret", "token", "password", "passwd", "key", "api_key", "apikey",
  "plaintext", "blob", "credential", "authorization",
]);

describe("store 初始化与解锁", () => {
  it("setupVault 写 meta + init 审计，返回 32 字节原始密钥；isInitialized 翻真", async () => {
    const backend = memoryBackend();
    assert.equal(await store.isInitialized(backend), false);
    const raw = await store.setupVault(backend, MASTER);
    assert.equal(raw instanceof Uint8Array, true);
    assert.equal(raw.length, 32);
    assert.equal(await store.isInitialized(backend), true);
    const meta = await backend.metaGet("vault-meta");
    assert.equal(meta.kdf, "PBKDF2-SHA256");
    assert.equal(meta.iterations, 600000);
    assert.equal(b64ToBytes(meta.salt).length, 16);
    const { rows } = await store.auditList(backend);
    assert.equal(rows[0].event, "init");
  });

  it("重复初始化拒绝", async () => {
    const { backend } = await makeVault();
    await assert.rejects(() => store.setupVault(backend, MASTER), /已经初始化/);
  });

  it("口令 <8 位拒绝（setup 与 export 同口径）", async () => {
    const backend = memoryBackend();
    await assert.rejects(() => store.setupVault(backend, "short"), /主密码至少 8 位/);
    assert.equal(await store.isInitialized(backend), false);
  });

  it("错口令 unlock 返回 false 且会话保持锁定", async () => {
    const { backend } = await makeVault();
    const session = new Session(memoryKeyStore());
    assert.equal(await store.unlock(backend, session, "wrong-pass-9"), false);
    assert.equal(await session.isUnlocked(), false);
  });

  it("未初始化 unlock 抛错", async () => {
    const backend = memoryBackend();
    await assert.rejects(
      () => store.unlock(backend, new Session(memoryKeyStore()), MASTER),
      /未初始化/,
    );
  });
});

describe("store 锁定态：一切写操作抛 LockedError", () => {
  it("add/rotate/revoke/delete/rename/setPlatform/reveal/import/export/restore 全部拒绝", async () => {
    const { backend, session } = await makeVault();
    const { secretId } = await store.add(backend, session, baseSpec());
    await session.lock();

    const lockedOps = [
      () => store.add(backend, session, baseSpec()),
      () => store.rotate(backend, session, secretId, V2),
      () => store.revoke(backend, session, secretId),
      () => store.deleteSecret(backend, session, secretId),
      () => store.rename(backend, session, secretId, "new-name"),
      () => store.setPlatform(backend, session, secretId, "github"),
      () => store.reveal(backend, session, secretId),
      () => store.importText(backend, session, `TOKEN=${V1}`),
      () => store.exportBackup(backend, session, "backup-pass-1"),
      () => store.restoreBackup(backend, session, "backup-pass-1", "KVBE1|x|y|z"),
    ];
    for (const op of lockedOps) {
      await assert.rejects(op, (error) => {
        assert.equal(error.name, "LockedError");
        return true;
      });
    }
  });
});

describe("store add：created / deduped（save.py:82-135 对齐）", () => {
  it("created：落库 + 指纹行 + save 审计", async () => {
    const { backend, session } = await makeVault();
    const result = await store.add(backend, session, baseSpec());
    assert.equal(result.outcome, "created");
    assert.equal(result.name, "k1"); // suggest_name：K1 小写化
    const record = await backend.secretGet(result.secretId);
    assert.equal(record.platform, "deepseek");
    assert.equal(record.confidence, "high");
    assert.equal(record.status, "active");
    assert.equal(record.sha256, record.sha256.toLowerCase());
    assert.equal(record.sha256.length, 64);
    assert.equal(record.valueLen, V1.length);
    assert.deepEqual(record.tags, []);
    const fps = await backend.fpListBySecret(result.secretId);
    assert.equal(fps.length, 1);
    assert.equal(fps[0].status, "active");
    assert.equal(fps[0].sha256, record.sha256);
    const { rows } = await store.auditList(backend);
    const saveRow = rows.find((r) => r.event === "save");
    assert.equal(saveRow.shaPrefix, record.sha256.slice(0, 12));
    assert.equal(saveRow.detail.platform, "deepseek");
    assert.equal(saveRow.detail.len, V1.length);
  });

  it("deduped：合并 tags、补别名、空位回填、touch updated_at", async () => {
    const { backend, session } = await makeVault();
    const first = await store.add(backend, session, baseSpec());
    const original = await backend.secretGet(first.secretId);

    const before = Date.now();
    const second = await store.add(backend, session, baseSpec({
      value: `  ${V1}  `, // 剪贴板常带空白：规范化后同值
      tags: ["b", "c"],
      name: "custom-alias",
      note: "第二来源",
      sourceUrl: "https://example.com/rotated",
      expiresAt: "2030-01-01T00:00:00.000Z",
      maskStyle: [3, 4],
    }));
    assert.equal(second.outcome, "deduped");
    assert.equal(second.secretId, first.secretId);
    assert.equal(second.name, "k1");
    assert.deepEqual([...second.mergedTags].sort(), ["alias:custom-alias", "b", "c"].sort());

    const merged = await backend.secretGet(first.secretId);
    assert.deepEqual([...merged.tags].sort(), ["b", "c"].sort());
    assert.ok(merged.aliases.includes("custom-alias"));
    assert.equal(merged.note, "第二来源"); // 原值为空 → 回填
    assert.equal(merged.sourceUrl, "https://example.com/rotated");
    assert.equal(merged.expiresAt, "2030-01-01T00:00:00.000Z");
    assert.equal(merged.maskHead, V1.slice(0, 3)); // 原 maskHead 为空 → 回填
    assert.equal(merged.maskTail, original.maskTail); // 原 maskTail 非空 → 保留
    assert.equal(merged.sha256, original.sha256);
    assert.ok(merged.updatedAt >= new Date(before).toISOString());

    const { rows } = await store.auditList(backend);
    const hit = rows.find((r) => r.event === "dedupe_hit");
    assert.equal(hit.secretId, first.secretId);
    assert.equal(hit.detail.merged, 3);
    assert.equal(hit.detail.revoked_readd, false);
  });

  it("deduped：已有 note/sourceUrl/expiresAt 不被覆盖", async () => {
    const { backend, session } = await makeVault();
    const first = await store.add(backend, session, baseSpec({
      note: "原始备注", sourceUrl: "https://original.example", expiresAt: "2029-01-01T00:00:00.000Z",
    }));
    const second = await store.add(backend, session, baseSpec({
      note: "新备注", sourceUrl: "https://new.example", expiresAt: "2031-01-01T00:00:00.000Z",
    }));
    assert.equal(second.outcome, "deduped");
    const merged = await backend.secretGet(first.secretId);
    assert.equal(merged.note, "原始备注");
    assert.equal(merged.sourceUrl, "https://original.example");
    assert.equal(merged.expiresAt, "2029-01-01T00:00:00.000Z");
  });

  it("值为空 → rejected", async () => {
    const { backend, session } = await makeVault();
    const result = await store.add(backend, session, baseSpec({ value: "   \n\t " }));
    assert.equal(result.outcome, "rejected");
    assert.equal(result.warning.includes("值为空"), true);
  });

  it("显式重名不同值抛 AlreadyExistsError", async () => {
    const { backend, session } = await makeVault();
    await store.add(backend, session, baseSpec({ name: "alpha" }));
    await assert.rejects(
      () => store.add(backend, session, baseSpec({ value: V2, name: "alpha" })),
      (error) => {
        assert.equal(error.name, "AlreadyExistsError");
        assert.ok(error.message.includes("同名记录已存在"));
        return true;
      },
    );
  });

  it("note/sourceUrl 里塞凭据味内容被拒（save.py guard_metadata）", async () => {
    const { backend, session } = await makeVault();
    await assert.rejects(
      () => store.add(backend, session, baseSpec({ note: "remember sk-abcdefghijklmnopqrstuvwx" })),
      (error) => error.name === "RefusedError",
    );
  });
});

describe("store 自动命名（save.py:63-79 对齐）", () => {
  it("key_name 小写化、非字母数字转 -、首尾修饰符剥掉", async () => {
    const { backend, session } = await makeVault();
    const result = await store.add(backend, session, baseSpec({ keyName: "MY_SERVICE_TOKEN!!" }));
    assert.equal(result.name, "my-service-token");
  });

  it("冲突加 -2/-3 后缀", async () => {
    const { backend, session } = await makeVault();
    const first = await store.add(backend, session, baseSpec({ keyName: "API_KEY", value: V1 }));
    const second = await store.add(backend, session, baseSpec({ keyName: "API_KEY", value: V2 }));
    const third = await store.add(backend, session, baseSpec({ keyName: "API_KEY", value: V3 }));
    assert.equal(first.name, "api-key");
    assert.equal(second.name, "api-key-2");
    assert.equal(third.name, "api-key-3");
  });

  it("无 key_name 时用平台名小写", async () => {
    const { backend, session } = await makeVault();
    const result = await store.add(backend, session, baseSpec({ keyName: null, platform: "GitHub" }));
    assert.equal(result.name, "github");
  });

  it("重名大小写不敏感（对齐桌面 UNIQUE COLLATE NOCASE，评审#6 回归）", async () => {
    const { backend, session } = await makeVault();
    await store.add(backend, session, baseSpec({ value: V1, name: "api-key" }));
    // 显式重名（不同大小写）不同值 → 拒绝
    await assert.rejects(
      () => store.add(backend, session, baseSpec({ value: V2, name: "API-KEY" })),
      (error) => {
        assert.equal(error.name, "AlreadyExistsError");
        return true;
      },
    );
    // 自动命名也避开大小写变体
    const auto = await store.add(backend, session, baseSpec({ value: V2, keyName: "API-KEY" }));
    assert.equal(auto.name, "api-key-2");
    // rename 撞大小写变体 → 拒绝
    await assert.rejects(
      () => store.rename(backend, session, auto.secretId, "Api-Key"),
      /同名记录已存在/,
    );
  });
});

describe("memory-backend IBackend 契约形状", () => {
  it("secretPut 的 id 为 null/undefined 时自增分配（C2）", async () => {
    const backend = memoryBackend();
    const a = await backend.secretPut({ name: "a", sha256: "sha-a" });
    const b = await backend.secretPut({ name: "b", sha256: "sha-b", id: null });
    const c = await backend.secretPut({ name: "c", sha256: "sha-c", id: undefined });
    assert.deepEqual([a, b, c], [1, 2, 3]);
    assert.deepEqual(
      (await backend.secretAll()).map((r) => r.id).sort((x, y) => x - y),
      [1, 2, 3],
    );
  });
});

describe("store rotate（rotate.py 对齐）", () => {
  it("旧指纹 revoked+revokedAt、新指纹 active、审计含新旧 sha 前缀", async () => {
    const { backend, session } = await makeVault();
    const { secretId } = await store.add(backend, session, baseSpec());
    const oldSha = (await backend.secretGet(secretId)).sha256;

    const result = await store.rotate(backend, session, secretId, V2);
    assert.equal(result.name, "k1");
    const record = await backend.secretGet(secretId);
    assert.equal(record.sha256.length, 64);
    assert.notEqual(record.sha256, oldSha);

    const fps = await backend.fpListBySecret(secretId);
    const revoked = fps.find((f) => f.status === "revoked");
    const active = fps.find((f) => f.status === "active");
    assert.ok(revoked, "旧指纹行存在");
    assert.ok(revoked.revokedAt, "旧指纹 revokedAt 有值");
    assert.equal(revoked.sha256, oldSha);
    assert.ok(active);
    assert.equal(active.sha256, record.sha256);
    assert.equal(active.revokedAt, null);

    const { rows } = await store.auditList(backend);
    const rotateRow = rows.find((r) => r.event === "rotate");
    assert.equal(rotateRow.shaPrefix, record.sha256.slice(0, 12));
    assert.equal(rotateRow.detail.old_sha256_prefix, oldSha.slice(0, 12));
    assert.equal(rotateRow.detail.new_len, V2.length);
  });

  it("同值拒绝（rotate.py:44）", async () => {
    const { backend, session } = await makeVault();
    const { secretId } = await store.add(backend, session, baseSpec({ value: V2 }));
    await store.rotate(backend, session, secretId, V3);
    await assert.rejects(() => store.rotate(backend, session, secretId, V3), /无需轮换/);
  });

  it("新值与他人记录重复拒绝", async () => {
    const { backend, session } = await makeVault();
    const other = await store.add(backend, session, baseSpec({ value: V3, keyName: "OTHER" }));
    const mine = await store.add(backend, session, baseSpec({ value: V1 }));
    await assert.rejects(
      () => store.rotate(backend, session, mine.secretId, V3),
      (error) => {
        assert.equal(error.name, "AlreadyExistsError");
        assert.ok(error.message.includes(other.name));
        return true;
      },
    );
  });

  it("新值为空 → rejected，库不变", async () => {
    const { backend, session } = await makeVault();
    const { secretId } = await store.add(backend, session, baseSpec());
    const result = await store.rotate(backend, session, secretId, "   ");
    assert.equal(result.outcome, "rejected");
    assert.equal((await backend.secretGet(secretId)).sha256.length, 64);
  });

  it("rotate 回转旧值（A→B→A）：复用历史指纹行，sha 不重复（评审#5 回归）", async () => {
    const { backend, session } = await makeVault();
    const { secretId } = await store.add(backend, session, baseSpec({ value: V1 }));
    await store.rotate(backend, session, secretId, V2);
    await store.rotate(backend, session, secretId, V1); // 回转到历史值

    const record = await backend.secretGet(secretId);
    const fps = await backend.fpListBySecret(secretId);
    assert.equal(fps.length, 2); // 不产生第三行
    assert.equal(new Set(fps.map((f) => f.sha256)).size, 2); // sha 无重复
    const active = fps.find((f) => f.status === "active");
    const revoked = fps.find((f) => f.status === "revoked");
    assert.ok(active && revoked);
    assert.equal(active.sha256, record.sha256); // 现值 A 的指纹行活跃
    assert.equal(active.valueIv, undefined); // 其旧密文已随行清除，密文由 secret 行独占
    assert.notEqual(revoked.sha256, record.sha256); // 吊销的是 B 的历史行
    assert.ok(revoked.valueIv); // B 的旧密文仍留在吊销行（泄露证据）
  });
});

describe("store revoke/delete/rename/setPlatform 审计", () => {
  it("revoke：状态翻 revoked、活跃指纹吊销、审计含 previous_status", async () => {
    const { backend, session } = await makeVault();
    const { secretId } = await store.add(backend, session, baseSpec());
    await store.revoke(backend, session, secretId);
    const record = await backend.secretGet(secretId);
    assert.equal(record.status, "revoked");
    const fps = await backend.fpListBySecret(secretId);
    assert.equal(fps.find((f) => f.sha256 === record.sha256).status, "revoked");
    const { rows } = await store.auditList(backend);
    const row = rows.find((r) => r.event === "revoke");
    assert.equal(row.detail.previous_status, "active");
  });

  it("delete：记录消失、审计保留 name_snapshot 与 platform", async () => {
    const { backend, session } = await makeVault();
    const { secretId, name } = await store.add(backend, session, baseSpec());
    await store.deleteSecret(backend, session, secretId);
    assert.equal(await backend.secretGet(secretId), null);
    const { rows } = await store.auditList(backend);
    const row = rows.find((r) => r.event === "delete");
    assert.equal(row.nameSnapshot, name);
    assert.equal(row.detail.platform, "deepseek");
  });

  it("delete 级联删除指纹行（含轮换换下的吊销行）不留孤儿（评审#4 回归）", async () => {
    const { backend, session } = await makeVault();
    const { secretId } = await store.add(backend, session, baseSpec({ value: V1 }));
    await store.rotate(backend, session, secretId, V2); // 两条指纹行，吊销行带旧密文
    assert.equal((await backend.fpListBySecret(secretId)).length, 2);
    await store.deleteSecret(backend, session, secretId);
    assert.deepEqual(await backend.fpListBySecret(secretId), []); // 无孤儿、旧密文随之消失
  });

  it("rename：旧名进 aliases、审计 name_snapshot=新名；重名拒绝", async () => {
    const { backend, session } = await makeVault();
    const { secretId, name } = await store.add(backend, session, baseSpec());
    await store.rename(backend, session, secretId, "renamed-one");
    const record = await backend.secretGet(secretId);
    assert.equal(record.name, "renamed-one");
    assert.ok(record.aliases.includes(name));
    const { rows } = await store.auditList(backend);
    const row = rows.find((r) => r.event === "rename");
    assert.equal(row.nameSnapshot, "renamed-one");
    assert.equal(row.detail.from_len, name.length);

    const other = await store.add(backend, session, baseSpec({ value: V2, keyName: "OTHER" }));
    await assert.rejects(
      () => store.rename(backend, session, other.secretId, "renamed-one"),
      (error) => {
        assert.equal(error.name, "AlreadyExistsError");
        assert.ok(error.message.includes("同名记录已存在"));
        return true;
      },
    );
  });

  it("setPlatform：manual + 固定 evidence + 审计；remember 写纠正规则", async () => {
    const { backend, session } = await makeVault();
    const { secretId } = await store.add(backend, session, baseSpec());
    await store.setPlatform(backend, session, secretId, "deepseek", {
      remember: { kind: "keyname", pattern: "^K1$" },
    });
    const record = await backend.secretGet(secretId);
    assert.equal(record.platform, "deepseek");
    assert.equal(record.confidence, "manual");
    assert.equal(record.detectSource, "correction");
    assert.equal(record.evidence, "用户在扩展里指定");
    const corrections = await store.listCorrections(backend);
    assert.equal(corrections.length, 1);
    assert.equal(corrections[0].kind, "keyname");
    assert.equal(corrections[0].platform, "deepseek");
    const { rows } = await store.auditList(backend);
    assert.ok(rows.find((r) => r.event === "platform_set" && r.detail.platform === "deepseek"));
    assert.ok(rows.find((r) => r.event === "correction_add"));
  });

  it("所有审计行的 detail 都没有机密味键名", async () => {
    const { backend, session } = await makeVault();
    const { secretId } = await store.add(backend, session, baseSpec());
    await store.rotate(backend, session, secretId, V2);
    await store.revoke(backend, session, secretId);
    await store.rename(backend, session, secretId, "audit-check");
    await store.deleteSecret(backend, session, secretId);
    const { rows } = await store.auditList(backend);
    assert.ok(rows.length >= 5);
    for (const row of rows) {
      for (const detailKey of Object.keys(row.detail ?? {})) {
        assert.equal(
          FORBIDDEN_DETAIL_KEYS.has(detailKey.toLowerCase()), false,
          `事件 ${row.event} 的 detail 键 ${detailKey} 有机密味`,
        );
      }
    }
  });
});

describe("store 审计链（C4 公式）", () => {
  it("连续 append 后自算校验 chainOk=true，行最新在前", async () => {
    const backend = memoryBackend();
    await store.auditAppend(backend, { event: "init", actor: "test", detail: {} });
    await store.auditAppend(backend, { event: "save", actor: "test", secretId: 1, nameSnapshot: "a", shaPrefix: "abcdef012345", detail: { len: 5 } });
    await store.auditAppend(backend, { event: "reveal", actor: "test", secretId: 1, nameSnapshot: "a", detail: {} });

    const { rows, chainOk, firstBadId } = await store.auditList(backend);
    assert.equal(chainOk, true);
    assert.equal(firstBadId, null);
    assert.equal(rows.length, 3);
    assert.equal(rows[0].event, "reveal"); // 最新在前
    assert.equal(rows[2].event, "init");
  });

  it("超长 shaPrefix 截到 12 位", async () => {
    const backend = memoryBackend();
    await store.auditAppend(backend, {
      event: "save", actor: "test",
      shaPrefix: "a".repeat(64), detail: {},
    });
    const { rows } = await store.auditList(backend);
    assert.equal(rows[0].shaPrefix.length, 12);
  });

  it("篡改一行 detail 后 firstBadId 指向该行（等价桌面 DROP TRIGGER 改行）", async () => {
    const backend = memoryBackend();
    await store.auditAppend(backend, { event: "init", actor: "test", detail: {} });
    await store.auditAppend(backend, { event: "save", actor: "test", detail: { len: 3 } });
    await store.auditAppend(backend, { event: "reveal", actor: "test", detail: {} });

    const tamperedId = backend._audit[1].id;
    backend._audit[1].detail.len = 999;

    const { chainOk, firstBadId } = await store.auditList(backend);
    assert.equal(chainOk, false);
    assert.equal(firstBadId, tamperedId);
  });

  it("机密味键名直接拒绝进审计", async () => {
    const backend = memoryBackend();
    await assert.rejects(
      () => store.auditAppend(backend, { event: "save", actor: "test", detail: { token: "x" } }),
      /机密味/,
    );
  });

  it("并发 append 串行化：链不断（评审#3 探针回归）", async () => {
    const backend = memoryBackend();
    await Promise.all([
      store.auditAppend(backend, { event: "save", actor: "test", secretId: 1, nameSnapshot: "a", detail: {} }),
      store.auditAppend(backend, { event: "reveal", actor: "test", secretId: 1, nameSnapshot: "a", detail: {} }),
      store.auditAppend(backend, { event: "copy", actor: "test", secretId: 1, nameSnapshot: "a", detail: {} }),
    ]);
    const { chainOk, firstBadId, rows } = await store.auditList(backend);
    assert.equal(chainOk, true);
    assert.equal(firstBadId, null);
    assert.equal(rows.length, 3);
    // 每行的 prevHash 都接到时间序前一行（内存后端最新在前：rows[1] 是首行）
    const stored = [...backend._audit].sort((a, b) => a.id - b.id);
    assert.equal(stored[0].prevHash, "");
    assert.equal(stored[1].prevHash, stored[0].hash);
    assert.equal(stored[2].prevHash, stored[1].hash);
  });

  it("单个 append 失败不毒化队列：后续 append 仍接对链", async () => {
    const backend = memoryBackend();
    await assert.rejects(
      () => store.auditAppend(backend, { event: "save", actor: "test", detail: { token: "x" } }),
    );
    await store.auditAppend(backend, { event: "init", actor: "test", detail: {} });
    const { chainOk } = await store.auditList(backend);
    assert.equal(chainOk, true);
  });
});

describe("store 备份 KVBE1 往返（C5）", () => {
  const BACKUP_PASS = "backup-pass-123";

  async function makeSourceVault() {
    const { backend, session } = await makeVault();
    await store.add(backend, session, baseSpec({
      value: V1, keyName: "K1", tags: ["team-a", "prod"],
      note: "主库记录", sourceUrl: "https://example.com/one",
    }));
    await store.add(backend, session, baseSpec({
      value: V2, keyName: "K2", platform: "github", confidence: "manual",
    }));
    await store.add(backend, session, baseSpec({
      value: V3, keyName: "K3", platform: "amap", expiresAt: "2030-06-01T00:00:00.000Z",
    }));
    return { backend, session };
  }

  it("导出 blob 格式正确、payload 不含 valueIv/valueCt 密文字段", async () => {
    const { backend, session } = await makeSourceVault();
    const blob = await store.exportBackup(backend, session, BACKUP_PASS);
    const parts = blob.split("|");
    assert.equal(parts[0], "KVBE1");
    assert.equal(b64ToBytes(parts[1]).length, 16); // 盐 16B
    assert.equal(b64ToBytes(parts[2]).length, 12); // IV 12B

    const key = await importRawKey(await deriveRawKey(BACKUP_PASS, b64ToBytes(parts[1]), 600000));
    const payload = JSON.parse(await decryptText(key, {
      iv: b64ToBytes(parts[2]), ct: b64ToBytes(parts[3]),
    }));
    assert.equal(payload.version, 1);
    assert.equal(typeof payload.exportedAt, "string");
    assert.equal(payload.records.length, 3);
    for (const record of payload.records) {
      assert.equal("valueIv" in record, false);
      assert.equal("valueCt" in record, false);
      assert.equal(typeof record.value, "string");
      assert.equal(typeof record.name, "string");
    }
    const { rows } = await store.auditList(backend);
    const exportRow = rows.find((r) => r.event === "export");
    assert.equal(exportRow.detail.count, 3);
  });

  it("口令 <8 位导出被拒；锁定态导出被拒", async () => {
    const { backend, session } = await makeSourceVault();
    await assert.rejects(() => store.exportBackup(backend, session, "short"), /至少 8 位/);
    const locked = new Session(memoryKeyStore());
    await assert.rejects(() => store.exportBackup(backend, locked, BACKUP_PASS), { name: "LockedError" });
  });

  it("导出→重建空库→restore：created 计数、平台与标签还原、明文一致", async () => {
    const source = await makeSourceVault();
    const blob = await store.exportBackup(source.backend, source.session, BACKUP_PASS);

    const target = await makeVault();
    const summary = await store.restoreBackup(target.backend, target.session, BACKUP_PASS, blob);
    assert.deepEqual(summary, { created: 3, deduped: 0, rejected: 0 });

    const rows = await store.listSecrets(target.backend);
    assert.equal(rows.length, 3);
    const byName = new Map(rows.map((r) => [r.name, r]));
    const first = byName.get("k1");
    assert.equal(first.platform, "deepseek");
    assert.deepEqual([...first.tags].sort(), ["prod", "team-a"]);
    assert.equal(first.note, "主库记录");
    assert.equal(first.sourceUrl, "https://example.com/one");
    assert.equal(byName.get("k2").platform, "github");
    assert.equal(byName.get("k3").expiresAt, "2030-06-01T00:00:00.000Z");

    const revealed = await store.reveal(target.backend, target.session, first.id);
    assert.equal(revealed.value, V1);

    const { rows: auditRows } = await store.auditList(target.backend);
    assert.ok(auditRows.find((r) => r.event === "restore"));
  });

  it("同一备份恢复两次 → 第二次全 deduped（去重生效）", async () => {
    const source = await makeSourceVault();
    const blob = await store.exportBackup(source.backend, source.session, BACKUP_PASS);
    const target = await makeVault();
    await store.restoreBackup(target.backend, target.session, BACKUP_PASS, blob);
    const again = await store.restoreBackup(target.backend, target.session, BACKUP_PASS, blob);
    assert.deepEqual(again, { created: 0, deduped: 3, rejected: 0 });
  });

  it("错口令恢复报错且不落库", async () => {
    const source = await makeSourceVault();
    const blob = await store.exportBackup(source.backend, source.session, BACKUP_PASS);
    const target = await makeVault();
    await assert.rejects(
      () => store.restoreBackup(target.backend, target.session, "wrong-pass-99", blob),
      /口令错误或备份已损坏/,
    );
    assert.equal((await target.backend.secretAll()).length, 0);
  });

  it("非 KVBE1 格式拒绝", async () => {
    const { backend, session } = await makeVault();
    await assert.rejects(
      () => store.restoreBackup(backend, session, BACKUP_PASS, "KVBK1|xx|yy|zz"),
      /KVBE1/,
    );
  });
});

describe("store destroyVault（C1#30 存储侧）", () => {
  it("正确主密码 → 后端清空、isInitialized=false、会话锁定", async () => {
    const { backend, session } = await makeVault();
    const { secretId } = await store.add(backend, session, baseSpec());
    await store.addCorrection(backend, { kind: "keyname", pattern: "^K1$", platform: "deepseek" });
    await store.destroyVault(backend, session, MASTER);
    assert.equal(await store.isInitialized(backend), false);
    assert.equal(await backend.metaGet("vault-meta"), null);
    assert.equal((await backend.secretAll()).length, 0);
    assert.equal(await backend.secretGet(secretId), null);
    assert.equal((await backend.auditList(100)).length, 0);
    assert.equal((await backend.corrAll()).length, 0);
    assert.equal(await session.isUnlocked(), false);
  });

  it("错口令抛错且数据完好", async () => {
    const { backend, session } = await makeVault();
    await store.add(backend, session, baseSpec());
    await assert.rejects(() => store.destroyVault(backend, session, "wrong-pass-9"), /主密码不正确/);
    assert.equal(await store.isInitialized(backend), true);
    assert.equal((await backend.secretAll()).length, 1);
  });

  it("未初始化库抛错", async () => {
    const backend = memoryBackend();
    await assert.rejects(
      () => store.destroyVault(backend, new Session(memoryKeyStore()), MASTER),
      /未初始化/,
    );
  });
});

describe("store importText（经 detect.js，C4/C9#2）", () => {
  it("两行可信 .env 入库 2 条", async () => {
    const { backend, session } = await makeVault();
    const env36 = "abcdefghij0123456789".repeat(2).slice(0, 36);
    const text = `DEEPSEEK_API_KEY=sk-${"A".repeat(48)}\nGITHUB_TOKEN=ghp_${env36}`;
    const summary = await store.importText(backend, session, text, { tags: ["imported"] });
    assert.equal(summary.created, 2);
    assert.equal(summary.deduped, 0);
    assert.equal(summary.skipped.length, 0);
    const rows = await store.listSecrets(backend);
    assert.equal(rows.length, 2);
    assert.deepEqual([...rows[0].tags], ["imported"]);
    const platforms = rows.map((r) => r.platform).sort();
    assert.deepEqual(platforms, ["deepseek", "github"]);
  });

  it("裸 sk-（歧义→promptable）入库、hex-32（none）进 skipped，skipped 不含值本身", async () => {
    const { backend, session } = await makeVault();
    const skValue = `sk-${"A".repeat(48)}`;
    const hexValue = "0123456789abcdef0123456789abcdef";
    const summary = await store.importText(backend, session, `${skValue}\n${hexValue}`);

    // C9#1：ambiguous 是 promptable 全集成员 → 有入库路径，不在 skipped
    assert.equal(summary.created, 1);
    assert.equal(summary.skipped.length, 1);
    const skipped = summary.skipped[0];
    assert.equal(skipped.platform, "unknown");
    assert.equal(skipped.confidence, "none");
    assert.equal(skipped.preview.includes("*"), true);
    assert.equal(skipped.preview, `****${hexValue.slice(-4)}`);
    assert.equal(JSON.stringify(summary).includes(skValue), false);
    assert.equal(JSON.stringify(summary).includes(hexValue), false);

    const rows = await store.listSecrets(backend);
    assert.equal(rows.length, 1);
    assert.equal(rows[0].platform, "openai-compatible");
  });

  it("重复导入同一 .env → deduped 计数", async () => {
    const { backend, session } = await makeVault();
    const env36 = "abcdefghij0123456789".repeat(2).slice(0, 36);
    const text = `DEEPSEEK_API_KEY=sk-${"A".repeat(48)}\nGITHUB_TOKEN=ghp_${env36}`;
    await store.importText(backend, session, text);
    const again = await store.importText(backend, session, text);
    assert.equal(again.created, 0);
    assert.equal(again.deduped, 2);
  });

  it("每批审计一条 import，detail 含计数", async () => {
    const { backend, session } = await makeVault();
    await store.importText(backend, session, `DEEPSEEK_API_KEY=sk-${"A".repeat(48)}`);
    const { rows } = await store.auditList(backend);
    const importRow = rows.find((r) => r.event === "import");
    assert.ok(importRow);
    assert.equal(importRow.detail.created, 1);
    assert.equal(importRow.detail.total_candidates, 1);
    assert.equal(importRow.detail.source, "import");
  });
});

describe("store markExpired 与 stats", () => {
  it("过期标记：只翻已过且 active 的记录，审计 expire", async () => {
    const { backend, session } = await makeVault();
    await store.add(backend, session, baseSpec({ value: V1, expiresAt: "2020-01-01T00:00:00.000Z" }));
    await store.add(backend, session, baseSpec({ value: V2, keyName: "K2", expiresAt: "2099-01-01T00:00:00.000Z" }));
    await store.add(backend, session, baseSpec({ value: V3, keyName: "K3" }));
    const { flipped } = await store.markExpired(backend);
    assert.equal(flipped, 1);
    const rows = await store.listSecrets(backend);
    const statuses = new Map(rows.map((r) => [r.name, r.status]));
    assert.equal(statuses.get("k1"), "expired");
    assert.equal(statuses.get("k2"), "active");
    assert.equal(statuses.get("k3"), "active");
    const { rows: auditRows } = await store.auditList(backend);
    assert.ok(auditRows.find((r) => r.event === "expire"));
  });

  it("stats 汇总 total/active/flagged/expiring/platforms", async () => {
    const { backend, session } = await makeVault();
    await store.add(backend, session, baseSpec({ value: V1, expiresAt: new Date(Date.now() + 86400000).toISOString() }));
    await store.add(backend, session, baseSpec({ value: V2, keyName: "K2", platform: "github" }));
    await store.revoke(backend, session, (await store.listSecrets(backend))[0].id);
    const summary = await store.stats(backend);
    assert.equal(summary.total, 2);
    assert.equal(summary.active, 1);
    assert.equal(summary.flagged, 1);
    assert.equal(summary.expiring, 1); // k1 活跃且 24h 后过期（30 天窗内）
    // 被吊销的是后加的 k2(github)；平台计数只统计 active → 剩 deepseek
    assert.deepEqual(summary.platforms, [{ platform: "deepseek", count: 1 }]);
  });
});

describe("store listSecrets（C1#7）", () => {
  it("排序 createdAt DESC 再 id DESC；行不含明文/密文字段", async () => {
    const { backend, session } = await makeVault();
    const a = await store.add(backend, session, baseSpec({ value: V1 }));
    const b = await store.add(backend, session, baseSpec({ value: V2, keyName: "K2" }));
    const rows = await store.listSecrets(backend);
    // b 晚于 a 创建：createdAt 更大；同毫秒时 id 更大——两种情况都该排前面
    assert.deepEqual(rows.map((r) => r.id), [b.secretId, a.secretId]);
    for (const row of rows) {
      assert.equal("valueIv" in row, false);
      assert.equal("valueCt" in row, false);
      assert.equal("value" in row, false);
    }
  });

  it("搜索覆盖 name/platform/note/keyName/sourceUrl/aliases，不含 tags（C9#5）", async () => {
    const { backend, session } = await makeVault();
    const rec = await store.add(backend, session, baseSpec({
      note: "生产环境网关", sourceUrl: "https://gw.example.com", tags: ["zigbah-tag"],
    }));
    await store.rename(backend, session, rec.secretId, "gateway-main");
    const search = async (query) => (await store.listSecrets(backend, { query })).map((r) => r.id);
    assert.equal((await search("gateway-main")).length, 1); // 新名
    assert.equal((await search("k1")).length, 1); // keyName
    assert.equal((await search("deepseek")).length, 1); // platform
    assert.equal((await search("生产环境网关")).length, 1); // note
    assert.equal((await search("gw.example.com")).length, 1); // sourceUrl
    assert.equal((await search("k1")).length, 1);
    const oldNameHits = await search("K1".toLowerCase());
    assert.equal(oldNameHits.length, 1); // 旧名现在在 aliases 里，也参与搜索
    assert.equal((await search("zigbah-tag")).length, 0); // tags 不参与搜索
  });

  it("platform/status 过滤与大小写不敏感子串", async () => {
    const { backend, session } = await makeVault();
    await store.add(backend, session, baseSpec({ value: V1, platform: "deepseek" }));
    await store.add(backend, session, baseSpec({ value: V2, keyName: "K2", platform: "github" }));
    await store.revoke(backend, session, (await store.listSecrets(backend, { query: "k2" }))[0].id);
    assert.equal((await store.listSecrets(backend, { platform: "github" })).length, 1);
    assert.equal((await store.listSecrets(backend, { status: "revoked" })).length, 1);
    assert.equal((await store.listSecrets(backend, { query: "DEEPSEEK" })).length, 1);
  });
});
