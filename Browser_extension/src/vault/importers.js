// 批量导入解析 —— 纯函数：只解析成待确认条目，不落库、不检测、不审计。
//
// 信任闸的位置：解析本身是「结构性」的（CSV 列、JSON 字段、otpauth 参数），
// 真正的信任决策在 options 导入向导的第二步——用户逐行勾选并可改平台，
// 确认后逐条走 C1#29 store-direct（显式平台 = 用户拍板 manual，空平台 =
// SW 现场检测）。本模块对齐桌面 importer.py 的角色：解析器不发明安全语义。
//
// 红线（C1#20）：skipped 列表只带原因与非敏感预览（名称/厂商/掩码），绝不
// 携带原始值——tests/importers.test.js 把「skipped 不含任何 item.value」
// 钉成机械断言。
//
// 格式面（综合路线图 §3.1#5）：
// - bitwarden-csv：Bitwarden「导出密码」CSV（folder,name,login_uri,
//   login_username,login_password,login_totp,note 列；RFC 4180 子集解析）；
// - bitwarden-json：Bitwarden JSON 导出（{items:[…]} 或裸数组）；
// - otpauth-list：每行一条 otpauth://totp/…（验证器批量导出）；
// - kv-text：.env / curl 等键值文本——复用 src/core/parse.js（桌面 dotenv
//   解析器的 JS 对位），平台留空走 SW 现场检测，判定与单条 store-direct 同源。
//
// Bitwarden 的 login_password 是随机串，检测器不可能认识它——所以 CSV/JSON
// 行的平台建议值取 login_uri 主机名（用户可改），确认时显式传入，走 manual
// 语义（用户拍板），绝不静默猜平台。一行里的 login_totp 展开为独立条目
// （vault 一条记录一个值，与桌面 TOTP 种子记录同形）。

import { parse } from "../core/parse.js";
import { parseOtpauthUri, parseSeed } from "./totp.js";

export const BULK_FORMATS = ["bitwarden-csv", "bitwarden-json", "otpauth-list", "kv-text"];

const IMPORT_MAX_ITEMS = 500;
const IMPORT_MAX_CHARS = 2_000_000;

// ---------------------------------------------------------------- CSV（RFC 4180 子集）

/** RFC 4180 子集：双引号字段可含逗号/换行/转义的双引号（""）。行分隔
 * 兼容 \n 与 \r\n。返回 string[][]。 */
export function parseCsvRows(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let inQuotes = false;
  let i = 0;
  const source = String(text ?? "");
  const pushCell = () => {
    row.push(cell);
    cell = "";
  };
  const pushRow = () => {
    pushCell();
    if (row.length > 1 || row[0] !== "") rows.push(row);
    row = [];
  };
  while (i < source.length) {
    const ch = source[i];
    if (inQuotes) {
      if (ch === '"') {
        if (source[i + 1] === '"') {
          cell += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        cell += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      pushCell();
    } else if (ch === "\n") {
      pushRow();
    } else if (ch === "\r") {
      if (source[i + 1] === "\n") i += 1;
      pushRow();
    } else {
      cell += ch;
    }
    i += 1;
  }
  if (cell !== "" || row.length > 0) pushRow();
  return rows;
}

/** 从 URL 提取主机名（小写）；解析不了返回空串——与 background 的
 * sourceHostname 同口径。 */
function hostnameOf(uri) {
  if (typeof uri !== "string" || uri === "") return "";
  try {
    return new URL(uri).hostname.toLowerCase();
  } catch (_) {
    return "";
  }
}

function maskedPreview(value) {
  const text = String(value ?? "");
  if (text.length <= 8) return "*".repeat(text.length);
  return `${text.slice(0, 2)}${"*".repeat(6)}${text.slice(-2)}`;
}

// ---------------------------------------------------------------- 列映射

const CSV_COLUMNS = ["folder", "name", "login_uri", "login_username", "login_password", "login_totp", "note"];

function csvRowToDraft(cells, headerIndex) {
  const pick = (column) => {
    const index = headerIndex.get(column);
    return index === undefined ? "" : String(cells[index] ?? "").trim();
  };
  return {
    name: pick("name"),
    uri: pick("login_uri"),
    username: pick("login_username"),
    password: pick("login_password"),
    totp: pick("login_totp"),
    note: pick("note"),
    folder: pick("folder"),
  };
}

function jsonEntryToDraft(entry) {
  if (!entry || typeof entry !== "object" || Array.isArray(entry)) return null;
  const login = entry.login && typeof entry.login === "object" ? entry.login : {};
  const uri = Array.isArray(login.uris) && login.uris[0]?.uri ? login.uris[0].uri : "";
  return {
    name: String(entry.name ?? "").trim(),
    uri: String(uri ?? "").trim(),
    username: String(login.username ?? "").trim(),
    password: String(login.password ?? "").trim(),
    totp: String(login.totp ?? "").trim(),
    note: String(entry.notes ?? "").trim(),
    folder: String(entry.folder ?? "").trim(),
  };
}

/** 把一行草稿变成 0..2 个待确认条目（密码 + 展开的 TOTP）。
 * 返回 { items, skipped, warnings }。 */
function draftToItems(draft) {
  const items = [];
  const skipped = [];
  const warnings = [];
  const base = draft.name || draft.username || "(未命名)";
  const platform = hostnameOf(draft.uri);

  if (!draft.password && !draft.totp) {
    skipped.push({ preview: base, reason: "行里既没有密码也没有 TOTP 种子" });
    return { items, skipped };
  }
  let passwordItem = null;
  if (draft.password) {
    passwordItem = {
      name: base,
      value: draft.password,
      keyName: draft.username || null,
      platform,
      sourceUrl: draft.uri,
      note: draft.note,
      totp: null,
      warnings: [],
    };
    items.push(passwordItem);
  }
  if (draft.totp) {
    // TOTP 列既可能是裸 base32 也可能是 otpauth:// URI——统一过 parseSeed 校验
    let seedText = draft.totp;
    try {
      const uri = parseOtpauthUri(draft.totp);
      if (uri) seedText = uri.secret;
      parseSeed(seedText);
    } catch (error) {
      if (!passwordItem) {
        skipped.push({
          preview: base,
          reason: `TOTP 字段不合法（${error?.message ?? "解析失败"}）`,
        });
        return { items, skipped };
      }
      passwordItem.warnings.push("TOTP 字段不是合法种子，已忽略（密码条目保留）");
      return { items, skipped };
    }
    const issuerHint = (() => {
      const uri = parseOtpauthUri(draft.totp);
      return uri?.issuer || uri?.label || "";
    })();
    items.push({
      name: passwordItem ? `${base}-totp` : base,
      value: seedText,
      keyName: draft.username || null,
      platform: issuerHint || "totp",
      sourceUrl: draft.uri,
      note: passwordItem ? "" : draft.note,
      totp: null,
      warnings: [],
    });
  }
  return { items, skipped };
}

// ---------------------------------------------------------------- 解析主入口

/** 猜测输入格式。返回 BULK_FORMATS 之一或 null（认不出，让 UI 明示失败）。 */
export function sniffFormat(text) {
  const source = String(text ?? "");
  const head = source.slice(0, 4096).trimStart();
  if (/^otpauth:\/\//im.test(head)) return "otpauth-list";
  if (head.startsWith("{") || head.startsWith("[")) {
    // JSON 形态只认 Bitwarden 导出；解不开或形状不对都明示失败，
    // 绝不静默降级成文本解析（那是「假成功」的入口）。
    try {
      const data = JSON.parse(source);
      if (Array.isArray(data) || (data && Array.isArray(data.items))) return "bitwarden-json";
    } catch (_) { /* fallthrough */ }
    return null;
  }
  const firstLine = (head.split(/\r?\n/)[0] ?? "").toLowerCase();
  if (firstLine.includes("login_password") || firstLine.includes("login_username")
    || (firstLine.includes(",") && firstLine.includes("name") && firstLine.includes("login_uri"))) {
    return "bitwarden-csv";
  }
  if (source.trim()) return "kv-text";
  return null;
}

/** 解析待导入文本。返回 { format, items, skipped, truncated }。
 * items 供向导第二步预览勾选（value 只存在于内存，绝不渲染）；
 * skipped 只带 preview + reason，不含任何原始值（C1#20）。 */
export function parseBulk(text, format = null) {
  const source = String(text ?? "");
  const resolvedFormat = format ?? sniffFormat(source);
  if (!resolvedFormat || !BULK_FORMATS.includes(resolvedFormat)) {
    throw new Error("认不出导入格式——支持 Bitwarden CSV / JSON、otpauth:// 列表与 .env 文本");
  }
  if (source.length > IMPORT_MAX_CHARS) {
    throw new Error(`导入内容超过 ${IMPORT_MAX_CHARS} 字符上限`);
  }
  const items = [];
  const skipped = [];

  if (resolvedFormat === "bitwarden-csv") {
    const rows = parseCsvRows(source);
    if (rows.length < 2) throw new Error("CSV 至少要有表头和数据两行");
    const header = rows[0].map((cell) => cell.trim().toLowerCase());
    // 只收录真实存在的列（indexOf -1 的不进 Map，让下面的 has() 闸有效）
    const headerIndex = new Map(
      CSV_COLUMNS.map((column) => [column, header.indexOf(column)]).filter(([, index]) => index >= 0),
    );
    if (!headerIndex.has("login_password") && !headerIndex.has("login_totp")) {
      throw new Error("CSV 表头缺 login_password / login_totp 列——不是 Bitwarden 导出格式");
    }
    for (const cells of rows.slice(1)) {
      if (items.length + skipped.length >= IMPORT_MAX_ITEMS) break;
      const draft = csvRowToDraft(cells, headerIndex);
      const result = draftToItems(draft);
      items.push(...result.items);
      skipped.push(...result.skipped);
    }
  } else if (resolvedFormat === "bitwarden-json") {
    let data;
    try {
      data = JSON.parse(source);
    } catch (_) {
      throw new Error("JSON 解析失败——不是合法的 Bitwarden 导出文件");
    }
    const entries = Array.isArray(data) ? data : (Array.isArray(data?.items) ? data.items : null);
    if (!entries) throw new Error("JSON 里没有 items 数组——不是 Bitwarden 导出格式");
    for (const entry of entries) {
      if (items.length + skipped.length >= IMPORT_MAX_ITEMS) break;
      const draft = jsonEntryToDraft(entry);
      if (!draft) {
        skipped.push({ preview: "", reason: "条目不是对象，已跳过" });
        continue;
      }
      const result = draftToItems(draft);
      items.push(...result.items);
      skipped.push(...result.skipped);
    }
  } else if (resolvedFormat === "otpauth-list") {
    for (const line of source.split(/\r?\n/)) {
      if (items.length + skipped.length >= IMPORT_MAX_ITEMS) break;
      const trimmed = line.trim();
      if (!trimmed) continue;
      if (!/^otpauth:\/\//i.test(trimmed)) {
        skipped.push({ preview: trimmed.slice(0, 24), reason: "不是 otpauth:// 开头的行" });
        continue;
      }
      try {
        const uri = parseOtpauthUri(trimmed);
        parseSeed(uri.secret);
        items.push({
          name: uri.label || uri.issuer || "(TOTP)",
          value: uri.secret,
          keyName: null,
          platform: uri.issuer || "totp",
          sourceUrl: "",
          note: "",
          totp: null,
          warnings: [],
        });
      } catch (error) {
        // 预览绝不能带出 secret：只给 issuer/label 段
        const label = trimmed.split("?")[0].slice(0, 40);
        skipped.push({ preview: label, reason: error?.message ?? "解析失败" });
      }
    }
  } else {
    // kv-text：候选抽取复用 core/parse.js（桌面 dotenv 解析器的 JS 对位）；
    // 平台留空 → 确认步 store-direct 走 SW 现场检测，判定与单条添加同源。
    const result = parse(source, { sourceHint: "import" });
    for (const candidate of result.candidates) {
      if (items.length >= IMPORT_MAX_ITEMS) break;
      items.push({
        name: "",
        value: candidate.value,
        keyName: candidate.keyName ?? null,
        platform: "",
        sourceUrl: "",
        note: "",
        totp: null,
        warnings: [],
      });
    }
  }

  return {
    format: resolvedFormat,
    items,
    skipped,
    truncated: items.length + skipped.length >= IMPORT_MAX_ITEMS,
  };
}

/** 生成掩码预览行——向导第二步展示用，绝不展示原文。 */
export function previewText(value) {
  const text = String(value ?? "");
  if (!text) return "";
  return maskedPreview(text);
}
