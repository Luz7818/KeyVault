/**
 * 平台规则表 —— 纯数据，无逻辑。kv/detect/rules.py 的忠实移植。
 *
 * **加一个平台就是往 RULES 里加一行，别处一行都不用改。** 交互菜单、歧义组、
 * golden 语料的断言全部从这张表推导出来。
 *
 * 三条从桌面工程继承的硬约束（都对应真实踩过的坑）：
 *
 * 1. **不给凭据值设长度下限。** 曾经卡过 15 字符下限，一把 15 字符的真令牌刚好
 *    漏网。只在厂商格式确实要求时才写 {16,}。
 * 2. **正则语义与桌面逐点一致。** 规则表存 Python 风格原串（含前导 (?i)/(?s)），
 *    编译时提取旗标转成 JS flags；Python 的 $（匹配串尾或串尾换行之前）换成
 *    等价零宽断言。原串本身保留，供 tableHash 与桌面逐字节对拍。
 * 3. **needsContext 用于「形态本身不可信」的规则。** 裸 hex-32 既是高德 Key 也是
 *    MD5 / 去掉连字符的 GUID / git 对象名的一部分。没有键名或主机名佐证时判
 *    unknown 并拒绝落盘——那是「没识别出来」，不是「被静默放过」。
 */
import { sha256Hex } from "./sha256.js";
import { stableStringify } from "./masking.js";

// 裸 sk- 的形态在 OpenAI / DeepSeek / SiliconFlow / Moonshot / 智谱 / 灵积之间
// **完全相同**，靠正则永远分不出来。所以它们共用这一个形态，并挂在同一个歧义组里。
export const SK_SHAPE = String.raw`^sk-[A-Za-z0-9_\-]{16,}$`;

export const GROUP_OPENAI_COMPATIBLE = "openai_compatible";

function defRule(spec) {
  const rule = {
    platform: "",
    display: "",
    hostnames: [],
    keynames: [],
    shape: null,
    window: [],
    group: null,
    kind: "token",
    needsContext: false,
    specificity: 0,
    maskStyle: [0, 4],
    note: "",
    consoleUrl: "",
    ...spec,
  };
  Object.freeze(rule.hostnames);
  Object.freeze(rule.keynames);
  Object.freeze(rule.window);
  Object.freeze(rule.maskStyle);
  return Object.freeze(rule);
}

export const RULES = Object.freeze(
  [
    // ------------------------------------------------ 形态独有、可直接判定
    {
      platform: "anthropic", display: "Anthropic",
      hostnames: ["api.anthropic.com"],
      keynames: [String.raw`(?i)^ANTHROPIC_(API_)?(KEY|TOKEN|AUTH)$`],
      shape: String.raw`^sk-ant-(?:api03-)?[A-Za-z0-9_\-]{20,}$`,
      window: ["anthropic", "claude"],
      specificity: 90, maskStyle: [8, 4],
      consoleUrl: "https://console.anthropic.com/settings/keys",
    },
    {
      platform: "github", display: "GitHub",
      hostnames: ["api.github.com", "github.com"],
      keynames: [
        String.raw`(?i)^(GITHUB|GH)_(TOKEN|PAT|API_KEY)$`,
        String.raw`(?i)^UI_PRO_MAX_GITHUB_TOKEN$`,
      ],
      shape: String.raw`(?i)^(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})$`,
      window: ["github", "github.com"],
      specificity: 90, maskStyle: [4, 4],
      consoleUrl: "https://github.com/settings/tokens",
    },
    {
      platform: "aws-access-key-id", display: "AWS access key ID",
      keynames: [String.raw`(?i)^AWS_ACCESS_KEY_ID$`],
      shape: String.raw`^AKIA[0-9A-Z]{16}$`,
      window: ["aws", "amazon web services", "iam"],
      specificity: 95, maskStyle: [4, 4],
      note: "这是标识符不是机密本体；配对的 secret access key 另存一条",
    },
    {
      platform: "google", display: "Google",
      hostnames: ["generativelanguage.googleapis.com", "googleapis.com",
        "oauth2.googleapis.com"],
      keynames: [String.raw`(?i)^(GOOGLE|GEMINI|GOOGLE_FONTS)_API_KEY$`],
      shape: String.raw`^AIza[0-9A-Za-z_\-]{35}$`,
      window: ["google cloud", "gemini", "aistudio"],
      specificity: 85, maskStyle: [4, 4],
      consoleUrl: "https://aistudio.google.com/app/apikey",
    },
    {
      platform: "gitlab", display: "GitLab",
      hostnames: ["gitlab.com"],
      keynames: [String.raw`(?i)^GITLAB_(TOKEN|PAT|API_KEY)$`],
      shape: String.raw`^glpat-[A-Za-z0-9_\-]{20,}$`,
      window: ["gitlab"],
      specificity: 90, maskStyle: [6, 4],
    },
    {
      platform: "slack", display: "Slack",
      hostnames: ["slack.com"],
      keynames: [String.raw`(?i)^SLACK_(BOT_)?(TOKEN|WEBHOOK)$`],
      shape: String.raw`^xox[baprs]-[A-Za-z0-9\-]{10,}$`,
      window: ["slack"],
      specificity: 90, maskStyle: [5, 4],
    },
    {
      platform: "vercel", display: "Vercel",
      hostnames: ["api.vercel.com", "vercel.com"],
      keynames: [String.raw`(?i)^VERCEL_(TOKEN|OIDC_TOKEN)$`],
      shape: String.raw`^vercel_[A-Za-z0-9]{20,}$`,
      window: ["vercel"],
      specificity: 90, maskStyle: [7, 4],
      consoleUrl: "https://vercel.com/account/tokens",
    },
    {
      platform: "npm", display: "npm",
      hostnames: ["registry.npmjs.org"],
      keynames: [String.raw`(?i)^NPM_(TOKEN|AUTH_TOKEN)$`],
      shape: String.raw`^npm_[A-Za-z0-9]{30,}$`,
      window: ["npm", "npmjs"],
      specificity: 90, maskStyle: [4, 4],
    },
    {
      platform: "telegram", display: "Telegram Bot",
      hostnames: ["api.telegram.org"],
      keynames: [String.raw`(?i)^(TELEGRAM|TG)_BOT_(TOKEN|SECRET)$`],
      shape: String.raw`^[0-9]{6,12}:[A-Za-z0-9_\-]{30,}$`,
      window: ["botfather", "telegram"],
      specificity: 85, maskStyle: [0, 4],
    },
    {
      platform: "modelscope", display: "ModelScope",
      hostnames: ["api-inference.modelscope.cn", "modelscope.cn"],
      keynames: [String.raw`(?i)^MODELSCOPE_`],
      shape: String.raw`^ms-[A-Za-z0-9\-]{16,}$`,
      window: ["modelscope", "魔搭"],
      specificity: 70, maskStyle: [3, 4],
    },
    {
      platform: "wechat-appid", display: "WeChat AppID",
      keynames: [String.raw`(?i)^(WX|WECHAT)_APP_?ID$`],
      shape: String.raw`^wx[0-9a-f]{16}$`,
      window: ["微信", "wechat", "公众平台"],
      specificity: 80, maskStyle: [2, 4],
      note: "AppID 是标识符；配套的 AppSecret 另存一条",
    },
    {
      platform: "stripe", display: "Stripe",
      hostnames: ["api.stripe.com"],
      keynames: [String.raw`(?i)^STRIPE_(SECRET|API|PUBLISHABLE)_KEY$`],
      shape: String.raw`^(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{20,}$`,
      window: ["stripe"],
      specificity: 90, maskStyle: [9, 4],
      consoleUrl: "https://dashboard.stripe.com/apikeys",
    },
    {
      platform: "openai-project", display: "OpenAI project key",
      hostnames: ["api.openai.com"],
      shape: String.raw`^sk-proj-[A-Za-z0-9_\-]{20,}$`,
      window: ["openai", "platform.openai.com"],
      specificity: 88, maskStyle: [8, 4],
      note: "sk-proj- 前缀是 OpenAI 独有的，所以它**不**歧义 —— 不进 openai_compatible 组",
    },
    {
      platform: "jwt", display: "JWT（签发方未定）",
      keynames: [String.raw`(?i)_JWT$`, String.raw`(?i)^OIDC_TOKEN$`],
      shape: String.raw`^eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*$`,
      specificity: 50, maskStyle: [0, 4], kind: "token",
      note: "JWT 的平台信息在 payload 里，需要解 base64 才知道；v1 不做",
    },
    {
      platform: "pem-private-key", display: "PEM 私钥",
      keynames: [String.raw`(?i)_PRIVATE_KEY$`],
      shape: String.raw`(?s)^-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----\s*$`,
      specificity: 95, maskStyle: [0, 0], kind: "pem",
    },
    {
      platform: "connection-string", display: "带密码的连接串",
      shape: String.raw`^[a-z][a-z0-9+.\-]*://[^:/@\s]+:[^@\s]+@[^\s]+$`,
      specificity: 70, maskStyle: [0, 0], kind: "connstring",
      note: "形如 scheme://user:pass@host —— 这是真凭据，不是占位符",
    },
    // ------------------------------------------------ 形态需要上下文佐证
    {
      platform: "amap", display: "高德 AMap",
      hostnames: ["restapi.amap.com", "lbs.amap.com", "webapi.amap.com"],
      keynames: [String.raw`(?i)^(AMAP|GAODE)_`],
      shape: String.raw`(?i)^[0-9a-f]{32}$`,
      window: ["高德", "amap", "lbs.amap"],
      needsContext: true,
      specificity: 40, maskStyle: [0, 4],
      consoleUrl: "https://console.amap.com/dev/key/app",
      note: "hex-32 同时匹配 MD5、去掉连字符的 GUID、git 对象名 —— 没有键名或主机名就不认",
    },
    // ------------------------------------------------ 歧义组：共用裸 sk- 形态
    {
      platform: "openai", display: "OpenAI",
      hostnames: ["api.openai.com"],
      keynames: [String.raw`(?i)^OPENAI_(API_KEY|TOKEN|KEY)$`],
      shape: SK_SHAPE, group: GROUP_OPENAI_COMPATIBLE,
      window: ["openai", "platform.openai.com", "chatgpt"],
      specificity: 30, maskStyle: [3, 4],
      consoleUrl: "https://platform.openai.com/api-keys",
    },
    {
      platform: "deepseek", display: "DeepSeek",
      hostnames: ["api.deepseek.com"],
      keynames: [String.raw`(?i)^DEEPSEEK_(API_KEY|TOKEN|KEY)$`],
      shape: SK_SHAPE, group: GROUP_OPENAI_COMPATIBLE,
      window: ["deepseek", "深度求索"],
      specificity: 30, maskStyle: [3, 4],
      consoleUrl: "https://platform.deepseek.com/api_keys",
    },
    {
      platform: "siliconflow", display: "SiliconFlow",
      hostnames: ["api.siliconflow.cn", "siliconflow.cn"],
      keynames: [String.raw`(?i)^SILICONFLOW_`],
      shape: SK_SHAPE, group: GROUP_OPENAI_COMPATIBLE,
      window: ["siliconflow", "硅基流动"],
      specificity: 30, maskStyle: [3, 4],
      consoleUrl: "https://cloud.siliconflow.cn/account/ak",
    },
    {
      platform: "moonshot", display: "Moonshot / Kimi",
      hostnames: ["api.moonshot.cn", "api.moonshot.ai"],
      keynames: [String.raw`(?i)^(MOONSHOT|KIMI)_`],
      shape: SK_SHAPE, group: GROUP_OPENAI_COMPATIBLE,
      window: ["moonshot", "kimi"],
      specificity: 30, maskStyle: [3, 4],
      consoleUrl: "https://platform.moonshot.cn/console/api-keys",
    },
    {
      platform: "zhipu", display: "智谱 Zhipu / GLM",
      hostnames: ["open.bigmodel.cn", "bigmodel.cn"],
      keynames: [String.raw`(?i)^(ZHIPU|GLM|BIGMODEL)_`],
      shape: SK_SHAPE, group: GROUP_OPENAI_COMPATIBLE,
      window: ["智谱", "bigmodel", "zhipu", "glm"],
      specificity: 30, maskStyle: [3, 4],
      consoleUrl: "https://open.bigmodel.cn/usercenter/apikeys",
    },
    {
      platform: "dashscope", display: "阿里云百炼 DashScope",
      hostnames: ["dashscope.aliyuncs.com", "dashscope.aliyun.com"],
      keynames: [String.raw`(?i)^(DASHSCOPE|ALIYUN|ALIBABA_CLOUD)_`],
      shape: SK_SHAPE, group: GROUP_OPENAI_COMPATIBLE,
      window: ["百炼", "dashscope", "阿里云"],
      specificity: 30, maskStyle: [3, 4],
      consoleUrl: "https://bailian.console.aliyun.com/?apiKey=1",
    },
    // 无形态的标签规则：歧义时的落点。它是一个真平台 id —— 可查询、
    // 可事后用 set-platform 修正。
    {
      platform: "openai-compatible", display: "OpenAI 兼容（未指定厂商）",
      group: GROUP_OPENAI_COMPATIBLE,
      specificity: 0, maskStyle: [3, 4],
      note: "裸 sk- 无法区分厂商时的诚实落点。**绝不猜一个厂商** —— 猜错会把假事实"
        + "写进明文可搜索的永久元数据列",
    },
  ].map(defRule),
);

const LEADING_FLAGS = /^\(\?([ailmsux]+)\)/;

/**
 * 把 Python 风格正则原串编译成 JS RegExp。
 *
 * ①前导 (?i)/(?s) 提取为 JS flags（原串本身保留给 tableHash 对拍）；
 * ②Python 的 $ 匹配串尾**或串尾换行之前**，JS 的 $ 只匹配串尾——把每个
 *   未转义的 $ 换成零宽断言 (?=\n?$)，逐点等价（含交替分支内的 $）；
 * ③规则表其余语法（(?:)、{m,}、字符类、环视）JS 原生支持，原样保留。
 * 遇到不支持的 Python 旗标直接抛错——检测器静默失效是最贵的失败模式。
 */
export function compilePyRegex(pattern) {
  let flags = "";
  let body = pattern;
  const match = LEADING_FLAGS.exec(pattern);
  if (match) {
    for (const ch of match[1]) {
      if (!"ims".includes(ch)) {
        throw new Error(`规则表正则含未支持的 Python 旗标 (?${match[1]}): ${pattern}`);
      }
      flags += ch;
    }
    body = pattern.slice(match[0].length);
  }
  let translated = "";
  for (let i = 0; i < body.length; i += 1) {
    const ch = body[i];
    if (ch === "\\" && i + 1 < body.length) {
      translated += ch + body[i + 1];
      i += 1;
      continue;
    }
    translated += ch === "$" ? "(?=\\n?$)" : ch;
  }
  return new RegExp(translated, flags);
}

export const BY_PLATFORM = new Map(RULES.map((rule) => [rule.platform, rule]));

// 歧义是**推导**出来的，不是声明出来的。判据是「共享同一个 shape 字符串」而不是
// 「挂了同一个 group 标签」——后者会把 sk-proj-（OpenAI 独有、毫不歧义）也算进
// openai_compatible 的候选菜单里。
export const SHAPE_GROUPS = new Map();
for (const rule of RULES) {
  if (!rule.shape) continue;
  if (!SHAPE_GROUPS.has(rule.shape)) SHAPE_GROUPS.set(rule.shape, new Set());
  SHAPE_GROUPS.get(rule.shape).add(rule.platform);
}

// group 标签另有一用：`group:openai_compatible -> deepseek` 这种纠正范围。
// 它按标签聚合，与歧义判定无关。
export const GROUPS = new Map();
for (const rule of RULES) {
  if (!rule.group) continue;
  if (!GROUPS.has(rule.group)) GROUPS.set(rule.group, new Set());
  GROUPS.get(rule.group).add(rule.platform);
}

// 每个歧义组的「诚实落点」：组里那条没有 shape 的标签规则。
export const GROUP_LABEL = new Map();
for (const groupId of GROUPS.keys()) {
  const labelRule = RULES.find((rule) => rule.group === groupId && !rule.shape);
  GROUP_LABEL.set(groupId, labelRule ? labelRule.platform : "");
}

export const HOST_INDEX = new Map();
for (const rule of RULES) {
  for (const hostname of rule.hostnames) {
    const key = hostname.toLowerCase();
    if (!HOST_INDEX.has(key)) HOST_INDEX.set(key, []);
    HOST_INDEX.get(key).push(rule);
  }
}

export const WINDOW_INDEX = RULES.flatMap(
  (rule) => rule.window.map((needle) => [needle.toLowerCase(), rule]),
);

export const KEYNAME_COMPILED = RULES.flatMap(
  (rule) => rule.keynames.map((pattern) => [compilePyRegex(pattern), rule]),
);

export const SHAPE_COMPILED = RULES.filter((rule) => rule.shape)
  .map((rule) => [compilePyRegex(rule.shape), rule]);

export const SHAPE_BY_PLATFORM = new Map(
  SHAPE_COMPILED.map(([pattern, rule]) => [rule.platform, pattern]),
);

/** 这个平台的值形态和眼前这个值相符吗。窗口标题层的形态守门员。 */
export function shapeFits(platform, valueText) {
  const pattern = SHAPE_BY_PLATFORM.get(platform);
  return pattern === undefined || pattern.test(valueText);
}

/**
 * 整张表的指纹。与桌面 kv/detect/rules.py:table_hash 逐字节对拍：
 * 每条规则取 platform/hostnames/keynames/shape/window/group/kind/
 * needs_context/specificity/mask_style 十字段，stableStringify 键排序 +
 * 紧凑分隔符对齐 sort_keys=True + separators，keynames/shape 用带
 * (?i)/(?s) 的 Python 风格原串；非ASCII 不转义与 ensure_ascii=False 一致。
 */
export function tableHash() {
  const payload = RULES.map((rule) => ({
    platform: rule.platform,
    hostnames: rule.hostnames,
    keynames: rule.keynames,
    shape: rule.shape,
    window: rule.window,
    group: rule.group,
    kind: rule.kind,
    needs_context: rule.needsContext,
    specificity: rule.specificity,
    mask_style: rule.maskStyle,
  }));
  return sha256Hex(stableStringify(payload));
}

export const TABLE_HASH = tableHash();

export function displayName(platform) {
  const rule = BY_PLATFORM.get(platform);
  return rule ? rule.display : platform;
}

export function allPlatforms() {
  return RULES.map((rule) => rule.platform);
}

/** 按 group 标签聚合的成员——用于 `group:` 纠正范围与交互菜单。 */
export function groupMembers(group) {
  return [...(GROUPS.get(group) ?? [])].sort();
}

/** 和某个平台共享形态、因而无法靠 shape 区分的其它平台。空=形态独有。 */
export function ambiguousWith(platform) {
  const rule = BY_PLATFORM.get(platform);
  if (!rule || !rule.shape) return [];
  const members = SHAPE_GROUPS.get(rule.shape) ?? new Set();
  return [...members].filter((member) => member !== platform).sort();
}

export function shapeGroupOf(shape) {
  return [...(SHAPE_GROUPS.get(shape) ?? [])].sort();
}

export function maskStyleFor(platform) {
  const rule = BY_PLATFORM.get(platform);
  return rule ? rule.maskStyle : [0, 4];
}
