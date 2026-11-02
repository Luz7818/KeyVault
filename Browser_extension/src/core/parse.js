// 解析链：文本 → ParseResult —— 移植 kv/parse/ 全部六个解析器与 pipeline。
//
// 顺序有讲究（与桌面版一致）：结构化解析器必须在 bare 之前跑，否则一条 curl
// 会被当成一串垃圾「裸 key」；generic_scan 只在全部落空时才跑（散文捞 key 噪声
// 最大）。链条之后按 (sha256, keyName) 去重。

import { Candidate, Rejection, ParseResult, emit, hostsIn, Context } from "./candidate.js";
import { isCredentialKeyName } from "./placeholders.js";

// ---------------------------------------------------------------- dotenv / kvline

const KEY_RE = "[A-Za-z_][A-Za-z0-9_.\\-]*";
const PAIR_RE = new RegExp(`^\\s*(?:export\\s+)?(${KEY_RE})\\s*=\\s*(.*)$`);

/** 去引号、去行尾注释。先按引号取内容再考虑注释——引号内的 # 天然存活。 */
export function unquote(raw) {
  const text = raw.trim();
  const first = text.slice(0, 1);
  if (first === '"' || first === "'") {
    const end = text.indexOf(first, 1);
    if (end < 0) return text.slice(1);
    const inner = text.slice(1, end);
    if (first === '"') {
      return inner.replace(/\\"/g, '"').replace(/\\n/g, "\n").replace(/\\\\/g, "\\");
    }
    return inner;
  }
  return text.split(" #", 1)[0].trim();
}

export function splitPairs(text) {
  const pairs = [];
  for (const [number, line] of numberedLines(text)) {
    const stripped = line.trim();
    if (!stripped || stripped.startsWith("#")) continue;
    const match = PAIR_RE.exec(line);
    if (!match) continue;
    pairs.push([number, match[1], match[2]]);
  }
  return pairs;
}

function* numberedLines(text) {
  const lines = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  for (let i = 0; i < lines.length; i++) yield [i + 1, lines[i]];
}

/** 单行 KEY=value。多行交给 parseDotenv。 */
export function parseKvline(text, { source = "manual" } = {}) {
  if (text.trim().includes("\n")) return [];
  return splitPairs(text).map(([number, key, raw]) => one(number, key, raw, source, []));
}

export function parseDotenv(text, { source = "manual" } = {}) {
  const pairs = splitPairs(text);
  if (pairs.length < 2) return [];
  const blockHosts = hostsIn(text);
  const results = pairs.map(([number, key, raw]) => one(number, key, raw, source, []));

  // 块级主机名传播给「键名像凭据、且自己没有直接主机名」的候选，
  // 放进 contextHostnames（弱证据）而不是 hostnames（强证据）。
  const targets = new Set(
    results.filter(
      (r) => r instanceof Candidate && r.hostnames.length === 0
        && isCredentialKeyName(r.keyName),
    ),
  );
  if (targets.size > 0 && blockHosts.length > 0) {
    return results.map((r) => (targets.has(r)
      ? new Candidate({
          ...r, contextHostnames: blockHosts,
          extra: { ...r.extra, hosts_from_block: true },
        })
      : r));
  }
  return results;
}

function one(number, key, raw, source, hosts) {
  const value = unquote(raw);
  const context = new Context({
    source, lineNo: number, lineText: `${key}=${raw}`,
  });
  return emit(value, {
    kind: kindFor(key, value),
    keyName: key,
    hostnames: hosts.length ? hosts : hostsIn(value),
    source,
    span: `line ${number}`,
    context,
  });
}

function kindFor(key, value) {
  if (value.startsWith("-----BEGIN")) return "pem";
  const trimmed = value.trimStart();
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) return "json";
  const lowered = key.toLowerCase();
  if (lowered.includes("connection") || lowered.includes("conn_str")
      || lowered.includes("database_url")) return "connstring";
  return "token";
}

// ---------------------------------------------------------------- bare

const MAX_BARE_LEN = 20000;
const WHITESPACE = /\s/;
const KEY_VALUE = /^[A-Za-z_][A-Za-z0-9_.\-]*=/;
// 规则表里每一种凭据形态都是可打印 ASCII——挡中文散文，不需要「像不像标识符」宽判断。
const ASCII_TOKEN = /^[!-~]+$/;

export function parseBare(text, { source = "manual" } = {}) {
  const value = text.trim();
  if (!value || !isBareValue(value)) return [];
  const context = new Context({ source, lineText: value });
  return [emit(value, {
    kind: inferKind(value),
    hostnames: hostsIn(value),
    source,
    span: "whole input",
    context,
  })];
}

function isBareValue(value) {
  if (value.includes("\n") || value.length > MAX_BARE_LEN) return false;
  if (WHITESPACE.test(value)) return false;
  if (value[0] === "{" || value[0] === "[") return false;
  if (KEY_VALUE.test(value)) return false;
  return ASCII_TOKEN.test(value);
}

function inferKind(value) {
  if (value.startsWith("-----BEGIN")) return "pem";
  if (value.includes("://") && value.split("://", 2)[1].slice(0, 80).includes("@")) {
    return "connstring";
  }
  return "token";
}

// ---------------------------------------------------------------- curl

const CURL_START = /^\s*(?:curl|http|https)\b/i;

const HEADER_FLAGS = new Set(["-h", "--header"]);
const USER_FLAGS = new Set(["-u", "--user"]);
const VALUE_FLAGS = new Set([
  "-d", "--data", "--data-raw", "--data-binary", "--data-urlencode",
  "-b", "--cookie", "--url", "-e", "--referer",
]);

const CREDENTIAL_HEADERS = {
  "authorization": "AUTHORIZATION",
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
  "cookie": "COOKIE",
  "x-csrf-token": "CSRF_TOKEN",
};

const BEARER_RE = /^bearer\s+(.+)$/i;
const BASIC_RE = /^basic\s+([A-Za-z0-9+/=]{8,})$/i;
const TOKEN_PAIR_RE = /^([A-Za-z0-9_\-]{2,40})\s*[:=]\s*(\S.*)$/i;

export function looksLikeCurl(text) {
  return CURL_START.test(text);
}

function normalizeCurl(text) {
  const joined = text.replace(/\\\r\n/g, " ").replace(/\\\n/g, " ")
    .replace(/`\r?\n/g, " ");
  return joined.replace(/\s+/g, " ").trim();
}

/** POSIX 风格分词（引号 + 转义）；引号不平衡时退回空白切——宁可少抽也不崩。 */
function tokenize(text) {
  const normalized = normalizeCurl(text);
  const tokens = [];
  let current = "";
  let quote = null;
  let escaped = false;
  let closed = true;
  for (const ch of normalized) {
    if (escaped) { current += ch; escaped = false; continue; }
    if (ch === "\\") { escaped = true; continue; }
    if (quote) {
      if (ch === quote) { quote = null; closed = true; continue; }
      current += ch;
      continue;
    }
    if (ch === "'" || ch === '"') { quote = ch; closed = false; continue; }
    if (ch === " ") {
      if (current) { tokens.push(current); current = ""; }
      continue;
    }
    current += ch;
  }
  if (current) tokens.push(current);
  if (!closed) {
    // 引号不平衡（网页复制的智能引号污染）——退回按空白切
    return normalized.replace(/'/g, '"').split(/\s+/).filter(Boolean);
  }
  return tokens;
}

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
      index += 2; // 请求体 JSON 交给 jsonblob，这里只跳过
      continue;
    }
    index += 1;
  }
  return out;
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
      keyName: CREDENTIAL_HEADERS[header] ?? header.toUpperCase(),
      hostnames: hosts, source, span,
      extra: { header, scheme: "bearer" }, context,
    })];
  }
  const basic = BASIC_RE.exec(payload);
  if (basic) {
    return [emit(basic[1], {
      keyName: CREDENTIAL_HEADERS[header] ?? header.toUpperCase(),
      hostnames: hosts, source, span,
      extra: { header, scheme: "basic" }, context,
    })];
  }
  if (header in CREDENTIAL_HEADERS) {
    return [emit(payload, {
      keyName: CREDENTIAL_HEADERS[header],
      hostnames: hosts, source, span, extra: { header }, context,
    })];
  }
  return [];
}

function fromUser(raw, hosts, source, position) {
  const separator = raw.indexOf(":");
  if (separator < 0) return [];
  const user = raw.slice(0, separator);
  const password = raw.slice(separator + 1);
  if (!password) return [];
  return [emit(password, {
    keyName: "HTTP_BASIC_PASSWORD",
    hostnames: hosts,
    source,
    span: `curl arg ${position}`,
    extra: { basic_user_len: user.length },
    context: new Context({ source, lineNo: position, lineText: "-u <user>:<pass>" }),
  })];
}

// ---------------------------------------------------------------- jsonblob

const SERVICE_ACCOUNT_EXTRA = ["type", "project_id", "client_email", "token_uri",
  "private_key_id", "client_id"];

const CREDENTIAL_KEYS =
  /^(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|secret[_-]?key|client[_-]?secret|app[_-]?secret|private[_-]?key|password|passwd|token|secret|refresh[_-]?token|id[_-]?token|session[_-]?token|encryption[_-]?key|[a-z0-9_]*(?:_api_key|_apikey|_token|_secret|_password))$/i;

const NON_CREDENTIAL_KEYS = new Set([
  "type", "project_id", "client_email", "token_uri", "private_key_id", "client_id",
  "auth_uri", "auth_provider_x509_cert_url", "client_x509_cert_url",
  "universe_domain", "base_url", "api_base", "endpoint", "url", "host", "model",
  "region", "version", "name", "id", "username", "user",
]);

const BASE_URL_KEYS = ["base_url", "api_base", "endpoint", "url", "host", "api_url",
  "token_uri", "auth_uri", "server", "address"];

function looksLikeJson(text) {
  const stripped = text.trim();
  return Boolean(stripped) && (stripped[0] === "{" || stripped[0] === "[");
}

export function parseJsonblob(text, { source = "json" } = {}) {
  const out = [];
  for (const chunk of jsonChunks(text)) {
    let loaded;
    try {
      loaded = JSON.parse(chunk);
    } catch (_) {
      continue;
    }
    if (loaded && typeof loaded === "object" && !Array.isArray(loaded)) {
      out.push(...fromDict(loaded, chunk, source));
    } else if (Array.isArray(loaded)) {
      loaded.forEach((item, index) => {
        if (item && typeof item === "object" && !Array.isArray(item)) {
          out.push(...fromDict(item, chunk, source, `[${index}]`));
        }
      });
    }
  }
  return out;
}

function jsonChunks(text) {
  const stripped = text.trim();
  const chunks = [];
  if (stripped) chunks.push(stripped);
  const hasBrace = /[{[]/.test(stripped);
  if (hasBrace && !(stripped.startsWith("{") || stripped.startsWith("["))) {
    chunks.push(...balancedSubstrings(stripped));
  }
  return chunks;
}

function* balancedSubstrings(text) {
  let depth = 0;
  let start = -1;
  let inString = false;
  let escaped = false;
  for (let index = 0; index < text.length; index++) {
    const char = text[index];
    if (escaped) { escaped = false; continue; }
    if (char === "\\") { escaped = true; continue; }
    if (char === '"') { inString = !inString; continue; }
    if (inString) continue;
    if (char === "{" || char === "[") {
      if (depth === 0) start = index;
      depth += 1;
    } else if (char === "}" || char === "]") {
      if (depth > 0) {
        depth -= 1;
        if (depth === 0 && start >= 0) {
          yield text.slice(start, index + 1);
          start = -1;
        }
      }
    }
  }
}

function fromDict(obj, chunk, source, spanPrefix = "") {
  if (isServiceAccount(obj)) return [serviceAccount(obj, chunk, source, spanPrefix)];

  const hosts = hostsFromSiblings(obj);
  const span = spanPrefix ? `${spanPrefix}json object` : "json object";
  const context = new Context({ source, lineText: chunk.slice(0, 200) });

  let found = 0;
  const out = [];
  for (const [key, value] of Object.entries(obj)) {
    if (typeof value !== "string" || !CREDENTIAL_KEYS.test(key)) continue;
    if (NON_CREDENTIAL_KEYS.has(key.toLowerCase())) continue;
    found += 1;
    out.push(emit(value, {
      kind: value.startsWith("-----BEGIN") ? "pem" : "token",
      keyName: envStyle(key),
      hostnames: hosts, source, span,
      extra: extraFromSiblings(obj), context,
    }));
  }
  if (found) return out;

  if (containsPem(obj)) {
    out.push(emit(stableStringify(obj), {
      kind: "json", keyName: null, hostnames: hosts, source, span,
      extra: extraFromSiblings(obj), context,
    }));
  }
  return out;
}

function isServiceAccount(obj) {
  return String(obj.type ?? "").toLowerCase() === "service_account"
    && Boolean(obj.private_key);
}

function serviceAccount(obj, chunk, source, spanPrefix) {
  const canonical = stableStringify(obj);
  const hostValues = ["token_uri", "auth_uri", "client_email"]
    .filter((k) => k in obj).map((k) => String(obj[k]));
  const span = spanPrefix ? `${spanPrefix}service account` : "service account";
  return emit(canonical, {
    kind: "json",
    keyName: "GCP_SERVICE_ACCOUNT",
    hostnames: hostsIn(...hostValues),
    source,
    span,
    extra: Object.fromEntries(
      SERVICE_ACCOUNT_EXTRA.filter((k) => k in obj).map((k) => [k, String(obj[k])]),
    ),
    context: new Context({ source, lineText: chunk.slice(0, 200) }),
  });
}

function hostsFromSiblings(obj) {
  const values = BASE_URL_KEYS.filter((k) => k in obj).map((k) => String(obj[k]));
  for (const v of Object.values(obj)) {
    if (typeof v === "string" && v.includes("://")) values.push(v);
  }
  return hostsIn(...values);
}

function extraFromSiblings(obj) {
  const result = {};
  for (const key of [...BASE_URL_KEYS, "model", "region", "project_id", "client_email", "type"]) {
    if (key in obj && (typeof obj[key] === "string" || typeof obj[key] === "number")) {
      result[key] = String(obj[key]).slice(0, 200);
    }
  }
  return result;
}

function containsPem(obj) {
  return Object.values(obj).some((v) => typeof v === "string" && v.startsWith("-----BEGIN"));
}

function envStyle(key) {
  return key.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").toUpperCase();
}

function stableStringify(obj) {
  if (obj === null || typeof obj !== "object") return JSON.stringify(obj);
  if (Array.isArray(obj)) return "[" + obj.map(stableStringify).join(",") + "]";
  return "{" + Object.keys(obj).sort()
    .map((k) => JSON.stringify(k) + ":" + stableStringify(obj[k])).join(",") + "}";
}

// ---------------------------------------------------------------- awscsv

const ACCESS_KEY_HEADER = /access\s*key\s*id/i;
const SECRET_KEY_HEADER = /secret\s*access\s*key/i;
const USER_HEADER = /^user\s*name$/i;

export function looksLikeAwsCsv(text) {
  const first = text.trim() ? text.trim().split(/\r?\n/)[0] : "";
  return Boolean(ACCESS_KEY_HEADER.test(first) && SECRET_KEY_HEADER.test(first));
}

export function parseAwsCsv(text, { source = "csv" } = {}) {
  if (!looksLikeAwsCsv(text)) return [];
  const [headerLine, ...bodyLines] = text.trim().split(/\r?\n/);
  const headers = splitCsvLine(headerLine);
  const out = [];

  bodyLines.forEach((line, i) => {
    const number = i + 2; // 第 1 行是表头
    const cells = splitCsvLine(line);
    const row = {};
    headers.forEach((h, j) => { row[h] = cells[j] ?? ""; });

    const accessKey = pick(row, ACCESS_KEY_HEADER);
    const secretKey = pick(row, SECRET_KEY_HEADER);
    const userName = pick(row, USER_HEADER);
    if (!secretKey) return;

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
        extra: { pair_id: pairId, aws_pair_role: "identifier", aws_user_name: userName || "" },
        context,
      }));
    }
  });
  return out;
}

function splitCsvLine(line) {
  const cells = [];
  let current = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"' && line[i + 1] === '"') { current += '"'; i += 1; continue; }
      if (ch === '"') { inQuotes = false; continue; }
      current += ch;
      continue;
    }
    if (ch === '"') { inQuotes = true; continue; }
    if (ch === ",") { cells.push(current); current = ""; continue; }
    current += ch;
  }
  cells.push(current);
  return cells.map((c) => c.trim());
}

function pick(row, headerPattern) {
  for (const [key, value] of Object.entries(row)) {
    if (key && headerPattern.test(key.trim())) return (value || "").trim();
  }
  return "";
}

// ---------------------------------------------------------------- pipeline

const TOKEN_RUN = /[A-Za-z0-9_\-]{16,}/g;
const JWT_RUN = /eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*/g;
const PEM_BLOCK = /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g;
const CONNSTRING_RUN = /\b[a-z][a-z0-9+.\-]*:\/\/[^:/@\s]+:[^@\s]+@[^\s'"<>]+/gi;

/** 主入口。纯函数：不开库、不碰剪贴板、不写任何东西。 */
export function parse(text, { sourceHint = "manual" } = {}) {
  if (!text || !text.trim()) return new ParseResult();

  const items = [
    ...parseCurl(text, { source: sourceHint }),
    ...parseJsonblob(text, { source: sourceHint }),
    ...parseAwsCsv(text, { source: sourceHint }),
    ...parseDotenv(text, { source: sourceHint }),
    ...parseKvline(text, { source: sourceHint }),
    ...parseBare(text, { source: sourceHint }),
  ];

  let [candidates, rejected] = splitOutcome(items);
  if (candidates.length === 0) {
    [candidates, rejected] = splitOutcome(genericScan(text, sourceHint));
  }

  // 按 (sha256, keyName) 去重：同一段文本常被多个解析器同时命中
  const unique = [];
  const seen = new Set();
  for (const candidate of candidates) {
    const key = `${candidate.sha256()}|${candidate.keyName}`;
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(candidate);
  }

  const uniqueRejected = [];
  const seenRejections = new Set();
  for (const item of rejected) {
    const key = `${item.reason}|${item.detail}|${item.span}`;
    if (seenRejections.has(key)) continue;
    seenRejections.add(key);
    uniqueRejected.push(item);
  }

  return new ParseResult(unique, uniqueRejected);
}

function splitOutcome(items) {
  const candidates = [];
  const rejected = [];
  for (const item of items) {
    (item instanceof Candidate ? candidates : rejected).push(item);
  }
  return [candidates, rejected];
}

/** 散文里捞 key。跨行形态（PEM）必须整段找；被吃掉的区间后续扫描跳过。 */
function genericScan(text, source) {
  const out = [];
  const hosts = hostsIn(text);
  const consumed = [];

  for (const match of text.matchAll(PEM_BLOCK)) {
    consumed.push([match.index, match.index + match[0].length]);
    out.push(emit(match[0], {
      kind: "pem", hostnames: hosts, source,
      span: `line ${lineAt(text, match.index)}`,
      context: new Context({ source, lineText: "PEM block" }),
    }));
  }

  for (const match of text.matchAll(CONNSTRING_RUN)) {
    if (inside(match, consumed)) continue;
    out.push(emit(match[0], {
      kind: "connstring", hostnames: hostsIn(match[0]).length ? hostsIn(match[0]) : hosts,
      source, span: `line ${lineAt(text, match.index)}`,
      context: new Context({ source, lineText: "connection string" }),
    }));
  }

  for (const match of text.matchAll(JWT_RUN)) {
    if (inside(match, consumed)) continue;
    out.push(emit(match[0], {
      hostnames: hosts, source, span: `line ${lineAt(text, match.index)}`,
      context: new Context({ source, lineText: "JWT" }),
    }));
  }

  let offset = 0;
  for (const [number, line] of numberedLines(text.replace(/\r\n/g, "\n"))) {
    for (const match of line.matchAll(TOKEN_RUN)) {
      if (inside({ index: offset + match.index, 0: match[0] }, consumed)) continue;
      const token = match[0];
      if (!plausibleToken(token)) continue;
      out.push(emit(token, {
        hostnames: hosts, source, span: `line ${number}`,
        context: new Context({ source, lineNo: number, lineText: line.slice(0, 200) }),
      }));
    }
    offset += line.length + 1;
  }
  return out;
}

function lineAt(text, index) {
  return text.slice(0, index).split("\n").length;
}

function inside(match, ranges) {
  const start = match.index;
  const end = match.index + match[0].length;
  return ranges.some(([s, e]) => s <= start && end <= e);
}

/** 过滤散文噪声但不按长度卡：要求数字/分隔符/混合大小写之一。 */
function plausibleToken(token) {
  if (/\d/.test(token)) return true;
  if (token.includes("-") || token.includes("_")) return true;
  return /[A-Z]/.test(token) && /[a-z]/.test(token);
}

export { Candidate, Rejection, ParseResult, emit, hostsIn, Context };
