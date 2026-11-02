// 分级判定解析器 —— 1:1 移植 kv/detect/resolve.py。
//
// 优先级（高到低）：
//   0 用户对这个具体值的纠正 -> manual
//   1 主机名直接关联（同一条 curl / 同一个 JSON 对象） -> exact
//   2 键名（附着在值上） -> high
//   3 值形态且独有 -> medium
//   4 主机名上下文级（同一块 .env 别处） -> high
//   5 窗口/页面标题 -> high
//   6 形态歧义/缺上下文 -> ambiguous 或 none（拒绝落盘）
//   7 全部未命中 -> none
//
// 两条移植时绝不能走的样（都是桌面版实测出的错判）：
// - 第 1 级与第 4 级都是主机名但强度不同——直接关联压过键名，上下文级排在
//   键名和形态之后。不区分的话 .env 里的 deepseek base_url 会把同块
//   GITLAB_TOKEN 误判成 deepseek。
// - 页面标题排在形态之后：标题是「环境」证据不是「值」证据。DeepSeek 窗口里
//   复制的 ghp_ token 会被标题误判——而形态本来 100% 判对。标题真正的用武
//   之地是值附着信号全部失败或歧义时。

import * as rules from "./rules.js";
import { CorrectionSet } from "./corrections.js";

const CONFIDENCE_BY_STAGE = { hostname: "exact", keyname: "high", shape: "medium", window: "high" };

/** 形态阶段命中了但不能定论：先搁着，让标题有机会救回来。 */
class Deferred {
  constructor(verdict) {
    this.deferred = true;
    this.verdict = verdict;
  }
}

export const CONFIDENCE = {
  MANUAL: "manual", EXACT: "exact", HIGH: "high", MEDIUM: "medium",
  AMBIGUOUS: "ambiguous", NONE: "none",
};

function verdict(platform, stage, evidence, { confidence = null, candidates = [],
                                              rule = null, source = null } = {}) {
  const sourceRule = rule ?? rules.BY_PLATFORM.get(platform);
  return {
    platform,
    confidence: confidence ?? CONFIDENCE_BY_STAGE[stage],
    source: source ?? stage,
    evidence,
    candidates: [...candidates],
    maskStyle: sourceRule ? sourceRule.maskStyle : [0, 4],
    kind: sourceRule ? sourceRule.kind : "token",
  };
}

/** 从多条命中里挑最具体的。平局返回 [null, 全部] 表示歧义。
 *
 * 两步收窄（各对应一个实测错判）：按平台去重（同一答案数两遍不是歧义）；
 * 多条命中时用值形态收窄（api.openai.com 挂 openai(30)/openai-project(88) 两条，
 * 光看 specificity 会让裸 sk- 判成 openai-project——而 sk-proj- 形态不符）。
 * 形态只在「多条命中」时收窄：Vercel 的 token 不带 vercel_ 前缀，单条命中
 * 拿形态否决会误杀。 */
function pickBySpecificity(hits, valueText = null) {
  if (!hits.length) return [null, []];
  const unique = new Map();
  for (const rule of hits) {
    if (!unique.has(rule.platform)) unique.set(rule.platform, rule);
  }
  let deduped = [...unique.values()];
  if (deduped.length === 1) return [deduped[0], []];

  if (valueText !== null) {
    const fitting = deduped.filter((r) => rules.shapeFits(r.platform, valueText));
    if (!fitting.length) return [null, []];
    if (new Set(fitting.map((r) => r.platform)).size === 1) return [fitting[0], []];
    deduped = fitting;
  }

  const best = Math.max(...deduped.map((h) => h.specificity));
  const winners = deduped.filter((h) => h.specificity === best);
  if (winners.length === 1) return [winners[0], []];
  return [null, winners];
}

/** 主机名匹配：精确或 .后缀。api.deepseek.com 命中 deepseek.com 的规则。 */
function matchHosts(hostnames) {
  const found = [];
  for (const host of hostnames || []) {
    const lowered = host.toLowerCase().replace(/^\.+|\.+$/g, "");
    if (!lowered) continue;
    const exact = rules.HOST_INDEX.get(lowered);
    if (exact) {
      found.push(...exact);
      continue;
    }
    for (const [known, matched] of rules.HOST_INDEX) {
      if (lowered.endsWith("." + known)) found.push(...matched);
    }
  }
  return found;
}

function hostnameStage(cand, corr) {
  if (!cand.hostnames.length) return null;

  const user = corr.hostnameHits(cand.hostnames);
  if (user.length) {
    if (new Set(user).size === 1) {
      return verdict(user[0], "hostname",
        `用户纠正的主机名规则命中 ${cand.hostnames[0]}`,
        { confidence: CONFIDENCE.MANUAL });
    }
    return verdict(rules.PLATFORM_UNKNOWN, "hostname", "用户主机名规则互相冲突",
      { confidence: CONFIDENCE.AMBIGUOUS, candidates: [...new Set(user)].sort() });
  }

  const [winner, tied] = pickBySpecificity(matchHosts(cand.hostnames), cand.value);
  if (winner) {
    return verdict(winner.platform, "hostname", `主机名 ${cand.hostnames[0]}`, { rule: winner });
  }
  if (tied.length) {
    return verdict(rules.PLATFORM_UNKNOWN, "hostname", "多条主机名规则同级命中",
      { confidence: CONFIDENCE.AMBIGUOUS, candidates: [...new Set(tied.map((r) => r.platform))].sort() });
  }
  return null;
}

function keynameStage(cand, corr) {
  if (!cand.keyName) return null;

  const user = corr.keynameHits(cand.keyName);
  if (user.length) {
    if (new Set(user).size === 1) {
      return verdict(user[0], "keyname", `用户纠正的键名规则命中 ${cand.keyName}`,
        { confidence: CONFIDENCE.MANUAL });
    }
    return verdict(rules.PLATFORM_UNKNOWN, "keyname", "用户键名规则互相冲突",
      { confidence: CONFIDENCE.AMBIGUOUS, candidates: [...new Set(user)].sort() });
  }

  const hits = rules.KEYNAME_COMPILED
    .filter(([pattern]) => pattern.test(cand.keyName))
    .map(([, rule]) => rule);
  const [winner, tied] = pickBySpecificity(hits, cand.value);
  if (winner) {
    return verdict(winner.platform, "keyname", `键名 ${cand.keyName}`, { rule: winner });
  }
  if (tied.length) {
    return verdict(rules.PLATFORM_UNKNOWN, "keyname", "多条键名规则同级命中",
      { confidence: CONFIDENCE.AMBIGUOUS, candidates: [...new Set(tied.map((r) => r.platform))].sort() });
  }
  return null;
}

function shapeStage(cand, corr) {
  const text = cand.value;
  const hits = rules.SHAPE_COMPILED
    .filter(([pattern]) => pattern.test(text))
    .map(([, rule]) => rule);
  if (!hits.length) return null;

  const [winner, tied] = pickBySpecificity(hits);
  if (winner === null) {
    return new Deferred(ambiguousVerdict(tied, corr));
  }

  if (winner.needsContext) {
    // 走到这里说明主机名和键名两级都没命中——形态本身不可信，拒绝猜测。
    return new Deferred(verdict(rules.PLATFORM_UNKNOWN, "shape",
      `${winner.display} 的形态（${winner.note || "裸值"}）缺少键名或主机名佐证，拒绝猜测`,
      { confidence: CONFIDENCE.NONE, rule: winner }));
  }

  return verdict(winner.platform, "shape", `值形态匹配 ${winner.display}`, { rule: winner });
}

/** 平局：多条规则共享同一形态。诚实报告歧义，绝不猜一个厂商。 */
function ambiguousVerdict(tied, corr) {
  const platforms = [...new Set(tied.map((r) => r.platform))].sort();
  const groupId = tied.map((r) => r.group).find((g) => g) ?? null;

  const override = corr.groupPlatform(groupId);
  if (override) {
    return verdict(override, "shape", `组默认纠正 ${groupId} -> ${override}`,
      { confidence: CONFIDENCE.MANUAL });
  }

  const label = groupId ? rules.GROUP_LABEL.get(groupId) : null;
  if (!label) {
    return verdict(rules.PLATFORM_UNKNOWN, "shape", "多条形态规则同级命中",
      { confidence: CONFIDENCE.AMBIGUOUS, candidates: platforms });
  }

  const members = platforms.filter((p) => p !== label);
  return verdict(label, "shape",
    `形态歧义（${groupId}）：${members.length} 个平台共用这一形态，没有主机名或键名就无法区分`,
    { confidence: CONFIDENCE.AMBIGUOUS, candidates: members });
}

function windowStage(cand, corr, title) {
  // 环境证据。只在值附着信号全部失败或歧义时才走到这里。
  if (!title) return null;

  const user = corr.windowHits(title);
  if (user.length) {
    if (new Set(user).size === 1) {
      return verdict(user[0], "window", "用户纠正的标题规则命中（原始标题不留存）",
        { confidence: CONFIDENCE.MANUAL });
    }
    return null;
  }

  const lowered = title.toLowerCase();
  const valueText = cand.value;
  const hits = rules.WINDOW_INDEX
    .filter(([needle, rule]) => lowered.includes(needle)
      && rules.shapeFits(rule.platform, valueText))
    .map(([, rule]) => rule);
  const [winner] = pickBySpecificity(hits, valueText);
  if (winner) {
    return verdict(winner.platform, "window",
      `页面标题含 ${winner.display} 的关键词（原始标题不留存）`, { rule: winner });
  }
  return null;
}

function contextHostnameStage(cand, corr) {
  // 块级主机名：弱于键名和形态，只有那两级都没结论时才到这里。
  if (!cand.contextHostnames.length) return null;

  const user = corr.hostnameHits(cand.contextHostnames);
  if (new Set(user).size === 1) {
    return verdict(user[0], "shape",
      `用户纠正的主机名规则命中 ${cand.contextHostnames[0]}`,
      { confidence: CONFIDENCE.MANUAL, source: "correction" });
  }

  const [winner] = pickBySpecificity(matchHosts(cand.contextHostnames), cand.value);
  if (winner === null) return null;
  return verdict(winner.platform, "shape",
    `同一块配置里的主机名 ${cand.contextHostnames[0]}`,
    { confidence: CONFIDENCE.HIGH, source: "context", rule: winner });
}

/** 对一个候选做判定。纯函数：不开库、不碰剪贴板、不写任何东西。 */
export function resolve(cand, corr = null, { windowTitle = null } = {}) {
  const corrections = corr ?? CorrectionSet.empty();

  const pinned = corrections.exactPlatform(cand.sha256());
  if (pinned) {
    return verdict(pinned.platform, "shape",
      `你在 ${pinned.createdAt || "此前"} 亲自指定过这个值`,
      { confidence: CONFIDENCE.MANUAL, source: "correction" });
  }

  for (const stage of [hostnameStage, keynameStage]) {
    const result = stage(cand, corrections);
    if (result !== null) return result;
  }

  const outcome = shapeStage(cand, corrections);
  if (outcome && !outcome.deferred) return outcome; // Verdict 直接返回；null/Deferred 继续走

  const contextual = contextHostnameStage(cand, corrections);
  if (contextual !== null) return contextual;

  const windowed = windowStage(cand, corrections, windowTitle);
  if (windowed !== null) return windowed;

  if (outcome instanceof Deferred) return outcome.verdict;

  return verdict(rules.PLATFORM_UNKNOWN, "none", "没有任何规则匹配",
    { confidence: CONFIDENCE.NONE });
}

/** 一行判定摘要。歧义时列出候选——让人看见「它知道自己不确定」。 */
export function describe(v) {
  const marker = { manual: "=", exact: "=", high: "+", medium: "~",
    ambiguous: "?", none: "!" }[v.confidence] ?? "?";
  const head = `${v.platform}${marker}`;
  if (v.candidates && v.candidates.length) {
    return `${head}  候选 ${v.candidates.join(", ")}`;
  }
  return head;
}
