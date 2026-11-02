// vault 加密层：PBKDF2/AES-GCM 往返、错口令、篡改、会话锁定语义。
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  deriveRawKey, importRawKey, encryptText, decryptText, encryptBytes, decryptBytes,
  Session, memoryKeyStore, bytesToB64, b64ToBytes, randomBytes,
} from "../src/vault/crypto.js";

test("加密/解密往返（同一密钥）", async () => {
  const raw = await deriveRawKey("master-password-123", randomBytes(16));
  const key = await importRawKey(raw);
  const box = await encryptText(key, "sk-abc123_def456ghi789jklmno");
  assert.equal(await decryptText(key, box), "sk-abc123_def456ghi789jklmno");
  // 每次加密 IV 不同
  const box2 = await encryptText(key, "sk-abc123_def456ghi789jklmno");
  assert.notEqual(bytesToB64(box.iv), bytesToB64(box2.iv));
});

test("口令不同 → 派生密钥不同 → GCM 认证失败（不是静默错解）", async () => {
  const salt = randomBytes(16);
  const key1 = await importRawKey(await deriveRawKey("correct-horse", salt));
  const key2 = await importRawKey(await deriveRawKey("wrong-battery", salt));
  const box = await encryptText(key1, "secret");
  await assert.rejects(() => decryptText(key2, box));
});

test("密文篡改 → 解密失败", async () => {
  const key = await importRawKey(await deriveRawKey("pw", randomBytes(16)));
  const box = await encryptText(key, "secret");
  const ct = b64ToBytes(bytesToB64(box.ct));
  ct[0] ^= 0x01;
  await assert.rejects(() => decryptText(key, { iv: box.iv, ct }));
});

test("迭代次数差异 → 派生不同（KDF 参数是事实的一部分）", async () => {
  const salt = randomBytes(16);
  const a = await deriveRawKey("pw", salt, 600000);
  const b = await deriveRawKey("pw", salt, 100000);
  assert.notEqual(bytesToB64(a), bytesToB64(b));
});

test("Session：未锁定 key() 抛 LockedError；saveRawKey 后可用；lock 后失效", async () => {
  const session = new Session(memoryKeyStore());
  await assert.rejects(() => session.key(), (e) => e.name === "LockedError");
  assert.equal(await session.isUnlocked(), false);

  const raw = await deriveRawKey("pw", randomBytes(16));
  await session.saveRawKey(raw);
  assert.equal(await session.isUnlocked(), true);
  const key = await session.key();
  const box = await encryptBytes(key, new TextEncoder().encode("roundtrip"));
  assert.equal(new TextDecoder().decode(await decryptBytes(await session.key(), box)), "roundtrip");

  await session.lock();
  assert.equal(await session.isUnlocked(), false);
  await assert.rejects(() => session.key(), (e) => e.name === "LockedError");
});

test("会话密钥跨 SW 重启存活（keyStore 持久于会话）", async () => {
  const store = memoryKeyStore();
  const session1 = new Session(store);
  await session1.saveRawKey(await deriveRawKey("pw", randomBytes(16)));

  const session2 = new Session(store); // 模拟新 SW 实例
  assert.equal(await session2.isUnlocked(), true);
  const key = await session2.key();
  const box = await encryptText(key, "still-unlocked");
  assert.equal(await decryptText(await session2.key(), box), "still-unlocked");
});
