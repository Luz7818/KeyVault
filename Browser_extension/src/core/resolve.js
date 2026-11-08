/**
 * 分级判定解析器 —— kv/detect/resolve.py 的忠实移植。
 *
 * 优先级（高到低）：
 *
 *     0  用户对这个**具体值**的纠正      -> manual
 *     1  主机名，直接关联（同一条 curl / 同一个 JSON 对象）  -> exact
 *     2  键名（附着在值上）              -> high
 *     3  值形态，且该形态独有             -> medium
 *     4  主机名，上下文级（同一块 .env 里别处）  -> high
 *     5  窗口标题                        -> high
 *     6  值形态歧义 / 需要上下文           -> ambiguous 或 unknown（拒绝落盘）
 *     7  什么都没命中                    -> none
 *
 * **第 1 级和第 4 级都是主机名，但强度不同。** 直接关联的主机名压过键名；
 * 上下文级的主机名排在键名和形态之后。**窗口标题排在形态之后，不是之前**：
 * 窗口标题是关于「环境」的证据，不是关于「值」的证据。它的真正用武之地是
 * 值附着信号全部失败或歧义时——从厂商控制台复制一把裸 sk-，那是唯一能
 * 救回它的信号。
 *
 * 分级的另一个好处是免费送你一行人话证据。加权打分做不到这点，而且它会让
 * 两个弱信号联手压过一个强信号。
 */
import * as rules from "./rules.js";
import { CorrectionSet } from "./corrections.js";

export const CONFIDENCE_MANUAL = "manual";
export const CONFIDENCE_EXACT = "exact";
export const CONFIDENCE_HIGH = "high";
export const CONFIDENCE_MEDIUM = "medium";
export const CONFIDENCE_AMBIGUOUS = "ambiguous";
export const CONFIDENCE_NONE = "none";

export const CONFIDENCES = [
  CONFIDENCE_MANUAL, CONFIDENCE_EXACT, CONFIDENCE_HIGH,
  CONFIDENCE_MEDIUM, CONFIDENCE_AMBIGUOUS, CONFIDENCE_NONE,
];

export const PLATFORM_UNKNOWN = "unknown";
export const PLATFORM_OPENAI_COMPATIBLE = "openai-compatible";

const CONFIDENCE_BY_STAGE = {
  hostname: CONFIDENCE_EXACT,
  keyname: CONFIDENCE_HIGH,
  shape: CONFIDENCE_MEDIUM,
  window: CONFIDENCE_HIGH,
};

/** detect 对一个 Candidate 的判定。 */
export class Verdict {
  constructor({
    platform = PLATFORM_UNKNOWN, confidence = CONFIDENCE_NONE, source = "none",
    evidence = "", candidates = [], maskStyle = [0, 4], kind = "token",
  } = {}) {
    this.platform = platform;
    this.confidence = confidence;
    this.source = source;
    this.evidence = evidence;
    this.candidates = [...candidates];
    this.maskStyle = [...maskStyle];
    this.kind = kind;
    Object.freeze(this);
  }

  /** 可以无需 force 落盘的判定。ambiguous 也算可信——它诚实地说自己不确定。 */
  get trusted() {
    return isTrusted(this);
  }
}

export function isTrusted(verdict) {
  return [
    CONFIDENCE_MANUAL, CONFIDENCE_EXACT, CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM, CONFIDENCE_AMBIGUOUS,
  ].includes(verdict.confidence);
}

/** 形态阶段命中了但**不能定论**：歧义，或缺上下文。先搁着，让窗口标题救。 */
class Deferred {
  constructor(verdict) {
    this.verdict = verdict;
  }
}

function buildVerdict(platform, stage, evidence, {
  confidence = null, candidates = [], rule = null, source = null,
} = {}) {
  const sourceRule = rule || rules.BY_PLATFORM.get(platform) || null;
  return new Verdict({
    platform,
    confidence: confidence || CONFIDENCE_BY_STAGE[stage],
    source: source || stage,
    evidence,
    candidates,
    maskStyle: sourceRule ? sourceRule.maskStyle : [0, 4],
    kind: sourceRule ? sourceRule.kind : "token",
  });
}

/**
 * 从多条命中里挑最具体的。平局返回 [null, 全部] 表示歧义。
 *
 * 两步收窄：①按平台去重（同一条规则可能因两个关键词都命中而出现两次，那不是
 * 歧义，是同一个答案被数了两遍）；②多条命中时用值形态收窄（api.openai.com 同时
 * 挂在 openai 和 openai-project 上，光看 specificity 会让裸 sk- 被判成
 * openai-project）。形态只在「同级有多条命中」时用来收窄，**单条命中一律接受**
 * ——Vercel 的 token 并不带 vercel_ 前缀，拿形态去否决单条命中会误杀它。
 */
function pickBySpecificity(hits, valueText = null) {
  if (hits.length === 0) return [null, []];
  const unique = new Map();
  for (const rule of hits) {
    if (!unique.has(rule.platform)) unique.set(rule.platform, rule);
  }
  let deduped = [...unique.values()];
  if (deduped.length === 1) return [deduped[0], []];

  if (valueText !== null) {
    const fitting = deduped.filter((rule) => rules.shapeFits(rule.platform, valueText));
    if (fitting.length === 0) return [null, []];
    if (new Set(fitting.map((rule) => rule.platform)).size === 1) return [fitting[0], []];
    deduped = fitting;
  }

  const best = Math.max(...deduped.map((rule) => rule.specificity));
  const winners = deduped.filter((rule) => rule.specificity === best);
  if (winners.length === 1) return [winners[0], []];
  return [null, winners];
}

/** 主机名匹配：精确，或 .后缀。api.deepseek.com 命中 deepseek.com 的规则。 */
function matchHosts(hostnames) {
  const found = [];
  for (const host of hostnames || []) {
    const lowered = String(host).toLowerCase().replace(/^\.+|\.+$/g, "");
    if (!lowered) continue;
    const exact = rules.HOST_INDEX.get(lowered);
    if (exact) {
      found.push(...exact);
      continue;
    }
    for (const [known, matched] of rules.HOST_INDEX) {
      if (lowered.endsWith(`.${known}`)) found.push(...matched);
    }
  }
  return found;
}

function hostnameStage(cand, corr) {
  if (cand.hostnames.length === 0) return null;

  const user = corr.hostnameHits(cand.hostnames);
  if (user.length > 0) {
    if (new Set(user).size === 1) {
      return buildVerdict(user[0], "hostname",
        `用户纠正的主机名规则命中 ${cand.hostnames[0]}`, { confidence: CONFIDENCE_MANUAL });
    }
    return buildVerdict(PLATFORM_UNKNOWN, "hostname", "用户主机名规则互相冲突", {
      confidence: CONFIDENCE_AMBIGUOUS, candidates: [...new Set(user)].sort(),
    });
  }

  const [winner, tied] = pickBySpecificity(matchHosts(cand.hostnames), cand.text());
  if (winner) {
    return buildVerdict(winner.platform, "hostname", `主机名 ${cand.hostnames[0]}`, { rule: winner });
  }
  if (tied.length > 0) {
    return buildVerdict(PLATFORM_UNKNOWN, "hostname", "多条主机名规则同级命中", {
      confidence: CONFIDENCE_AMBIGUOUS,
      candidates: [...new Set(tied.map((rule) => rule.platform))].sort(),
    });
  }
  return null;
}

function keynameStage(cand, corr) {
  if (!cand.keyName) return null;

  const user = corr.keynameHits(cand.keyName);
  if (user.length > 0) {
    if (new Set(user).size === 1) {
      return buildVerdict(user[0], "keyname",
        `用户纠正的键名规则命中 ${cand.keyName}`, { confidence: CONFIDENCE_MANUAL });
    }
    return buildVerdict(PLATFORM_UNKNOWN, "keyname", "用户键名规则互相冲突", {
      confidence: CONFIDENCE_AMBIGUOUS, candidates: [...new Set(user)].sort(),
    });
  }

  const hits = rules.KEYNAME_COMPILED
    .filter(([pattern]) => pattern.test(cand.keyName))
    .map(([, rule]) => rule);
  const [winner, tied] = pickBySpecificity(hits, cand.text());
  if (winner) {
    return buildVerdict(winner.platform, "keyname", `键名 ${cand.keyName}`, { rule: winner });
  }
  if (tied.length > 0) {
    return buildVerdict(PLATFORM_UNKNOWN, "keyname", "多条键名规则同级命中", {
      confidence: CONFIDENCE_AMBIGUOUS,
      candidates: [...new Set(tied.map((rule) => rule.platform))].sort(),
    });
  }
  return null;
}

function ambiguousVerdict(tied, corr) {
  // 平局：多条规则共享同一个形态。诚实报告歧义，**绝不猜一个厂商**。
  const platforms = [...new Set(tied.map((rule) => rule.platform))].sort();
  const groupRule = tied.find((rule) => rule.group);
  const groupId = groupRule ? groupRule.group : null;

  const override = corr.groupPlatform(groupId);
  if (override) {
    return buildVerdict(override, "shape", `组默认纠正 ${groupId} -> ${override}`, {
      confidence: CONFIDENCE_MANUAL,
    });
  }

  const label = groupId ? rules.GROUP_LABEL.get(groupId) ?? "" : null;
  if (!label) {
    return buildVerdict(PLATFORM_UNKNOWN, "shape", "多条形态规则同级命中", {
      confidence: CONFIDENCE_AMBIGUOUS, candidates: platforms,
    });
  }

  const members = platforms.filter((platform) => platform !== label);
  return buildVerdict(
    label,
    "shape",
    `形态歧义（${groupId}）：${members.length} 个平台共用这一形态，没有主机名或键名就无法区分`,
    { confidence: CONFIDENCE_AMBIGUOUS, candidates: members },
  );
}

function shapeStage(cand, corr) {
  const text = cand.text();
  const hits = rules.SHAPE_COMPILED
    .filter(([pattern]) => pattern.test(text))
    .map(([, rule]) => rule);
  if (hits.length === 0) return null;

  const [winner, tied] = pickBySpecificity(hits);
  if (winner === null) return new Deferred(ambiguousVerdict(tied, corr));

  if (winner.needsContext) {
    // 走到这里说明主机名和键名两级都没命中——形态本身不可信。
    return new Deferred(buildVerdict(
      PLATFORM_UNKNOWN,
      "shape",
      `${winner.display} 的形态（${winner.note || "裸值"}）缺少键名或主机名佐证，拒绝猜测`,
      { confidence: CONFIDENCE_NONE, rule: winner },
    ));
  }

  return buildVerdict(winner.platform, "shape", `值形态匹配 ${winner.display}`, { rule: winner });
}

/** 环境证据。只在值附着信号全部失败或歧义时才走到这里。 */
function windowStage(cand, corr, title) {
  if (!title) return null;

  const user = corr.windowHits(title);
  if (user.length > 0) {
    if (new Set(user).size === 1) {
      return buildVerdict(user[0], "window", "用户纠正的窗口标题规则命中（原始标题不留存）", {
        confidence: CONFIDENCE_MANUAL,
      });
    }
    return null;
  }

  const lowered = title.toLowerCase();
  const valueText = cand.text();
  const hits = rules.WINDOW_INDEX
    .filter(([needle]) => lowered.includes(needle))
    .map(([, rule]) => rule)
    .filter((rule) => rules.shapeFits(rule.platform, valueText));
  const [winner] = pickBySpecificity(hits, valueText);
  if (winner) {
    return buildVerdict(winner.platform, "window",
      `前台窗口标题含 ${winner.display} 的关键词（原始标题不留存）`, { rule: winner });
  }
  return null;
}

/**
 * 块级主机名：同一块 .env 里别处出现的 endpoint。**弱于键名和形态**——只有那
 * 两级都没给出结论时才走到这里。于是 LLM_BASE_URL 能救回同块的裸 sk-，却不会
 * 把同块的 GITLAB_TOKEN 改判成 deepseek（那个在第 2 级就定了）。
 */
function contextHostnameStage(cand, corr) {
  if (cand.contextHostnames.length === 0) return null;

  const user = corr.hostnameHits(cand.contextHostnames);
  if (new Set(user).size === 1) {
    return buildVerdict(user[0], "shape",
      `用户纠正的主机名规则命中 ${cand.contextHostnames[0]}`, {
        confidence: CONFIDENCE_MANUAL, source: "correction",
      });
  }

  const [winner] = pickBySpecificity(matchHosts(cand.contextHostnames), cand.text());
  if (winner === null) return null;
  return buildVerdict(winner.platform, "shape",
    `同一块配置里的主机名 ${cand.contextHostnames[0]}`, {
      confidence: CONFIDENCE_HIGH, source: "context", rule: winner,
    });
}

/** 对一个候选做判定。纯函数：不开库、不碰剪切板、不写任何东西。 */
export function resolve(cand, corr = null, { windowTitle = null } = {}) {
  const corrections = corr || CorrectionSet.empty();

  const pinned = corrections.exactPlatform(cand.sha256());
  if (pinned) {
    const [platform, when] = pinned;
    return buildVerdict(platform, "shape", `你在 ${when || "此前"} 亲自指定过这个值`, {
      confidence: CONFIDENCE_MANUAL, source: "correction",
    });
  }

  for (const stage of [hostnameStage, keynameStage]) {
    const verdict = stage(cand, corrections);
    if (verdict !== null) return verdict;
  }

  const outcome = shapeStage(cand, corrections);
  if (outcome instanceof Verdict) return outcome;

  const contextual = contextHostnameStage(cand, corrections);
  if (contextual !== null) return contextual;

  const windowed = windowStage(cand, corrections, windowTitle);
  if (windowed !== null) return windowed;

  if (outcome instanceof Deferred) return outcome.verdict;

  return buildVerdict(PLATFORM_UNKNOWN, "none", "没有任何规则匹配", {
    confidence: CONFIDENCE_NONE,
  });
}

/** 一行判定摘要。歧义时列出候选——让人看见「它知道自己不确定」。 */
export function describe(verdict) {
  const marker = {
    manual: "=", exact: "=", high: "+", medium: "~", ambiguous: "?", none: "!",
  }[verdict.confidence] ?? "?";
  const head = `${verdict.platform}${marker}`;
  if (verdict.candidates.length > 0) {
    return `${head}  候选 ${verdict.candidates.join(", ")}`;
  }
  return head;
}
