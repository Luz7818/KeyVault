// 占位符与非机密闸门 —— 1:1 移植 kv/parse/placeholders.py。
//
// 它是闸门不是打分器：占位符产生 Rejection（带原因，绝不含值），绝不产生
// 低置信候选——sk-xxxx 存进库半年后没人记得是模板还是真 key。
//
// 顺序陷阱（与 Python 版同款）：连接串是**真凭据**，豁免必须跑在裸 URL
// 占位符规则**之前**，否则所有带密码的 URL 都被当成「这是个网址」放过。

export const REASON_PLACEHOLDER = "placeholder";
export const REASON_NOT_A_SECRET = "not-a-secret";
export const REASON_EMPTY = "empty";
export const REASON_TOO_LONG = "too-long";

export const MAX_VALUE_LEN = 20000;

// 连接串豁免。必须在 URL 占位符规则之前跑。
const CONNECTION_STRING = /^(?:[a-z][a-z0-9+.\-]*):\/\/[^:/@\s]+:[^@\s]+@[^\s]+$/i;

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
 // 嵌在中间也要拦：OPENAI_API_KEY=${OPENAI_API_KEY} 是 .env 最常见的「看起来配好了」
 [/\$\{[^}]*\}/, "含 shell 变量展开"],
 [/%[A-Za-z_][A-Za-z0-9_]*%/, "含 Windows 环境变量展开"],
 [/^%[A-Za-z_][A-Za-z0-9_]*%$/, "Windows 环境变量展开"],
 [/^\$[A-Za-z_][A-Za-z0-9_]*$/, "shell 变量引用"],
 [/^(?:none|null|nil|undefined|true|false|nan|empty|todo)$/i, "字面空值"],
 [/^(?:str|int|bool|float|bytes|list|dict|set|tuple|Optional|List|Dict|Set|Tuple|Any|Union)\b/, "类型标注"],
 [/^(?:https?|ftp):\/\//i, "裸 URL（不含凭据）"],
 [/(?:^|[./@])example\.(?:com|org|net|cn)(?:[./]|$)/i, "example.com 域名"],
 [/^(?:api[_-]?key|token|secret|password|passwd)\s*$/i, "只有键名没有值"],
];

// 确定性的哈希形态 —— 与上下文无关，一律不是凭据。
const GIT_SHA1 = /^[0-9a-f]{40}$/;
const SHA256_HEX = /^[0-9a-f]{64}$/;
const SHA512_HEX = /^[0-9a-f]{128}$/;
const UUID_FORM = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const HEX_RUN = /^[0-9a-f]{16,}$/i;
const DECIMAL_ONLY = /^[0-9]+$/;

// 可枚举的「这里的 hex 不是凭据」上下文。窄是故意的。
export const LOCK_FILES = new Set([
  "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "go.sum", "poetry.lock",
  "composer.lock", "cargo.lock", "gemfile.lock", "pipfile.lock", "uv.lock",
]);
const INTEGRITY_HINT = /\b(?:integrity|sha1|sha256|sha512|checksum|digest|etag)\b/i;

// 键名拒绝清单。必须是拒绝清单不能是允许清单（桌面版踩过误杀 AWS_ACCESS_KEY_ID）。
const NON_CREDENTIAL_KEY = new RegExp(
  "(?:(?:^(?:model|models|timeout|retries|retry|region|version|name|id|host|port|" +
  "scheme|transport|workspace|debug|verbose|log_?level|env|environment|stage|" +
  "user|username|tenant|project|app_?name|site|locale|lang|timezone|tz)$" +
  "|_model$|_models$|_timeout$|_retries$|_region$|_version$" +
  "|_host$|_port$|_url$|_uri$|_endpoint$|_base$|_dir$|_path$|_file$|_mode$" +
  "|_enabled$|_flag$|_interval(?:_sec)?$|_max_tokens$|_min_call_interval(?:_sec)?$" +
  "|_transport$|_encoding$|_format$|_type$|_level$|_count$|_limit$" +
  "|^max_|^min_|^default_|^allowed_|^cors_))",
  "i",
);

export function isNonCredentialKey(keyName) {
  return Boolean(keyName) && NON_CREDENTIAL_KEY.test(keyName);
}

const CREDENTIAL_KEY_HINT =
  /(?:key|token|secret|passw|passwd|pwd|credential|apikey|api_key|auth|cert|private|signature|salt|seed|dsn)/i;

/** 键名像凭据吗。宽松判定——用于决定要不要传播同块主机名，判错只少传播（保守）。 */
export function isCredentialKeyName(keyName) {
  if (!keyName) return false;
  if (isNonCredentialKey(keyName)) return false;
  return CREDENTIAL_KEY_HINT.test(keyName);
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
    const parts = this.path.replace(/\\/g, "/").split("/");
    return parts[parts.length - 1];
  }

  inDotGit() {
    return Boolean(this.path) && (this.path.replace(/\\/g, "/") + "/").includes("/.git/");
  }

  enumerableHexContext() {
    if (this.inDotGit()) return true;
    if (LOCK_FILES.has(this.fileName.toLowerCase())) return true;
    return INTEGRITY_HINT.test(this.lineText);
  }
}

/** 返回 [原因码, 人话说明]，或 null 表示放行。返回值绝不含 value 本身。 */
export function classify(value, context = null) {
  const ctx = context || new Context();
  const text = value.trim();

  if (!text) return [REASON_EMPTY, "空值"];
  if (text.length > MAX_VALUE_LEN) return [REASON_TOO_LONG, `超过 ${MAX_VALUE_LEN} 字符，不像凭据`];

  if (isNonCredentialKey(ctx.keyName)) {
    return [REASON_NOT_A_SECRET, `键名 ${ctx.keyName} 明确不是凭据`];
  }

  // 顺序要紧：连接串是真凭据，先豁免再跑 URL 占位符规则。
  if (CONNECTION_STRING.test(text)) return null;

  for (const [pattern, why] of PLACEHOLDER_PATTERNS) {
    if (pattern.test(text)) return [REASON_PLACEHOLDER, why];
  }

  if (GIT_SHA1.test(text) || SHA256_HEX.test(text) || SHA512_HEX.test(text)) {
    return [REASON_NOT_A_SECRET, "纯哈希形态（40/64/128 位 hex）"];
  }
  if (UUID_FORM.test(text)) return [REASON_NOT_A_SECRET, "UUID 形态"];
  if (DECIMAL_ONLY.test(text)) return [REASON_NOT_A_SECRET, "纯数字"];
  if (HEX_RUN.test(text) && ctx.enumerableHexContext()) {
    return [REASON_NOT_A_SECRET, `可枚举上下文里的 hex 串（${ctx.fileName || ".git"}）`];
  }

  return null;
}

export function isAcceptable(value, context = null) {
  return classify(value, context) === null;
}
