/**
 * 占位符与非机密闸门 —— kv/parse/placeholders.py 的忠实移植。
 *
 * **它是闸门，不是打分器**：占位符产生 Rejection（带原因，不含值），绝不产生一个
 * 低置信的 Candidate。低置信候选会被存进库，而一个 `sk-xxxxxxxx` 存进去之后，
 * 半年后没人记得那是模板还是真 key。
 *
 * 两条从桌面工程继承的硬约束：
 *
 * - **白名单必须窄。** 判「不是机密」只在可枚举的上下文里发生（.git/ 下、锁文件、
 *   integrity 字段），外加 40/64/128 位纯 hex 这种确定性的哈希形态。
 * - **不设长度下限。** 判别占位符靠的是模式，不是长度。
 *
 * 还有一个顺序陷阱：`https://user:pass@host` 是**真凭据**。所以连接串豁免必须跑在
 * `^https?://` 这条占位符规则**之前**，否则所有带密码的 URL 都会被当成
 * 「这是个网址」而放过。
 */

export const REASON_PLACEHOLDER = "placeholder";
export const REASON_NOT_A_SECRET = "not-a-secret";
export const REASON_EMPTY = "empty";
export const REASON_TOO_LONG = "too-long";

export const MAX_VALUE_LEN = 20000;

// 连接串豁免。必须在 URL 占位符规则之前跑。
const CONNECTION_STRING_RE =
  /^[a-z][a-z0-9+.\-]*:\/\/[^:/@\s]+:[^@\s]+@[^\s]+$/i;

// 桌面正则里的 Python \b 是 Unicode 词边界；JS 的 \b 只认 ASCII。
// 对锚在纯词法 token 边缘的 \b，用「前后都不是 Unicode 词字符」的环视等价替换。
const WORD_CHAR = String.raw`[\p{L}\p{N}_]`;

// 顺序有讲究：先命中的原因胜出。
const PLACEHOLDER_PATTERNS = [
  [/^sk-(?:key|xxx+|your|test|demo|prod|live|please|请)[a-z0-9_\-]*$/i, "sk- 模板"],
  [/^(?:x{4,}|\*{3,}|-{3,}|_{3,}|\.{3,}|0{4,}|1{4,})$/i, "重复字符填充"],
  [/your[-_]?(?:api|secret|access)?[-_]?(?:key|token|secret)/i, "your-key 模板"],
  [/(?:^|[^a-z0-9])(?:changeme|change_me|change-me|replace_?me|replaceme)(?:[^a-z0-9]|$)/i, "change_me 模板"],
  [/(?<![a-z0-9])(?:dummy|fake|sample|example|placeholder|todo|tbd|fixme|none-yet)(?![a-z])/i, "dummy/sample 模板"],
  [/insert[-_ ]?here/i, "insert-here 模板"],
  [/请替换|请填写|替换为|填入|占位|示例|待填|你的密钥|你的令牌/, "中文占位提示"],
  [/^<[^>]*>$/, "尖括号占位"],
  [/^\$\{[^}]*\}$/, "shell 变量展开"],
  // 嵌在中间也要拦：`OPENAI_API_KEY=${OPENAI_API_KEY}` 是 .env 里最常见的
  // 「看起来配好了其实没配」，只匹配整串会漏掉带前缀的写法。
  [/\$\{[^}]*\}/, "含 shell 变量展开"],
  [/%[A-Za-z_][A-Za-z0-9_]*%/, "含 Windows 环境变量展开"],
  [/^%[A-Za-z_][A-Za-z0-9_]*%$/i, "Windows 环境变量展开"],
  [/^\$[A-Za-z_][A-Za-z0-9_]*$/, "shell 变量引用"],
  [/^(?:none|null|nil|undefined|true|false|nan|empty|todo)$/i, "字面空值"],
  [new RegExp(`^(?:str|int|bool|float|bytes|list|dict|set|tuple|Optional|List|Dict|Set|Tuple|Any|Union)(?!${WORD_CHAR})`, "u"), "类型标注"],
  [/^(?:https?|ftp):\/\//i, "裸 URL（不含凭据）"],
  [/(?:^|[./@])example\.(?:com|org|net|cn)(?:[./]|$)/i, "example.com 域名"],
  [/^(?:api[_-]?key|token|secret|password|passwd)\s*$/i, "只有键名没有值"],
];

// 确定性的哈希形态——与上下文无关，一律不是凭据。注意桌面这里没有 (?i)，
// 大写 hex 不在此列（与桌面语义逐字一致）。
const GIT_SHA1_RE = /^[0-9a-f]{40}$/;
const SHA256_HEX_RE = /^[0-9a-f]{64}$/;
const SHA512_HEX_RE = /^[0-9a-f]{128}$/;
const UUID_FORM_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const HEX_RUN_RE = /^[0-9a-f]{16,}$/i;
const DECIMAL_ONLY_RE = /^[0-9]+$/;

// 可枚举的「这里的 hex 不是凭据」上下文。窄是故意的。
export const LOCK_FILES = new Set([
  "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "go.sum", "poetry.lock",
  "composer.lock", "cargo.lock", "gemfile.lock", "pipfile.lock", "uv.lock",
]);

const INTEGRITY_HINT_RE = new RegExp(
  `(?<!${WORD_CHAR})(?:integrity|sha1|sha256|sha512|checksum|digest|etag)(?!${WORD_CHAR})`,
  "iu",
);

// 键名拒绝清单。**必须是拒绝清单，不能是允许清单**——桌面工程踩过一次
// 「键名过滤误杀真凭据」：它用了允许清单，于是任何没被想到的键名一律放过或
// 一律杀掉，两个方向都错。这里只排掉「明确不可能是凭据」的键名，其余一律放行。
//
// 清单必须窄。曾经写过 `_id$`，结果把 AWS_ACCESS_KEY_ID 一起杀了——那正是
// 上面那个坑的另一种长相。所以：不排 `_id$`、不排任何含 key/token/secret 的键名，
// 并且 03-negative.jsonl 里有一条专门的回归用例守着这件事。
const NON_CREDENTIAL_KEY_RE = new RegExp(
  String.raw`(?:^(?:model|models|timeout|retries|retry|region|version|name|id|host|port|`
  + String.raw`scheme|transport|workspace|debug|verbose|log_?level|env|environment|stage|`
  + String.raw`user|username|tenant|project|app_?name|site|locale|lang|timezone|tz)$`
  + String.raw`|_model$|_models$|_timeout$|_retries$|_region$|_version$`
  + String.raw`|_host$|_port$|_url$|_uri$|_endpoint$|_base$|_dir$|_path$|_file$|_mode$`
  + String.raw`|_enabled$|_flag$|_interval(?:_sec)?$|_max_tokens$|_min_call_interval(?:_sec)?$`
  + String.raw`|_transport$|_encoding$|_format$|_type$|_level$|_count$|_limit$`
  + String.raw`|^max_|^min_|^default_|^allowed_|^cors_)`,
  "i",
);

/** 这个键名明确不是凭据（MODEL / TIMEOUT / BASE_URL / MAX_TOKENS ...）。 */
export function isNonCredentialKey(keyName) {
  return Boolean(keyName) && NON_CREDENTIAL_KEY_RE.test(keyName);
}

const CREDENTIAL_KEY_HINT_RE = new RegExp(
  String.raw`(?:key|token|secret|passw|passwd|pwd|credential|apikey|api_key`
  + String.raw`|auth|cert|private|signature|salt|seed|dsn)`,
  "i",
);

/**
 * 键名看起来像凭据吗。**宽松**判定——这是用来决定「要不要把同块的主机名
 * 传播给它」，判错的代价是少传播一次（保守），不是误杀。
 */
export function isCredentialKeyName(keyName) {
  if (!keyName) return false;
  if (isNonCredentialKey(keyName)) return false;
  return CREDENTIAL_KEY_HINT_RE.test(keyName);
}

/** 值来自哪里。只带非机密的定位信息。 */
export class Context {
  constructor({ source = "manual", path = null, lineNo = null, lineText = "", keyName = null } = {}) {
    this.source = source;
    this.path = path;
    this.lineNo = lineNo;
    this.lineText = lineText;
    this.keyName = keyName;
  }

  get fileName() {
    if (!this.path) return "";
    const normalized = this.path.replace(/\\/g, "/");
    const cut = normalized.lastIndexOf("/");
    return cut < 0 ? normalized : normalized.slice(cut + 1);
  }

  inDotGit() {
    return Boolean(this.path) && `${this.path.replace(/\\/g, "/")}/`.includes("/.git/");
  }

  /**
   * 这个上下文里的 hex 串可以确定性地判为「不是凭据」。
   * 只有三种：.git/ 内部、锁文件、以及行内出现 integrity/checksum 这类词。
   * **不含**「看起来像普通标识符」——那条规则曾经放走过一把真高德 Key。
   */
  enumerableHexContext() {
    if (this.inDotGit()) return true;
    if (LOCK_FILES.has(this.fileName.toLowerCase())) return true;
    return INTEGRITY_HINT_RE.test(this.lineText);
  }

  withKeyName(keyName) {
    return new Context({
      source: this.source, path: this.path, lineNo: this.lineNo,
      lineText: this.lineText, keyName,
    });
  }
}

/**
 * 返回 [原因码, 人话说明]，或 null 表示放行。
 * 返回值里**不含 value 本身**——拒绝原因会被打印出来。
 */
export function classify(value, context = null) {
  const ctx = context || new Context();
  const text = value.trim();

  if (!text) return [REASON_EMPTY, "空值"];
  if (Array.from(text).length > MAX_VALUE_LEN) {
    return [REASON_TOO_LONG, `超过 ${MAX_VALUE_LEN} 字符，不像凭据`];
  }

  if (isNonCredentialKey(ctx.keyName)) {
    return [REASON_NOT_A_SECRET, `键名 ${ctx.keyName} 明确不是凭据`];
  }

  // 顺序要紧：连接串是真凭据，必须先豁免，再跑 URL 占位符规则。
  if (CONNECTION_STRING_RE.test(text)) return null;

  for (const [pattern, why] of PLACEHOLDER_PATTERNS) {
    if (pattern.test(text)) return [REASON_PLACEHOLDER, why];
  }

  if (GIT_SHA1_RE.test(text) || SHA256_HEX_RE.test(text) || SHA512_HEX_RE.test(text)) {
    return [REASON_NOT_A_SECRET, "纯哈希形态（40/64/128 位 hex）"];
  }
  if (UUID_FORM_RE.test(text)) return [REASON_NOT_A_SECRET, "UUID 形态"];
  if (DECIMAL_ONLY_RE.test(text)) return [REASON_NOT_A_SECRET, "纯数字"];
  if (HEX_RUN_RE.test(text) && ctx.enumerableHexContext()) {
    return [REASON_NOT_A_SECRET, `可枚举上下文里的 hex 串（${ctx.fileName || ".git"}）`];
  }

  return null;
}

export function isAcceptable(value, context = null) {
  return classify(value, context) === null;
}
