// TOTP 验证码生成（RFC 6238）—— kv/ops/totp.py 的网页版。
//
// 种子就是记录的值（base32，验证器「显示密钥」里复制的那串）。SHA1 / 6 位 /
// 30 秒是 RFC 默认，也是各家验证器的兼容基线——不开参数面，generate 只留
// digits 给 RFC 测试向量用。与桌面版唯一的实现差异：桌面用 hashlib+hmac
// （同步），浏览器走 WebCrypto 的 HMAC（异步），算法与动态截断逐位一致。
// 正确性由 tests/totp.test.js 用 RFC 6238 附录 B SHA1 官方向量对拍保证
// （桌面 tests/test_ops_totp.py 同源）。

export const PERIOD = 30;
export const DIGITS = 6;

function subtle() {
  if (!globalThis.crypto?.subtle) {
    throw new Error("WebCrypto 不可用（需要安全上下文）");
  }
  return globalThis.crypto.subtle;
}

const B32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

/** base32 解码失败。值不是合法的 base32 TOTP 种子。 */
export class SeedError extends Error {
  constructor(message = "不是合法的 base32 种子") {
    super(message);
    this.name = "SeedError";
  }
}

/** 宽容解析 base32：大小写不敏感、忽略空格与连字符、容忍已有 padding。 */
export function parseSeed(raw) {
  const cleaned = String(raw ?? "")
    .split(/\s+/).join("")
    .split("-").join("")
    .split("=").join("")
    .toUpperCase();
  if (!cleaned) throw new SeedError("种子为空");
  // RFC 4648：合法的 base32 长度 mod 8 ∈ {0,2,4,5,7}；{1,3,6} 的尾组位数不够
  const remainder = cleaned.length % 8;
  if (remainder === 1 || remainder === 3 || remainder === 6) {
    throw new SeedError("不是合法的 base32 种子");
  }
  const bytes = [];
  let bitBuffer = 0;
  let bitCount = 0;
  for (const ch of cleaned) {
    const index = B32_ALPHABET.indexOf(ch);
    if (index < 0) throw new SeedError("不是合法的 base32 种子");
    bitBuffer = (bitBuffer << 5) | index;
    bitCount += 5;
    if (bitCount >= 8) {
      bytes.push((bitBuffer >>> (bitCount - 8)) & 0xff);
      bitCount -= 8;
    }
  }
  return new Uint8Array(bytes);
}

/** RFC 4231 风格的 8 字节大端计数器（struct.pack(">Q", counter) 的等价物）。 */
function counterBytes(counter) {
  const bytes = new Uint8Array(8);
  new DataView(bytes.buffer).setBigUint64(0, BigInt(counter));
  return bytes;
}

/** 生成 (验证码, 剩余秒数)。now 接受 Date 或 epoch 毫秒。
 * counter 按 RFC 6238 取 UTC 时间步；动态截断与桌面 totp.py 逐位一致。 */
export async function generate(seed, now = new Date(), { digits = DIGITS } = {}) {
  const milliseconds = now instanceof Date ? now.getTime() : Number(now);
  const timestamp = Math.floor(milliseconds / 1000);
  const counter = Math.floor(timestamp / PERIOD);
  const key = await subtle().importKey(
    "raw", seed, { name: "HMAC", hash: "SHA-1" }, false, ["sign"]);
  const digest = new Uint8Array(await subtle().sign("HMAC", key, counterBytes(counter)));
  const offset = digest[digest.length - 1] & 0x0f;
  const binary = ((digest[offset] & 0x7f) << 24)
    | (digest[offset + 1] << 16)
    | (digest[offset + 2] << 8)
    | digest[offset + 3];
  const code = binary % 10 ** digits;
  return {
    code: String(code).padStart(digits, "0"),
    remaining: PERIOD - (timestamp % PERIOD),
  };
}
