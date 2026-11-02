// 掩码与规范化 —— 移植 kv/core/masking.py。
//
// 关键语义（与桌面版一致）：
// - canonicalize 是「两个值算不算同一把」的唯一裁决：幂等，去重全靠它。
// - split_mask 存头尾两段而非合成串：掩码字符是纯显示偏好，改配置永不重算。
// - 短值（len <= head+tail）直接全掩，不留任何原文段。

import { sha256Hex } from "./sha256.js";

export const MASK_RUN = 4;

export const KIND_TOKEN = "token";
export const KIND_JSON = "json";
export const KIND_PEM = "pem";
export const KIND_PAIR = "pair";
export const KIND_CONNSTRING = "connstring";
export const KIND_OTHER = "other";
export const KINDS = [KIND_TOKEN, KIND_JSON, KIND_PEM, KIND_PAIR, KIND_CONNSTRING, KIND_OTHER];

const STRIP = "\r\n\t \x0b\x0c";

function stripChars(text) {
  return text.replace(/^[\r\n\t \x0b\x0c]+/, "").replace(/[\r\n\t \x0b\x0c]+$/, "");
}

/** 递归按键排序的紧凑 JSON 序列化 —— 对齐 Python json.dumps(sort_keys=True)。 */
export function stableStringify(obj) {
  if (obj === null || typeof obj !== "object") return JSON.stringify(obj);
  if (Array.isArray(obj)) return "[" + obj.map(stableStringify).join(",") + "]";
  const keys = Object.keys(obj).sort();
  return "{" + keys.map((k) => JSON.stringify(k) + ":" + stableStringify(obj[k])).join(",") + "}";
}

export function canonicalize(value, kind = KIND_TOKEN) {
  if (kind === KIND_JSON) {
    try {
      return stableStringify(JSON.parse(value));
    } catch (_) {
      return stripChars(value);
    }
  }
  if (kind === KIND_PEM) {
    return value.replace(/\r\n/g, "\n").replace(/^\n+|\n+$/g, "") + "\n";
  }
  return stripChars(value);
}

export function fingerprint(value, kind = KIND_TOKEN) {
  return sha256Hex(canonicalize(value, kind));
}

/** 取掩码两侧要保留的原文段。短值返回 ("", "") —— 全掩。 */
export function splitMask(value, keepHead = 0, keepTail = 4) {
  const text = value.replace(/\r/g, "").replace(/\n/g, "\\n");
  if (text.length <= keepHead + keepTail) return ["", ""];
  const head = keepHead ? text.slice(0, keepHead) : "";
  const tail = keepTail ? text.slice(text.length - keepTail) : "";
  return [head, tail];
}

export function preview(maskHead, maskTail, char = "*", run = MASK_RUN) {
  return maskHead + char.repeat(run) + maskTail;
}

/** unknown 平台的掩码回退：保住可识别前缀（sk-… → sk-****c31f）。 */
export function inferMaskStyle(value) {
  const match = /^(?:([A-Za-z]{2,6})[-_])/.exec(canonicalize(value));
  return match ? [match[1].length + 1, 4] : [0, 4];
}
