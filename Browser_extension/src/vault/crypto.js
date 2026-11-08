// vault 加密层 —— WebCrypto：PBKDF2(SHA-256, 600k) 派生 AES-GCM-256。
//
// 与桌面版的对位：kv/crypto/dpapi.py 的 DPAPI 不可移植（浏览器沙箱无 ctypes），
// 主密码派生 + 认证加密正是桌面 .kvb 备份（kv/ops/backup.py）的同款信任模型，
// 只是浏览器把这套用在常态加密上——DPAPI 里「当前 Windows 账户」的角色由
// 浏览器配置文件身份承担。零外部依赖，加解密全走 globalThis.crypto。
//
// keyStore 抽象：解锁后的**原始密钥字节**只存调用方注入的 keyStore
// （MV3 = chrome.storage.session——浏览器会话结束即清空，锁定=删除；
// 测试 = 内存 Map，见 memoryKeyStore()）。本文件不 import chrome.*。

export const KDF_ITERATIONS = 600000;
export const KDF_SALT_BYTES = 16;
export const KEY_BITS = 256;
export const IV_BYTES = 12;

const te = new TextEncoder();
const td = new TextDecoder();

function subtle() {
  if (!globalThis.crypto?.subtle) {
    throw new Error("WebCrypto 不可用（需要安全上下文）");
  }
  return globalThis.crypto.subtle;
}

/** 未解锁就碰密钥的统一错误。background 按 name 判定并翻译成 {locked:true}。 */
export class LockedError extends Error {
  constructor() {
    super("vault 已锁定——先解锁");
    this.name = "LockedError";
  }
}

export function randomBytes(n) {
  const bytes = new Uint8Array(n);
  globalThis.crypto.getRandomValues(bytes);
  return bytes;
}

export function bytesToB64(bytes) {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}

export function b64ToBytes(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

/** PBKDF2-SHA256 派生 32 字节原始密钥（600 000 轮、盐 16B 由调用方给）。 */
export async function deriveRawKey(password, saltBytes, iterations = KDF_ITERATIONS) {
  const baseKey = await subtle().importKey(
    "raw", te.encode(password), "PBKDF2", false, ["deriveBits"]);
  const bits = await subtle().deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt: saltBytes, iterations },
    baseKey, KEY_BITS);
  return new Uint8Array(bits);
}

export async function importRawKey(rawKey) {
  return subtle().importKey("raw", rawKey, { name: "AES-GCM" }, false,
    ["encrypt", "decrypt"]);
}

/** 加密一条记录。返回 {iv, ct}（Uint8Array，可直接进 IndexedDB）。 */
export async function encryptBytes(key, plaintextBytes) {
  const iv = randomBytes(IV_BYTES);
  const ct = new Uint8Array(await subtle().encrypt(
    { name: "AES-GCM", iv }, key, plaintextBytes));
  return { iv, ct };
}

/** 解密。口令错误/篡改都表现为 WebCrypto 的 OperationError，由调用方翻译。 */
export async function decryptBytes(key, { iv, ct }) {
  const plain = await subtle().decrypt({ name: "AES-GCM", iv }, key, ct);
  return new Uint8Array(plain);
}

export async function encryptText(key, text) {
  return encryptBytes(key, te.encode(text));
}

export async function decryptText(key, box) {
  return td.decode(await decryptBytes(key, box));
}

// ---------------------------------------------------------------- 会话状态机

const SESSION_KEY = "kv-vault-raw-key";

/** 解锁后的密钥保存在哪里。SW 传 chrome.storage.session 的壳；测试传内存 Map。 */
export function memoryKeyStore() {
  const map = new Map();
  return {
    async get(k) { return map.get(k) ?? null; },
    async set(k, v) { map.set(k, v); },
    async remove(k) { map.delete(k); },
  };
}

export class Session {
  constructor(keyStore) {
    this.keyStore = keyStore;
    this._key = null; // CryptoKey（SW 生命周期内复用，省一次 importKey）
  }

  /** 会话里还有已解锁的密钥吗（有 raw bytes 就能重建 CryptoKey）。 */
  async isUnlocked() {
    return (await this.keyStore.get(SESSION_KEY)) !== null;
  }

  /** 解锁成功后调用：原始密钥字节进会话内存。 */
  async saveRawKey(rawKey) {
    await this.keyStore.set(SESSION_KEY, bytesToB64(rawKey));
    this._key = null;
  }

  /** 锁定：会话密钥删除，CryptoKey 缓存作废。 */
  async lock() {
    await this.keyStore.remove(SESSION_KEY);
    this._key = null;
  }

  /** 当前 AES-GCM CryptoKey；未解锁抛 LockedError。 */
  async key() {
    if (this._key) return this._key;
    const raw = await this.keyStore.get(SESSION_KEY);
    if (!raw) throw new LockedError();
    this._key = await importRawKey(b64ToBytes(raw));
    return this._key;
  }
}
