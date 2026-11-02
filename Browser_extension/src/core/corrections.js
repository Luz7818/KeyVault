// 用户纠正规则集 —— 移植 kv/detect/corrections.py。
//
// 用户规则在每一级都排在内置规则前面，覆盖永远赢，且永远给 manual 置信度。

export const CORRECTION_KINDS = ["exact", "keyname", "hostname", "window", "group"];

export class CorrectionSet {
  constructor({ exact = new Map(), keyname = [], hostname = new Map(),
                window = [], group = new Map() } = {}) {
    this.exact = exact;         // sha256 -> {platform, createdAt}
    this.keyname = keyname;     // [{pattern, platform}]
    this.hostname = hostname;   // host(小写) -> platform
    this.window = window;       // [{needle(小写), platform}]
    this.group = group;         // groupId -> platform
  }

  static empty() {
    return new CorrectionSet();
  }

  /** 从存储行构造（行来自 vault 的 correction 表）。 */
  static fromRows(rows) {
    const exact = new Map();
    const keyname = [];
    const hostname = new Map();
    const window = [];
    const group = new Map();
    for (const row of rows) {
      const { kind, pattern, platform } = row;
      if (kind === "exact") {
        exact.set(pattern, { platform, createdAt: row.createdAt ?? "" });
      } else if (kind === "keyname") {
        try {
          keyname.push({ pattern: new RegExp(pattern), platform });
        } catch (_) {
          continue; // 用户输入的坏正则：跳过不崩
        }
      } else if (kind === "hostname") {
        hostname.set(pattern.toLowerCase(), platform);
      } else if (kind === "window") {
        window.push({ needle: pattern.toLowerCase(), platform });
      } else if (kind === "group") {
        group.set(pattern, platform);
      }
    }
    return new CorrectionSet({ exact, keyname, hostname, window, group });
  }

  isEmpty() {
    return this.exact.size === 0 && this.keyname.length === 0
      && this.hostname.size === 0 && this.window.length === 0 && this.group.size === 0;
  }

  exactPlatform(sha256) {
    return this.exact.get(sha256) ?? null;
  }

  keynameHits(keyName) {
    if (!keyName) return [];
    return this.keyname.filter((k) => k.pattern.test(keyName)).map((k) => k.platform);
  }

  hostnameHits(hostnames) {
    const found = [];
    for (const host of hostnames || []) {
      const lowered = host.toLowerCase();
      if (this.hostname.has(lowered)) {
        found.push(this.hostname.get(lowered));
        continue;
      }
      for (const [known, platform] of this.hostname) {
        if (lowered.endsWith("." + known)) found.push(platform);
      }
    }
    return found;
  }

  windowHits(title) {
    if (!title) return [];
    const lowered = title.toLowerCase();
    return this.window.filter((w) => lowered.includes(w.needle)).map((w) => w.platform);
  }

  groupPlatform(groupId) {
    if (!groupId) return null;
    return this.group.get(groupId) ?? null;
  }
}

/** 为一次消歧选择「仍然有用的最窄范围」：键名 > 主机名 > 组默认（组默认绝不静默选）。 */
export function suggestCorrectionScope(cand) {
  if (cand.keyName) {
    const escaped = cand.keyName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return ["keyname", `^${escaped}$`, `以后凡是键名 ${cand.keyName} 都判成这个平台`];
  }
  for (const host of cand.hostnames) {
    return ["hostname", host.toLowerCase(), `以后凡是主机名 ${host} 都判成这个平台`];
  }
  return null;
}

import { groupMembers } from "./rules.js";

/** 组级默认。只在用户明确要求时用，调用方必须打印警告。 */
export function groupCorrection(groupId, platform) {
  const members = groupMembers(groupId);
  return ["group", groupId,
    `此后该组（${members.join(", ")}）的裸值一律默认 ${platform}；个别记录可能是错的，用「设为平台」修`];
}
