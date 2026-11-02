// 构造 Candidate 的共用助手与主机名抽取 —— 移植 kv/parse/candidate.py。
//
// 所有解析器都经 emit() 过占位符闸门，「闸门是闸门不是打分器」只在实现一次。
//
// 与桌面版的表示差异（有意）：JS 版 Candidate.value 是**字符串**——浏览器场景
// 没有 bytes 语义，TextEncoder 只在哈希与加密的边界出现。sha256 同步计算
// （见 sha256.js 的 ponytail 注），整条检测链保持同步。

import { sha256Hex } from "./sha256.js";
import { canonicalize } from "./masking.js";
import { classify, Context } from "./placeholders.js";

const URL_RE =
  /\b(?:https?|ftps?|wss?|mysql|postgres(?:ql)?|mongodb(?:\+srv)?|redis|rediss|amqp|ssh|git):\/\/(?:[^:/@\s]+(?::[^@\s]*)?@)?([^/:?\s#]+)/gi;

const EMAIL_DOMAIN_RE = /[a-z0-9._%+\-]+@([a-z0-9.\-]+\.[a-z]{2,})/gi;

/** 从任意文本里抽主机名。只是信号——本包永不把主机名映射成平台。 */
export function hostsIn(...texts) {
  const found = [];
  for (const text of texts) {
    if (!text) continue;
    for (const m of text.matchAll(URL_RE)) found.push(m[1].toLowerCase());
    for (const m of text.matchAll(EMAIL_DOMAIN_RE)) found.push(m[1].toLowerCase());
  }
  const unique = [];
  for (const host of found) {
    if (host && !unique.includes(host)) unique.push(host);
  }
  return unique;
}

export class Candidate {
  constructor({ value, kind = "token", keyName = null, hostnames = [],
                contextHostnames = [], source = "manual", span = "", extra = {} }) {
    this.value = value;
    this.kind = kind;
    this.keyName = keyName;
    this.hostnames = hostnames;
    this.contextHostnames = contextHostnames;
    this.source = source;
    this.span = span;
    this.extra = extra;
  }

  canonical() {
    return canonicalize(this.value, this.kind);
  }

  sha256() {
    return sha256Hex(this.canonical());
  }

  maskParts(keepHead = 0, keepTail = 4) {
    // splitMask 语义在 masking.js；这里对 canonical 值取段
    const canonical = this.canonical();
    if (canonical.length <= keepHead + keepTail) return ["", ""];
    return [keepHead ? canonical.slice(0, keepHead) : "",
            keepTail ? canonical.slice(canonical.length - keepTail) : ""];
  }

  preview(char = "*", keepHead = 0, keepTail = 4) {
    const [head, tail] = this.maskParts(keepHead, keepTail);
    return preview_(head, tail, char);
  }
}

function preview_(head, tail, char) {
  return head + char.repeat(4) + tail;
}

export class Rejection {
  constructor({ span = "", reason = "", detail = "" }) {
    this.rejection = true;
    this.span = span;
    this.reason = reason;
    this.detail = detail;
  }
}

export class ParseResult {
  constructor(candidates = [], rejected = []) {
    this.candidates = candidates;
    this.rejected = rejected;
  }
}

/** 过闸门，然后要么给出候选，要么给出一个不含值的拒绝原因。 */
export function emit(value, { kind = "token", keyName = null, hostnames = [],
                              source = "manual", span = "", extra = null,
                              context = null } = {}) {
  const ctx = context || new Context({ source, lineText: value });
  // 键名要进 Context：占位符闸门靠它执行「键名拒绝清单」。
  if (keyName && ctx.keyName !== keyName) {
    ctx.keyName = keyName;
  }
  const verdict = classify(value, ctx);
  if (verdict !== null) {
    const [reason, why] = verdict;
    return new Rejection({ span, reason, detail: why });
  }
  return new Candidate({
    value, kind, keyName, hostnames, source, span, extra: extra || {},
  });
}

export function splitOutcome(items) {
  const candidates = [];
  const rejected = [];
  for (const item of items) {
    (item instanceof Candidate ? candidates : rejected).push(item);
  }
  return [candidates, rejected];
}

export { Context };
