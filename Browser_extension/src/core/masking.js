/**
 * 规范化、指纹、掩码 —— kv/core/masking.py 的忠实移植。
 *
 * 全项目唯一决定「两个值算不算同一把 key」的地方。去重能不能工作全看
 * canonicalize：剪切板复制经常带尾部换行，不规范化的话每次重新复制都会
 * 产生一条重复行。
 *
 * Python→JS 换算点：
 * - 桌面侧的值是 bytes；浏览器没有 bytes 语义，这里的 value 一律是 string，
 *   只在 fingerprint 落到 sha256.js 时经 TextEncoder 转成 UTF-8 字节。
 * - JSON 规范化用 stableStringify 对齐 json.dumps(sort_keys=True,
 *   separators=(",", ":"), ensure_ascii=False) 的紧凑形式（键按码点排序，
 *   非ASCII 不转义）。
 * - split_mask 的长度与切片按 Unicode 码点计（对齐 Python 的 str 语义）。
 */
import { sha256Hex } from "./sha256.js";

export const MASK_RUN = 4;

export const KIND_TOKEN = "token";
export const KIND_JSON = "json";
export const KIND_PEM = "pem";
export const KIND_PAIR = "pair";
export const KIND_CONNSTRING = "connstring";
export const KIND_OTHER = "other";

export const KINDS = [
  KIND_TOKEN, KIND_JSON, KIND_PEM, KIND_PAIR, KIND_CONNSTRING, KIND_OTHER,
];

// 与桌面 _STRIP = b"\r\n\t \x0b\x0c" 同一字节集。
const STRIP_CHARS = "\r\n\t \x0b\x0c";

function stripChars(text, chars) {
  let start = 0;
  let end = text.length;
  while (start < end && chars.includes(text.charAt(start))) start += 1;
  while (end > start && chars.includes(text.charAt(end - 1))) end -= 1;
  return text.slice(start, end);
}

/** 码点序比较器，对齐 Python 对 str 的排序（而非 JS 的 UTF-16 码元序）。 */
function codePointCompare(a, b) {
  const left = Array.from(a);
  const right = Array.from(b);
  const shared = Math.min(left.length, right.length);
  for (let i = 0; i < shared; i += 1) {
    const diff = left[i].codePointAt(0) - right[i].codePointAt(0);
    if (diff !== 0) return diff;
  }
  return left.length - right.length;
}

/**
 * 确定性序列化：键按码点排序、紧凑分隔符、非ASCII 不转义。
 * 数组保序；嵌套对象逐层排序。输入假定来自 JSON.parse（无 undefined）。
 */
export function stableStringify(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  const keys = Object.keys(value).sort(codePointCompare);
  const parts = keys.map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`);
  return `{${parts.join(",")}}`;
}

/** 把同一个逻辑值的各种写法收敛成唯一字符串。幂等。 */
export function canonicalize(value, kind = KIND_TOKEN) {
  if (kind === KIND_JSON) return canonicalJson(value);
  if (kind === KIND_PEM) {
    return `${stripChars(value.replace(/\r\n/g, "\n"), "\n")}\n`;
  }
  return stripChars(value, STRIP_CHARS);
}

/** 键排序 + 紧凑分隔符重序列化；解析失败回退为去空白。 */
function canonicalJson(value) {
  try {
    return stableStringify(JSON.parse(value));
  } catch {
    return stripChars(value, STRIP_CHARS);
  }
}

/** canonicalize 后的 sha256。幂等：传已规范化的值结果相同。 */
export function fingerprint(value, kind = KIND_TOKEN) {
  return sha256Hex(canonicalize(value, kind));
}

/**
 * 取出要露在掩码两侧的头尾两段原文。返回 [head, tail]。
 *
 * 分开存这两段、而不是存合成好的掩码串，是为了让掩码字符成为纯显示偏好：
 * 改配置立刻生效，永远不需要重算全库。值太短、留头留尾就等于泄漏全部时，
 * 两段都返回空串，渲染出来是全掩。
 */
export function splitMask(value, keepHead = 0, keepTail = 4) {
  const text = value.replace(/\n/g, "\\n").replace(/\r/g, "");
  const chars = Array.from(text);
  if (chars.length <= keepHead + keepTail) return ["", ""];
  const head = keepHead ? chars.slice(0, keepHead).join("") : "";
  const tail = keepTail ? chars.slice(chars.length - keepTail).join("") : "";
  return [head, tail];
}

/** 把头尾两段和掩码字符合成预览串。渲染期调用，不需要解密任何东西。 */
export function preview(maskHead, maskTail, char = "*", run = MASK_RUN) {
  return `${maskHead}${char.repeat(run)}${maskTail}`;
}

const PREFIX_RE = /^([A-Za-z]{2,6}[-_])/;

/**
 * 没有平台规则指定掩码风格时的回退：保住可识别的前缀。
 * sk-... -> [3, 4]，显示成 sk-****c31f 而不是 ****c31f。
 */
export function inferMaskStyle(value) {
  const match = PREFIX_RE.exec(canonicalize(value));
  return match ? [match[1].length, 4] : [0, 4];
}
