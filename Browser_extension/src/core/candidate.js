/**
 * 构造 Candidate 的共用助手，以及主机名抽取 —— kv/parse/candidate.py 的移植。
 *
 * 所有解析器都经这里过占位符闸门，于是「闸门是闸门不是打分器」这条性质只在
 * 一处实现，不会各解析器各写一套。
 *
 * Python→JS 换算点：Candidate.value 用 string（浏览器无 bytes 语义），只在
 * sha256()/canonical() 落到 masking 层时才经 TextEncoder 转字节。
 */
import * as masking from "./masking.js";
import * as placeholders from "./placeholders.js";

// 桌面 URL_RE 的 \b 是 Unicode 词边界，此处用等价环视替换（见 placeholders.js）。
const URL_RE = new RegExp(
  String.raw`(?<![\p{L}\p{N}_])(?:https?|ftps?|wss?|mysql|postgres(?:ql)?|mongodb(?:\+srv)?|redis|rediss|amqp|ssh|git)://(?:[^:/@\s]+(?::[^@\s]*)?@)?([^/:?\s#]+)`,
  "giu",
);

const EMAIL_DOMAIN_RE = /([a-z0-9._%+\-]+)@([a-z0-9.\-]+\.[a-z]{2,})/gi;

/** 从任意文本里抽出所有主机名。**只是信号**——本包永不把主机名映射成平台。 */
export function hostsIn(...texts) {
  const found = [];
  for (const text of texts) {
    if (!text) continue;
    for (const match of text.matchAll(URL_RE)) found.push(match[1].toLowerCase());
    for (const match of text.matchAll(EMAIL_DOMAIN_RE)) {
      found.push(match[2].toLowerCase());
    }
  }
  const unique = [];
  for (const host of found) {
    if (host && !unique.includes(host)) unique.push(host);
  }
  return unique;
}

/** 从一段文本里抽出来的一个待判定候选（parse 的产出、resolve 的输入）。 */
export class Candidate {
  constructor({
    value, kind = masking.KIND_TOKEN, keyName = null, hostnames = [],
    contextHostnames = [], source = "manual", span = "", extra = {},
  } = {}) {
    this.value = value;
    this.kind = kind;
    this.keyName = keyName;
    this.hostnames = [...hostnames];
    this.contextHostnames = [...contextHostnames];
    this.source = source;
    this.span = span;
    this.extra = { ...extra };
    Object.freeze(this);
  }

  // hostnames 与 contextHostnames 的区别是**证据强度**，不是来源：
  //   hostnames          直接关联——同一条 curl 命令、同一个 JSON 对象里的 endpoint。
  //                      这把 key 就是发给那个主机的，所以它压过键名。
  //   contextHostnames   上下文级——同一块 .env 里别处出现的 URL。它只是「这块配置
  //                      里提到过这个主机」，不能压过附着在值上的键名或形态，
  //                      否则一块含 deepseek base_url 的 .env 会把同块里的
  //                      GITLAB_TOKEN 误判成 deepseek。判定顺序见 resolve.js。

  /** 供 detect.resolve 内部跑正则用。桌面侧 value 是 bytes，这里本就是 string。 */
  text() {
    return this.value;
  }

  canonical() {
    return masking.canonicalize(this.value, this.kind);
  }

  sha256() {
    return masking.fingerprint(this.value, this.kind);
  }

  /** 要露在掩码两侧的头尾两段。渲染时再和掩码字符合成，于是字符可后改。 */
  maskParts(keepHead = 0, keepTail = 4) {
    return masking.splitMask(this.canonical(), keepHead, keepTail);
  }

  preview(char = "*", keepHead = 0, keepTail = 4) {
    const [head, tail] = this.maskParts(keepHead, keepTail);
    return masking.preview(head, tail, char);
  }

  /** dataclasses.replace 等价：按补丁派生一个新候选。 */
  with(patch) {
    return new Candidate({
      value: this.value, kind: this.kind, keyName: this.keyName,
      hostnames: this.hostnames, contextHostnames: this.contextHostnames,
      source: this.source, span: this.span, extra: this.extra, ...patch,
    });
  }
}

/** 被占位符闸门挡下的东西。**不含值**——只有定位和原因。 */
export class Rejection {
  constructor({ span, reason, detail = "" } = {}) {
    this.span = span;
    this.reason = reason;
    this.detail = detail;
    Object.freeze(this);
  }
}

/**
 * 过闸门，然后要么给出候选，要么给出一个不含值的拒绝原因。
 */
export function emit(
  value,
  {
    kind = masking.KIND_TOKEN, keyName = null, hostnames = [], source = "manual",
    span = "", extra = null, context = null,
  } = {},
) {
  let ctx = context || new placeholders.Context({ source, lineText: value });
  // 键名要进 Context：占位符闸门靠它执行「键名拒绝清单」。
  if (keyName && ctx.keyName !== keyName) ctx = ctx.withKeyName(keyName);
  const verdict = placeholders.classify(value, ctx);
  if (verdict !== null) {
    const [reason, why] = verdict;
    return new Rejection({ span, reason, detail: why });
  }
  return new Candidate({
    value, kind, keyName, hostnames, source, span, extra: extra || {},
  });
}
