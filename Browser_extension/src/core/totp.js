// TOTP（RFC 6238）—— 与桌面版 kv/ops/totp.py 对齐：SHA1 / 6 位 / 30 秒兼容基线，
// generate 留 digits 给 RFC 附录 B 官方向量。种子就是记录的值（base32）。

const PERIOD = 30;

export class SeedError extends Error {
  constructor(message = "不是合法的 base32 种子") {
    super(message);
    this.name = "SeedError";
  }
}

const B32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"; // RFC 4648 base32（注意：不是 atob 的 base64！）

/** 宽容解析 base32：大小写不敏感、忽略空格/连字符/padding。 */
export function parseSeed(raw) {
  const cleaned = raw.replace(/\s+/g, "").replace(/[-=]/g, "").toUpperCase();
  if (!cleaned) throw new SeedError("种子为空");
  const out = [];
  let bits = 0;
  let value = 0;
  for (const ch of cleaned) {
    const index = B32_ALPHABET.indexOf(ch);
    if (index < 0) throw new SeedError();
    value = (value << 5) | index;
    bits += 5;
    if (bits >= 8) {
      out.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  if (out.length === 0) throw new SeedError();
  return Uint8Array.from(out);
}

function* counterBytes(counter) {
  // RFC 4226：8 字节大端 counter（JS 位运算只有 32 位，高 32 位直接平移）
  const high = Math.floor(counter / 0x100000000);
  yield (high >>> 24) & 0xff;
  yield (high >>> 16) & 0xff;
  yield (high >>> 8) & 0xff;
  yield high & 0xff;
  const low = counter >>> 0;
  yield (low >>> 24) & 0xff;
  yield (low >>> 16) & 0xff;
  yield (low >>> 8) & 0xff;
  yield low & 0xff;
}

async function hmacSha1(seedBytes, counter) {
  const key = await crypto.subtle.importKey(
    "raw",
    seedBytes.buffer.slice(seedBytes.byteOffset, seedBytes.byteOffset + seedBytes.byteLength),
    { name: "HMAC", hash: "SHA-1" }, false, ["sign"]);
  const message = Uint8Array.from(counterBytes(counter));
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, message));
}

/** (验证码, 剩余秒数)。now 需 Date 可用时间戳；counter 按 RFC 取 UTC 时间步。 */
export async function generate(seedBytes, now, { digits = 6, period = PERIOD } = {}) {
  const timestamp = Math.floor(now.getTime() / 1000);
  const counter = Math.floor(timestamp / period);
  const digest = await hmacSha1(seedBytes, counter);
  const offset = digest[digest.length - 1] & 0x0f;
  const view = new DataView(digest.buffer, digest.byteOffset + offset, 4);
  const code = (view.getUint32(0) & 0x7fffffff) % 10 ** digits;
  return [String(code).padStart(digits, "0"), period - (timestamp % period)];
}

export { PERIOD };
