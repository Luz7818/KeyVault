/**
 * 用户纠正规则集 —— kv/detect/corrections.py 的忠实移植。
 *
 * detect 不碰数据库——纠正规则由调用方查出来后注入进来（CorrectionSet.fromRows）。
 * 这样整张规则表能在不建库的情况下被测完。
 *
 * 用户规则在**每一级都排在内置规则前面**，所以覆盖永远赢，且永远给 manual 置信度。
 */
import * as rules from "./rules.js";

export const CORRECTION_KINDS = ["exact", "keyname", "hostname", "window", "group"];

/** 全部用户纠正。空实例表示「没有任何覆盖」。 */
export class CorrectionSet {
  constructor({ exact = new Map(), keyname = [], hostname = new Map(), window = [], group = new Map() } = {}) {
    this.exact = exact;         // sha256 -> [platform, createdAt]
    this.keyname = keyname;     // [[RegExp, platform], ...]
    this.hostname = hostname;   // host(小写) -> platform（保持插入序）
    this.window = window;       // [[关键词小写, platform], ...]
    this.group = group;         // group id -> platform
    Object.freeze(this.keyname);
    Object.freeze(this.window);
  }

  static empty() {
    return new CorrectionSet();
  }

  /** 从 correction 表的行构造。行形状见 CONTRACTS.md C3（camelCase，createdAt）。 */
  static fromRows(rows) {
    const exact = new Map();
    const keyname = [];
    const hostname = new Map();
    const window = [];
    const group = new Map();
    for (const row of rows || []) {
      const { kind, pattern, platform } = row;
      if (kind === "exact") {
        exact.set(pattern, [platform, row.createdAt ?? ""]);
      } else if (kind === "keyname") {
        try {
          keyname.push([new RegExp(pattern), platform]);
        } catch {
          continue; // 非法正则的纠正规则跳过，不拖垮整组
        }
      } else if (kind === "hostname") {
        hostname.set(pattern.toLowerCase(), platform);
      } else if (kind === "window") {
        window.push([pattern.toLowerCase(), platform]);
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

  /** 这个具体值被用户亲自指定过平台吗。返回 [platform, createdAt] 或 null。 */
  exactPlatform(sha256) {
    return this.exact.get(sha256) ?? null;
  }

  keynameHits(keyName) {
    if (!keyName) return [];
    return this.keyname
      .filter(([pattern]) => pattern.test(keyName))
      .map(([, platform]) => platform);
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
        if (lowered.endsWith(`.${known}`)) found.push(platform);
      }
    }
    return found;
  }

  windowHits(title) {
    if (!title) return [];
    const lowered = title.toLowerCase();
    return this.window
      .filter(([needle]) => lowered.includes(needle))
      .map(([, platform]) => platform);
  }

  groupPlatform(groupId) {
    if (!groupId) return null;
    return this.group.get(groupId) ?? null;
  }
}

const escapeRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/**
 * 为一次交互消歧选择「仍然有用的最窄范围」。返回 [kind, pattern, 说明] 或 null。
 * 优先级：键名 > 主机名 > 组默认。
 *
 * 组默认**不会**被静默选中——它是唯一一条能悄悄给未来每一把 OpenAI key
 * 贴错标签的纠正，必须用户明确要求（见 groupCorrection）。
 */
export function suggestCorrectionScope(cand, verdict) {
  if (cand.keyName) {
    const pattern = `^${escapeRegExp(cand.keyName)}$`;
    return ["keyname", pattern, `以后凡是键名 ${cand.keyName} 都判成这个平台`];
  }
  for (const host of cand.hostnames) {
    return ["hostname", host.toLowerCase(), `以后凡是主机名 ${host} 都判成这个平台`];
  }
  return null;
}

/** 组级默认。只在用户明确要求时用，调用方必须打印警告。 */
export function groupCorrection(groupId, platform) {
  const members = rules.groupMembers(groupId);
  return [
    "group", groupId,
    `此后该组（${members.join(", ")}）的裸值一律默认 ${platform}；`
    + "个别记录可能是错的，用 kv set-platform 修",
  ];
}
