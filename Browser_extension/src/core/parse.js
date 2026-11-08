/**
 * 解析链：文本 → ParseResult。kv/parse/ 六个解析器与 pipeline 的忠实移植。
 *
 * 顺序有讲究——结构化解析器必须在 bare 之前跑，否则一条 curl 命令会被当成
 * 一串垃圾「裸 key」。generic_scan 只在前面全部落空时才跑：它是散文里捞 key 的
 * 最后手段，产出的候选噪声最大，不该抢在结构化解析前面。
 *
 * 链条之后按 (sha256, keyName) 去重：同一段文本常被多个解析器同时命中
 * （例如 curl 里的 -d JSON 会被 curl 和 jsonblob 各抽一次）。
 *
 * Python→JS 换算点：
 * - shlex 分词用手写 POSIX 分词器替换（单引号字面量、双引号内只转义 " 与 \、
 *   反斜杠转义、空引号产生空 token）；引号不平衡退回空白切分——与桌面
 *   shlex.split 抛 ValueError 后的回退路径一致。
 * - 桌面正则里的 Python \b 是 Unicode 词边界；JS 的 \b 只认 ASCII。对本链路
 *   三处 \b（URL、连接串）用「前后都不是 Unicode 词字符」的环视等价替换。
 */
import { stableStringify } from "./masking.js";
import { Candidate, Rejection, emit, hostsIn } from "./candidate.js";
import { Context, isCredentialKeyName } from "./placeholders.js";

// ============================================================ dotenv

const KEY_SOURCE = String.raw`[A-Za-z_][A-Za-z0-9_.\-]*`;
const PAIR_RE = new RegExp(
  `^\\s*(?:export\\s+)?(${KEY_SOURCE})\\s*=\\s*(.*)$`,
);

/** 去引号、去行尾注释。先按引号取内容、再考虑行尾注释——引号内的 # 天然存活。 */
export function unquote(raw) {
  const text = raw.trim();
  const first = text.charAt(0);
  if (first === '"' || first === "'") {
    const end = text.indexOf(first, 1);
    if (end < 0) return text.slice(1); // 未闭合：只去掉开头那个引号，别吃掉内容
    const inner = text.slice(1, end);
    if (first === '"') {
      return inner.replace(/\\"/g, '"').replace(/\\n/g, "\n").replace(/\\\\/g, "\\");
    }
    return inner;
  }
  return text.split(" #", 1)[0].trim();
}

/** 逐行产出 [行号, 键名, 原始值]。跳过注释与空行。 */
export function splitPairs(text) {
  const pairs = [];
  const lines = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  for (const [index, line] of lines.entries()) {
    const stripped = line.trim();
    if (!stripped || stripped.startsWith("#")) continue;
    const match = PAIR_RE.exec(line);
    if (!match) continue;
    pairs.push([index + 1, match[1], match[2]]);
  }
  return pairs;
}

function kindFor(key, value) {
  if (value.startsWith("-----BEGIN")) return "pem";
  if (value.trimStart().startsWith("{") || value.trimStart().startsWith("[")) return "json";
  const lowered = key.toLowerCase();
  if (lowered.includes("connection") || lowered.includes("conn_str")
    || lowered.includes("database_url")) return "connstring";
  return "token";
}

function dotenvOne(number, key, raw, source) {
  const value = unquote(raw);
  const context = new Context({ source, lineNo: number, lineText: `${key}=${raw}` });
  return emit(value, {
    kind: kindFor(key, value),
    keyName: key,
    hostnames: hostsIn(value),
    source,
    span: `line ${number}`,
    context,
  });
}

/** 单行 KEY=value。多行交给 parseDotenv。 */
export function parseKvline(text, { source = "manual" } = {}) {
  if (text.trim().includes("\n")) return [];
  return splitPairs(text).map(([number, key, raw]) => dotenvOne(number, key, raw, source));
}

/**
 * 整段 .env。主机名传播是保守的：块级主机名放进 contextHostnames（弱证据）
 * 而不是 hostnames（强证据）——否则同一块里的 deepseek base_url 会把
 * GITLAB_TOKEN 判成 deepseek。分级顺序把这条区别落成了行为，见 resolve.js。
 */
export function parseDotenv(text, { source = "manual" } = {}) {
  const pairs = splitPairs(text);
  if (pairs.length < 2) return [];
  const blockHosts = hostsIn(text);
  const results = pairs.map(([number, key, raw]) => dotenvOne(number, key, raw, source));

  // 传播给「键名像凭据、且自己没有直接主机名」的候选。
  const targets = results.map(
    (item) => item instanceof Candidate && item.hostnames.length === 0
      && isCredentialKeyName(item.keyName),
  );
  if (blockHosts.length > 0 && targets.some(Boolean)) {
    return results.map((item, index) => (targets[index]
      ? item.with({
        contextHostnames: blockHosts,
        extra: { ...item.extra, hosts_from_block: true },
      })
      : item));
  }
  return results;
}

export function looksLikeDotenv(text) {
  return splitPairs(text).length >= 2;
}

// ============================================================ bare

const MAX_BARE_LEN = 20000;
const KEY_VALUE_RE = new RegExp(`^${KEY_SOURCE}=`);
// 规则表里每一种凭据形态都是可打印 ASCII。要求 ASCII 是最窄的散文过滤：
// 它挡掉中文说明文字，而不需要任何「看起来像不像标识符」的宽判断。
const ASCII_TOKEN_RE = /^[!-~]+$/;

function isBareValue(value) {
  // 四条排除，每条都对应一个具体案例：多行 → dotenv/jsonblob/generic_scan；
  // 含空白 → 那是命令或散文；{ 或 [ 开头 → JSON；形如 KEY=value → kvline 的活。
  if (value.includes("\n") || value.length > MAX_BARE_LEN) return false;
  if (/\s/.test(value)) return false;
  if (value.charAt(0) === "{" || value.charAt(0) === "[") return false;
  if (KEY_VALUE_RE.test(value)) return false;
  return ASCII_TOKEN_RE.test(value);
}

function inferBareKind(value) {
  if (value.startsWith("-----BEGIN")) return "pem";
  const cut = value.indexOf("://");
  if (cut >= 0 && value.slice(cut + 3, cut + 83).includes("@")) return "connstring";
  return "token";
}

/** 整段文本就是一个值。排在链条末尾附近。 */
export function parseBare(text, { source = "manual" } = {}) {
  const value = text.trim();
  if (!value || !isBareValue(value)) return [];
  const context = new Context({ source, lineText: value });
  return [emit(value, {
    kind: inferBareKind(value),
    hostnames: hostsIn(value),
    source,
    span: "whole input",
    context,
  })];
}

// ============================================================ curl

const CURL_START_RE = /^\s*(?:curl|http|https)(?![\p{L}\p{N}_])/iu;

const HEADER_FLAGS = new Set(["-h", "--header"]);
const USER_FLAGS = new Set(["-u", "--user"]);
const VALUE_FLAGS = new Set([
  "-d", "--data", "--data-raw", "--data-binary", "--data-urlencode",
  "-b", "--cookie", "--url", "-e", "--referer",
]);

// 这些头里装的是凭据本体；其余头（Content-Type、Accept）不是。
const CREDENTIAL_HEADERS = {
  authorization: "AUTHORIZATION",
  "proxy-authorization": "PROXY_AUTHORIZATION",
  "x-api-key": "X_API_KEY",
  "api-key": "API_KEY",
  "x-api-token": "X_API_TOKEN",
  "x-auth-token": "X_AUTH_TOKEN",
  "x-goog-api-key": "GOOGLE_API_KEY",
  "x-functions-key": "FUNCTIONS_KEY",
  "x-amz-security-token": "AWS_SESSION_TOKEN",
  "openai-api-key": "OPENAI_API_KEY",
  "anthropic-api-key": "ANTHROPIC_API_KEY",
  cookie: "COOKIE",
  "x-csrf-token": "CSRF_TOKEN",
};

const BEARER_RE = /^bearer\s+(.+)$/i;
const BASIC_RE = /^basic\s+([A-Za-z0-9+/=]{8,})$/i;
const TOKEN_PAIR_RE = /^([A-Za-z0-9_\-]{2,40})\s*[:=]\s*(\S.*)$/i;

export function looksLikeCurl(text) {
  return CURL_START_RE.test(text);
}

/** 合并反斜杠续行、去掉 PowerShell 的反引号续行，压成一行。 */
export function normalize(text) {
  const joined = text.replace(/\\\r\n/g, " ").replace(/\\\n/g, " ");
  const backtick = joined.replace(/`\r?\n/g, " ");
  return backtick.replace(/\s+/g, " ").trim();
}

/**
 * 手写 POSIX 分词器（桌面用 shlex.split(posix=True)）：
 * 空白分词；单引号内字面量；双引号内只转义 " 与 \，其余反斜杠保留；
 * 引号外反斜杠转义下一字符；空引号产生空 token。
 * 引号不平衡返回 null——调用方退回空白切分（宁可少抽也不崩）。
 */
function posixSplit(text) {
  const tokens = [];
  let current = "";
  let hasToken = false;
  let i = 0;
  const length = text.length;
  while (i < length) {
    const ch = text[i];
    if (ch === " " || ch === "\t" || ch === "\r" || ch === "\n") {
      if (hasToken) {
        tokens.push(current);
        current = "";
        hasToken = false;
      }
      i += 1;
      continue;
    }
    if (ch === "'") {
      const end = text.indexOf("'", i + 1);
      if (end < 0) return null; // No closing quotation
      current += text.slice(i + 1, end);
      hasToken = true;
      i = end + 1;
      continue;
    }
    if (ch === '"') {
      let j = i + 1;
      let closed = false;
      while (j < length) {
        const c = text[j];
        if (c === "\\") {
          if (j + 1 >= length) return null; // No escaped character
          const next = text[j + 1];
          current += (next === '"' || next === "\\") ? next : `\\${next}`;
          j += 2;
          continue;
        }
        if (c === '"') {
          closed = true;
          j += 1;
          break;
        }
        current += c;
        j += 1;
      }
      if (!closed) return null;
      hasToken = true;
      i = j;
      continue;
    }
    if (ch === "\\") {
      if (i + 1 >= length) return null; // No escaped character
      current += text[i + 1];
      hasToken = true;
      i += 2;
      continue;
    }
    current += ch;
    hasToken = true;
    i += 1;
  }
  if (hasToken) tokens.push(current);
  return tokens;
}

/** 分词；引号不平衡（从网页复制时常被智能引号污染）退回按空白切。 */
export function tokenize(text) {
  const line = normalize(text);
  const tokens = posixSplit(line);
  if (tokens !== null) return tokens;
  const relaxed = line.replace(/'/g, '"');
  return relaxed ? relaxed.split(/\s+/) : [];
}

function headerNameFor(rawHeader) {
  return CREDENTIAL_HEADERS[rawHeader] ?? rawHeader.toUpperCase();
}

function fromHeader(raw, hosts, source, position) {
  const match = TOKEN_PAIR_RE.exec(raw.trim());
  if (!match) return [];
  const header = match[1].toLowerCase();
  const payload = match[2].trim();
  const span = `curl arg ${position}`;
  const context = new Context({ source, lineNo: position, lineText: raw });

  const bearer = BEARER_RE.exec(payload);
  if (bearer) {
    return [emit(bearer[1].trim(), {
      keyName: headerNameFor(header), hostnames: hosts, source, span,
      extra: { header, scheme: "bearer" }, context,
    })];
  }
  const basic = BASIC_RE.exec(payload);
  if (basic) {
    return [emit(basic[1], {
      keyName: headerNameFor(header), hostnames: hosts, source, span,
      extra: { header, scheme: "basic" }, context,
    })];
  }
  if (header in CREDENTIAL_HEADERS) {
    return [emit(payload, {
      keyName: CREDENTIAL_HEADERS[header], hostnames: hosts, source, span,
      extra: { header }, context,
    })];
  }
  return [];
}

function fromUser(raw, hosts, source, position) {
  const cut = raw.indexOf(":");
  const user = cut < 0 ? raw : raw.slice(0, cut);
  const password = cut < 0 ? "" : raw.slice(cut + 1);
  if (!password) return [];
  return [emit(password, {
    keyName: "HTTP_BASIC_PASSWORD",
    hostnames: hosts,
    source,
    span: `curl arg ${position}`,
    extra: { basic_user_len: Array.from(user).length },
    context: new Context({ source, lineNo: position, lineText: "-u <user>:<pass>" }),
  })];
}

/**
 * curl / HTTPie 命令行。信号最值钱的解析器：命令行里的 URL 给出主机名，
 * 而主机名是唯一能把裸 sk- 精确判成 DeepSeek 还是 OpenAI 的证据。
 */
export function parseCurl(text, { source = "curl" } = {}) {
  if (!looksLikeCurl(text)) return [];
  const tokens = tokenize(text);
  const hosts = hostsIn(text);
  const out = [];
  let index = 0;
  while (index < tokens.length) {
    const token = tokens[index];
    const lowered = token.toLowerCase();
    if (HEADER_FLAGS.has(lowered) && index + 1 < tokens.length) {
      out.push(...fromHeader(tokens[index + 1], hosts, source, index));
      index += 2;
      continue;
    }
    if (USER_FLAGS.has(lowered) && index + 1 < tokens.length) {
      out.push(...fromUser(tokens[index + 1], hosts, source, index));
      index += 2;
      continue;
    }
    if (VALUE_FLAGS.has(lowered) && index + 1 < tokens.length) {
      // 请求体里可能是一整段 JSON，交给 jsonblob 处理；这里只记位置。
      index += 2;
      continue;
    }
    index += 1;
  }
  return out;
}

// ============================================================ jsonblob

const SERVICE_ACCOUNT_EXTRA = [
  "type", "project_id", "client_email", "token_uri", "private_key_id", "client_id",
];

const CREDENTIAL_KEYS_RE = new RegExp(
  String.raw`^(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|secret[_-]?key|`
  + String.raw`client[_-]?secret|app[_-]?secret|private[_-]?key|password|passwd|token|secret|`
  + String.raw`refresh[_-]?token|id[_-]?token|session[_-]?token|encryption[_-]?key|`
  + String.raw`[a-z0-9_]*(?:_api_key|_apikey|_token|_secret|_password))$`,
  "i",
);

const NON_CREDENTIAL_KEYS = new Set([
  "type", "project_id", "client_email", "token_uri", "private_key_id", "client_id",
  "auth_uri", "auth_provider_x509_cert_url", "client_x509_cert_url",
  "universe_domain", "base_url", "api_base", "endpoint", "url", "host", "model",
  "region", "version", "name", "id", "username", "user",
]);

const BASE_URL_KEYS = [
  "base_url", "api_base", "endpoint", "url", "host", "api_url",
  "token_uri", "auth_uri", "server", "address",
];

const BRACE_START_RE = /[{[]/;

const isPlainObject = (value) => value !== null && typeof value === "object"
  && !Array.isArray(value);

function jsonChunks(text) {
  // 整段文本，以及其中花括号/方括号平衡的子串。
  const stripped = text.trim();
  const chunks = [];
  if (stripped) chunks.push(stripped);
  if (BRACE_START_RE.test(stripped)
    && !(stripped.startsWith("{") || stripped.startsWith("["))) {
    chunks.push(...balancedSubstrings(stripped));
  }
  return chunks;
}

function balancedSubstrings(text) {
  const out = [];
  let depth = 0;
  let start = -1;
  let inString = false;
  let escaped = false;
  for (let index = 0; index < text.length; index += 1) {
    const ch = text[index];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (ch === "\\") {
      escaped = true;
      continue;
    }
    if (ch === '"') {
      inString = !inString;
      continue;
    }
    if (inString) continue;
    if (ch === "{" || ch === "[") {
      if (depth === 0) start = index;
      depth += 1;
    } else if (ch === "}" || ch === "]") {
      if (depth > 0) {
        depth -= 1;
        if (depth === 0 && start >= 0) {
          out.push(text.slice(start, index + 1));
          start = -1;
        }
      }
    }
  }
  return out;
}

function hostsFromSiblings(obj) {
  const values = BASE_URL_KEYS.filter((key) => key in obj).map((key) => String(obj[key]));
  for (const value of Object.values(obj)) {
    if (typeof value === "string" && value.includes("://")) values.push(value);
  }
  return hostsIn(...values);
}

function extraFromSiblings(obj) {
  // 把非机密的兄弟字段带出来，于是不解密也能显示「这把 key 配的是哪个 endpoint」。
  const keys = [...BASE_URL_KEYS, "model", "region", "project_id", "client_email", "type"];
  const out = {};
  for (const key of keys) {
    if (!(key in obj)) continue;
    const value = obj[key];
    if (typeof value === "string" || typeof value === "number") {
      out[key] = String(value).slice(0, 200);
    }
  }
  return out;
}

function isServiceAccount(obj) {
  return String(obj.type ?? "").toLowerCase() === "service_account"
    && Boolean(obj.private_key);
}

function serviceAccount(obj, chunk, source, spanPrefix) {
  // GCP 服务账号 → **一条**记录，整个 JSON 一个 blob。绝不能拆成
  // private_key / client_email 多条——那会把一个凭据碎片化成能各自轮换、
  // 彼此漂移的行。
  const canonical = stableStringify(obj);
  const hostValues = ["token_uri", "auth_uri", "client_email"]
    .filter((key) => key in obj)
    .map((key) => String(obj[key]));
  const extra = {};
  for (const key of SERVICE_ACCOUNT_EXTRA) {
    if (key in obj) extra[key] = String(obj[key]);
  }
  return emit(canonical, {
    kind: "json",
    keyName: "GCP_SERVICE_ACCOUNT",
    hostnames: hostsIn(...hostValues),
    source,
    span: spanPrefix ? `${spanPrefix}service account` : "service account",
    extra,
    context: new Context({ source, lineText: chunk.slice(0, 200) }),
  });
}

function fromDict(obj, chunk, source, spanPrefix = "") {
  if (isServiceAccount(obj)) return [serviceAccount(obj, chunk, source, spanPrefix)];

  const hosts = hostsFromSiblings(obj);
  const span = spanPrefix ? `${spanPrefix}json object` : "json object";
  const context = new Context({ source, lineText: chunk.slice(0, 200) });
  const out = [];
  let found = 0;
  for (const [key, value] of Object.entries(obj)) {
    if (typeof value !== "string" || !CREDENTIAL_KEYS_RE.test(key)) continue;
    if (NON_CREDENTIAL_KEYS.has(key.toLowerCase())) continue;
    found += 1;
    out.push(emit(value, {
      kind: value.startsWith("-----BEGIN") ? "pem" : "token",
      keyName: envStyle(key),
      hostnames: hosts,
      source,
      span,
      extra: extraFromSiblings(obj),
      context,
    }));
  }
  if (found) return out;

  // 没有明显的凭据键，但整个对象里有 PEM——那这个对象本身就是一份凭据。
  if (Object.values(obj).some((v) => typeof v === "string" && v.startsWith("-----BEGIN"))) {
    out.push(emit(stableStringify(obj), {
      kind: "json", keyName: null, hostnames: hosts, source, span,
      extra: extraFromSiblings(obj), context,
    }));
  }
  return out;
}

function envStyle(key) {
  return key.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").toUpperCase();
}

/**
 * JSON 片段。两类完全不同的处理：GCP 服务账号一条化；含 base_url 的配置对象
 * 把主机名传播给兄弟候选——这就是裸 sk- 变成 exact 的机制。
 * 平台判定不在这里发生——本包只把主机名塞进 Candidate.hostnames。
 */
export function parseJsonblob(text, { source = "json" } = {}) {
  const out = [];
  for (const chunk of jsonChunks(text)) {
    let loaded;
    try {
      loaded = JSON.parse(chunk);
    } catch {
      continue;
    }
    if (isPlainObject(loaded)) {
      out.push(...fromDict(loaded, chunk, source));
    } else if (Array.isArray(loaded)) {
      for (const [index, item] of loaded.entries()) {
        if (isPlainObject(item)) out.push(...fromDict(item, chunk, source, `[${index}]`));
      }
    }
  }
  return out;
}

// ============================================================ awscsv

const ACCESS_KEY_HEADER_RE = new RegExp(String.raw`access\s*key\s*id`, "i");
const SECRET_KEY_HEADER_RE = new RegExp(String.raw`secret\s*access\s*key`, "i");
const USER_HEADER_RE = new RegExp(String.raw`^user\s*name$`, "i");

export function looksLikeAwscsv(text) {
  const stripped = text.trim();
  const first = stripped ? stripped.split(/\r\n|\r|\n/)[0] : "";
  return ACCESS_KEY_HEADER_RE.test(first) && SECRET_KEY_HEADER_RE.test(first);
}

/** 零依赖 RFC4180 CSV 解析：支持引号字段、转义引号、CRLF。 */
function parseCsvRows(text) {
  const rows = [];
  let row = [];
  let field = "";
  let inQuotes = false;
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 2;
          continue;
        }
        inQuotes = false;
        i += 1;
        continue;
      }
      field += ch;
      i += 1;
      continue;
    }
    if (ch === '"') {
      inQuotes = true;
      i += 1;
      continue;
    }
    if (ch === ",") {
      row.push(field);
      field = "";
      i += 1;
      continue;
    }
    if (ch === "\r" || ch === "\n") {
      if (ch === "\r" && text[i + 1] === "\n") i += 1;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
      i += 1;
      continue;
    }
    field += ch;
    i += 1;
  }
  if (field !== "" || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

function csvDictRows(text) {
  const raw = parseCsvRows(text).filter(
    (row) => row.length > 1 || (row.length === 1 && row[0] !== ""),
  );
  if (raw.length === 0) return [];
  const headers = raw[0];
  const rows = [];
  for (const values of raw.slice(1)) {
    const row = {};
    for (const [index, header] of headers.entries()) {
      if (!header) continue;
      row[header] = values[index] ?? "";
    }
    rows.push(row);
  }
  return rows;
}

function pickColumn(row, headerPattern) {
  for (const [key, value] of Object.entries(row)) {
    if (key && headerPattern.test(key.trim())) return (value ?? "").trim();
  }
  return "";
}

/**
 * AWS credentials.csv。与 GCP 服务账号的处理**相反**：这里要拆成两条记录。
 * 理由是 use 要把 AWS_ACCESS_KEY_ID 和 AWS_SECRET_ACCESS_KEY 作为两个独立的
 * 环境变量注入，而 scan 必须独立指纹那个真正的机密。两条记录用 pair_id 关联。
 */
export function parseAwscsv(text, { source = "csv" } = {}) {
  if (!looksLikeAwscsv(text)) return [];
  const rows = csvDictRows(text.trim());
  const out = [];
  for (const [offset, row] of rows.entries()) {
    const number = offset + 2; // 第 1 行是表头
    const accessKey = pickColumn(row, ACCESS_KEY_HEADER_RE);
    const secretKey = pickColumn(row, SECRET_KEY_HEADER_RE);
    const userName = pickColumn(row, USER_HEADER_RE);
    if (!secretKey) continue;

    const pairId = `aws-${number}`;
    const context = new Context({ source, lineNo: number, lineText: "aws credentials.csv row" });
    const secretExtra = { pair_id: pairId, aws_pair_role: "secret" };
    if (userName) secretExtra.aws_user_name = userName;

    out.push(emit(secretKey, {
      keyName: "AWS_SECRET_ACCESS_KEY", source,
      span: `csv row ${number}`, extra: secretExtra, context,
    }));

    if (accessKey) {
      out.push(emit(accessKey, {
        keyName: "AWS_ACCESS_KEY_ID", source,
        span: `csv row ${number}`,
        extra: {
          pair_id: pairId, aws_pair_role: "identifier", aws_user_name: userName || "",
        },
        context,
      }));
    }
  }
  return out;
}

// ============================================================ pipeline

// 通用 token 扫。故意不含平台特有前缀——那会和 rules.js 的 shape 表重复，
// 而两份手写的同一列表一定会漂移。这里只负责「找出值的边界」，判平台是 detect 的事。
const TOKEN_RUN_RE = /[A-Za-z0-9_\-]{16,}/g;
const JWT_RUN_RE = /eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*/g;
const PEM_BLOCK_RE =
  /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g;
const CONNSTRING_RUN_RE = new RegExp(
  String.raw`(?<![\p{L}\p{N}_])[a-z][a-z0-9+.\-]*://[^:/@\s]+:[^@\s]+@[^\s'"<>]+`,
  "giu",
);

// 结构化解析器必须在 bare 之前跑；generic_scan 只在全部落空时才跑。
const ORDER = [
  parseCurl, parseJsonblob, parseAwscsv, parseDotenv, parseKvline, parseBare,
];

/** 过滤散文噪声，但**不按长度卡**——长度下限曾经放走过一把 15 字符的真令牌。 */
function plausibleToken(token) {
  if (/\d/.test(token)) return true;
  if (token.includes("-") || token.includes("_")) return true;
  return /[A-Z]/.test(token) && /[a-z]/.test(token);
}

function inside(span, ranges) {
  return ranges.some(([start, end]) => start <= span[0] && span[1] <= end);
}

function lineNumberOf(text, index) {
  let count = 0;
  for (let i = 0; i < index; i += 1) {
    if (text[i] === "\n") count += 1;
  }
  return count + 1;
}

/** 散文里捞 key。只在结构化解析全部落空时才跑。 */
function* genericScan(text, source) {
  const hosts = hostsIn(text);
  const consumed = [];

  // 跨行的形态（PEM）必须在**整段文本**上找——按行找的话 PEM 永远匹配不到，
  // 反而会把中间的 base64 主体当成一把裸 token。已吃掉的区间要记下来。
  for (const match of text.matchAll(PEM_BLOCK_RE)) {
    consumed.push([match.index, match.index + match[0].length]);
    const number = lineNumberOf(text, match.index);
    yield emit(match[0], {
      kind: "pem", hostnames: hosts, source, span: `line ${number}`,
      context: new Context({ source, lineNo: number, lineText: "PEM block" }),
    });
  }

  for (const match of text.matchAll(CONNSTRING_RUN_RE)) {
    if (inside([match.index, match.index + match[0].length], consumed)) continue;
    const number = lineNumberOf(text, match.index);
    const localHosts = hostsIn(match[0]);
    yield emit(match[0], {
      kind: "connstring",
      hostnames: localHosts.length > 0 ? localHosts : hosts,
      source,
      span: `line ${number}`,
      context: new Context({ source, lineNo: number, lineText: "connection string" }),
    });
  }

  for (const match of text.matchAll(JWT_RUN_RE)) {
    if (inside([match.index, match.index + match[0].length], consumed)) continue;
    const number = lineNumberOf(text, match.index);
    yield emit(match[0], {
      hostnames: hosts, source, span: `line ${number}`,
      context: new Context({ source, lineNo: number, lineText: "JWT" }),
    });
  }

  let offset = 0;
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  for (const [index, line] of lines.entries()) {
    for (const match of line.matchAll(TOKEN_RUN_RE)) {
      if (inside([offset + match.index, offset + match.index + match[0].length], consumed)) {
        continue;
      }
      const token = match[0];
      if (!plausibleToken(token)) continue;
      yield emit(token, {
        hostnames: hosts, source, span: `line ${index + 1}`,
        context: new Context({ source, lineNo: index + 1, lineText: line.slice(0, 200) }),
      });
    }
    offset += line.length + 1;
  }
}

function dedupeRejections(rejected) {
  const seen = new Set();
  const unique = [];
  for (const item of rejected) {
    const key = `${item.reason}\u0000${item.detail}\u0000${item.span}`;
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(item);
  }
  return unique;
}

/** 主入口。纯函数：不开库、不碰剪切板、不写任何东西。 */
export function parse(text, { sourceHint = "manual" } = {}) {
  if (!text || !text.trim()) return { candidates: [], rejected: [] };

  const candidates = [];
  const rejected = [];
  const collect = (item) => {
    if (item instanceof Candidate) candidates.push(item);
    else rejected.push(item);
  };
  for (const parser of ORDER) {
    for (const item of parser(text, { source: sourceHint })) collect(item);
  }
  if (candidates.length === 0) {
    for (const item of genericScan(text, sourceHint)) collect(item);
  }

  const seen = new Set();
  const unique = [];
  for (const candidate of candidates) {
    const key = `${candidate.sha256()}\u0000${candidate.keyName ?? ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(candidate);
  }
  return { candidates: unique, rejected: dedupeRejections(rejected) };
}
