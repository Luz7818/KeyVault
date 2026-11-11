// 密码生成器 —— crypto.getRandomValues + 拒绝采样（无模偏差）。
//
// 采样纪律：WebCrypto 只给无偏的 32 位随机数；把随机数对池大小取模会引入
// 模偏差（2^32 % N ≠ 0 时排在前面的字符多出现一次）——拒绝采样：先算
// limit = floor(2^32 / N) * N，随机数 ≥ limit 丢弃重取。Bitwarden / 1Password
// 同款做法；偏差采样的正确性由 tests/generator.test.js 用边界池（N 不整除
// 2^32）钉死。
//
// 每个启用字符类至少出现一枚：先各类各取一枚放进随机位，余位从全池采样，
// 最后 Fisher-Yates 洗牌（索引同样拒绝采样）——「启用大写却整串没有大写」
// 对用户是不可理解的输出，宁可在熵上让出一点点。
//
// 熵粗估 = length × log2(池大小)：由于「至少一枚/类」约束，真实熵略低于该
// 值；界面按「粗估」口径展示，不做精确熵。默认长度 20：NIST SP 800-63B 的
// 随机密码建议 ≥15，留余量（综合路线图 §3.1#4）。

const UINT32_SPACE = 0x100000000;

export const LENGTH_MIN = 8;
export const LENGTH_MAX = 128;

export const GENERATOR_DEFAULTS = Object.freeze({
  length: 20,
  lower: true,
  upper: true,
  digits: true,
  symbols: true,
  ambiguousOff: true,
});

const CLASSES = Object.freeze({
  lower: "abcdefghijklmnopqrstuvwxyz",
  upper: "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
  digits: "0123456789",
  symbols: "!@#$%^&*()-_=+[]{};:,.?/",
});

// 易混淆集：I/l/1、O/0/o、竖线与引号类——肉眼无法核对的字符（Bitwarden
// ambiguous 同口径）。ambiguousOff 开启时从池与各类里剔除。
const AMBIGUOUS = new Set(["I", "l", "1", "O", "0", "o", "|", "'", '"', "`"]);

const CLASS_KEYS = ["lower", "upper", "digits", "symbols"];

/** 选项归一化：与 GENERATOR_DEFAULTS 浅合并 + 严格校验。非法即抛
 * Error（调用方决定展示文案）——对齐 hSetSettings「拒绝而非静默收敛」。
 * 例外：length 为空串/null/undefined（输入框刚清空）回落默认，不算错误。 */
export function resolveOptions(raw) {
  const source = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
  const rawLength = source.length;
  const length = rawLength === "" || rawLength === null || rawLength === undefined
    ? GENERATOR_DEFAULTS.length
    : Number(rawLength);
  if (!Number.isInteger(length) || length < LENGTH_MIN || length > LENGTH_MAX) {
    throw new Error(`长度必须是 ${LENGTH_MIN}..${LENGTH_MAX} 的整数`);
  }
  const flags = {};
  for (const key of [...CLASS_KEYS, "ambiguousOff"]) {
    const value = source[key] ?? GENERATOR_DEFAULTS[key];
    if (typeof value !== "boolean") throw new Error(`选项 ${key} 必须是布尔值`);
    flags[key] = value;
  }
  return { length, ...flags };
}

/** 按选项构建可用字符集：enabled 类各一组（已剔易混淆），加全池。
 * 至少启用一类且全池非空，否则抛 Error。 */
export function buildPool(options) {
  const opts = resolveOptions(options);
  const classes = [];
  const picked = new Set();
  for (const key of CLASS_KEYS) {
    if (!opts[key]) continue;
    const chars = [...CLASSES[key]].filter((ch) => !(opts.ambiguousOff && AMBIGUOUS.has(ch)));
    if (chars.length === 0) {
      throw new Error("该字符类的全部字符都在易混淆集里——关闭「排除易混淆」或换一类");
    }
    classes.push(chars);
    for (const ch of chars) picked.add(ch);
  }
  if (classes.length === 0) throw new Error("至少启用一个字符类");
  return { opts, classes, pool: [...picked] };
}

/** [0, max) 的无偏随机整数——拒绝采样，绝不取模。max ∈ [1, 2^32]。 */
export function randomInt(max) {
  if (!Number.isInteger(max) || max <= 0 || max > UINT32_SPACE) {
    throw new RangeError("randomInt: max 必须是 1..2^32 的整数");
  }
  const limit = Math.floor(UINT32_SPACE / max) * max;
  const buffer = new Uint32Array(1);
  for (;;) {
    crypto.getRandomValues(buffer);
    if (buffer[0] < limit) return buffer[0] % max;
  }
}

function pick(chars) {
  return chars[randomInt(chars.length)];
}

/** 熵粗估（比特）= length × log2(池大小)，保留 1 位小数。 */
export function entropyBits(options) {
  const { opts, pool } = buildPool(options);
  return Math.round(opts.length * Math.log2(pool.length) * 10) / 10;
}

/** 生成 { password, length, entropyBits, poolSize }。每个启用的字符类保证
 * 至少一枚； randomness 全部来自 crypto.getRandomValues + 拒绝采样。 */
export function generate(options) {
  const { opts, classes, pool } = buildPool(options);
  const chars = new Array(opts.length);

  // 每类先放一枚（随机位），保证类可见性
  for (let i = 0; i < classes.length && i < opts.length; i += 1) {
    chars[i] = pick(classes[i]);
  }
  // 余位从全池采样
  for (let i = classes.length; i < opts.length; i += 1) {
    chars[i] = pick(pool);
  }
  // Fisher-Yates 洗牌：把「每类一枚」的位置信息抹掉
  for (let i = opts.length - 1; i > 0; i -= 1) {
    const j = randomInt(i + 1);
    [chars[i], chars[j]] = [chars[j], chars[i]];
  }
  return {
    password: chars.join(""),
    length: opts.length,
    entropyBits: Math.round(opts.length * Math.log2(pool.length) * 10) / 10,
    poolSize: pool.length,
  };
}
