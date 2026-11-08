// crypto.js 单测：WebCrypto PBKDF2-SHA256（600k）派生 + AES-GCM-256 往返 +
// Session 状态机。篡改密文必须表现为 OperationError（调用方据此翻译成
// 「口令错误或已损坏」）；Session.lock() 后 key() 抛 LockedError。

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  randomBytes, bytesToB64, b64ToBytes, deriveRawKey, importRawKey,
  encryptBytes, decryptBytes, encryptText, decryptText,
  Session, memoryKeyStore, LockedError, KDF_ITERATIONS, IV_BYTES,
} from "../src/vault/crypto.js";

describe("crypto 随机与编码", () => {
  it("randomBytes 长度正确且两次不同", () => {
    const a = randomBytes(16);
    const b = randomBytes(16);
    assert.equal(a.length, 16);
    assert.notDeepEqual([...a], [...b]);
  });

  it("bytesToB64/b64ToBytes 往返，含空字节与中文", () => {
    const bytes = new Uint8Array([0, 1, 2, 250, 251, 0x00, 0xff]);
    assert.deepEqual([...b64ToBytes(bytesToB64(bytes))], [...bytes]);
    const text = new TextEncoder().encode("中文值\x00尾");
    assert.deepEqual([...b64ToBytes(bytesToB64(text))], [...text]);
  });
});

describe("crypto 派生与加解密", () => {
  it("deriveRawKey：600000 轮派生 32 字节，同盐确定、异盐不同", async () => {
    const salt = randomBytes(16);
    const k1 = await deriveRawKey("主密码-测试", salt);
    const k2 = await deriveRawKey("主密码-测试", salt);
    const k3 = await deriveRawKey("主密码-测试", randomBytes(16));
    const k4 = await deriveRawKey("另一个密码", salt);
    assert.equal(k1.length, 32);
    assert.deepEqual([...k1], [...k2]);
    assert.notDeepEqual([...k1], [...k3]);
    assert.notDeepEqual([...k1], [...k4]);
  });

  it("encryptBytes/decryptBytes 往返；iv 12 字节", async () => {
    const key = await importRawKey(await deriveRawKey("round-trip-pw", randomBytes(16)));
    const plain = new TextEncoder().encode("hello \x00 world 中文");
    const box = await encryptBytes(key, plain);
    assert.equal(box.iv.length, IV_BYTES);
    assert.deepEqual([...(await decryptBytes(key, box))], [...plain]);
  });

  it("篡改密文解密失败，OperationError", async () => {
    const key = await importRawKey(await deriveRawKey("tamper-pw", randomBytes(16)));
    const box = await encryptBytes(key, new TextEncoder().encode("sensitive-value"));
    const broken = { iv: box.iv, ct: new Uint8Array(box.ct) };
    broken.ct[0] ^= 0x01; // 翻转一位密文
    await assert.rejects(() => decryptBytes(key, broken), (error) => {
      assert.equal(error.name, "OperationError");
      return true;
    });
  });

  it("错密钥解密同样 OperationError（口令错误的形态）", async () => {
    const good = await importRawKey(await deriveRawKey("pw-one-11", randomBytes(16)));
    const bad = await importRawKey(await deriveRawKey("pw-two-22", randomBytes(16)));
    const box = await encryptText(good, "secret");
    await assert.rejects(() => decryptText(bad, box), { name: "OperationError" });
  });

  it("encryptText/decryptText 往返（中文/空串内容）", async () => {
    const key = await importRawKey(await deriveRawKey("text-pw-123", randomBytes(16)));
    assert.equal(await decryptText(key, await encryptText(key, "中文密文 value")), "中文密文 value");
  });

  it("encryptBytes 每次新 IV：同明文两次密文不同", async () => {
    const key = await importRawKey(await deriveRawKey("iv-pw-1234", randomBytes(16)));
    const box1 = await encryptBytes(key, new TextEncoder().encode("same"));
    const box2 = await encryptBytes(key, new TextEncoder().encode("same"));
    assert.notDeepEqual([...box1.ct], [...box2.ct]);
    assert.notDeepEqual([...box1.iv], [...box2.iv]);
  });

  it("KDF_ITERATIONS 契约常量 = 600000", () => {
    assert.equal(KDF_ITERATIONS, 600000);
  });
});

describe("crypto Session 状态机", () => {
  it("未解锁：isUnlocked=false，key() 抛 LockedError", async () => {
    const session = new Session(memoryKeyStore());
    assert.equal(await session.isUnlocked(), false);
    await assert.rejects(() => session.key(), LockedError);
  });

  it("saveRawKey 后可取 key，lock 后 key() 抛 LockedError", async () => {
    const session = new Session(memoryKeyStore());
    const raw = await deriveRawKey("session-pw-1", randomBytes(16));
    await session.saveRawKey(raw);
    assert.equal(await session.isUnlocked(), true);
    const key = await session.key();
    assert.equal(key instanceof CryptoKey, true);
    await session.lock();
    assert.equal(await session.isUnlocked(), false);
    await assert.rejects(() => session.key(), (error) => {
      assert.equal(error instanceof LockedError, true);
      assert.equal(error.name, "LockedError");
      assert.equal(error.message, "vault 已锁定——先解锁");
      return true;
    });
  });

  it("keyStore 换实例即换会话：新 Session 不继承密钥", async () => {
    const first = new Session(memoryKeyStore());
    await first.saveRawKey(await deriveRawKey("iso-pw-1234", randomBytes(16)));
    const second = new Session(memoryKeyStore());
    assert.equal(await second.isUnlocked(), false);
  });

  it("key() 复用缓存：两次调用同一把 CryptoKey", async () => {
    const session = new Session(memoryKeyStore());
    await session.saveRawKey(await deriveRawKey("cache-pw-1", randomBytes(16)));
    assert.equal(await session.key(), await session.key());
  });
});
