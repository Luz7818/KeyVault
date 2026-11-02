// 平台规则表 —— 1:1 移植 kv/detect/rules.py（纯数据 + 派生索引）。
//
// 硬约束随移植而来（对应桌面版踩过的坑，语义不得走样）：
// 1. 不给凭据值设长度下限——只在厂商格式确实要求时写 {16,}。
// 2. needs_context 用于「形态本身不可信」的规则（裸 hex-32 = 高德/MD5/GUID/…）。
// 3. 歧义是**推导**出来的：共享同一个 shape 字符串即歧义，而不是看 group 标签——
//    sk-proj- 是 OpenAI 独有形态，绝不进 openai_compatible 的歧义菜单。
//
// Python 正则 → JS 的转换只有一处：行内 (?i)/(?s) 提升为构造 flags。

export const SK_SHAPE = "^sk-[A-Za-z0-9_\\-]{16,}$";

export const GROUP_OPENAI_COMPATIBLE = "openai_compatible";
export const PLATFORM_UNKNOWN = "unknown";
export const PLATFORM_OPENAI_COMPATIBLE = "openai-compatible";

// [source, flags]：JS RegExp 的两段式写法，避免行内 (?i)。
function re(source, flags = "") {
  return new RegExp(source, flags);
}

export class Rule {
  constructor(spec) {
    Object.assign(this, spec);
    this.compiledShape = spec.shape ? re(spec.shape[0], spec.shape[1]) : null;
    this.keynameRes = (spec.keynames || []).map(([s, f]) => re(s, f));
  }
}

export const RULES = [
  new Rule({
    platform: "anthropic", display: "Anthropic",
    hostnames: ["api.anthropic.com"],
    keynames: [["^ANTHROPIC_(API_)?(KEY|TOKEN|AUTH)$", "i"]],
    shape: ["^sk-ant-(?:api03-)?[A-Za-z0-9_\\-]{20,}$", ""],
    window: ["anthropic", "claude"],
    specificity: 90, maskStyle: [8, 4],
    consoleUrl: "https://console.anthropic.com/settings/keys",
  }),
  new Rule({
    platform: "github", display: "GitHub",
    hostnames: ["api.github.com", "github.com"],
    keynames: [["^(GITHUB|GH)_(TOKEN|PAT|API_KEY)$", "i"], ["^UI_PRO_MAX_GITHUB_TOKEN$", "i"]],
    shape: ["^(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})$", "i"],
    window: ["github", "github.com"],
    specificity: 90, maskStyle: [4, 4],
    consoleUrl: "https://github.com/settings/tokens",
  }),
  new Rule({
    platform: "aws-access-key-id", display: "AWS access key ID",
    keynames: [["^AWS_ACCESS_KEY_ID$", "i"]],
    shape: ["^AKIA[0-9A-Z]{16}$", ""],
    window: ["aws", "amazon web services", "iam"],
    specificity: 95, maskStyle: [4, 4],
    note: "这是标识符不是机密本体；配对的 secret access key 另存一条",
  }),
  new Rule({
    platform: "google", display: "Google",
    hostnames: ["generativelanguage.googleapis.com", "googleapis.com", "oauth2.googleapis.com"],
    keynames: [["^(GOOGLE|GEMINI|GOOGLE_FONTS)_API_KEY$", "i"]],
    shape: ["^AIza[0-9A-Za-z_\\-]{35}$", ""],
    window: ["google cloud", "gemini", "aistudio"],
    specificity: 85, maskStyle: [4, 4],
    consoleUrl: "https://aistudio.google.com/app/apikey",
  }),
  new Rule({
    platform: "gitlab", display: "GitLab",
    hostnames: ["gitlab.com"],
    keynames: [["^GITLAB_(TOKEN|PAT|API_KEY)$", "i"]],
    shape: ["^glpat-[A-Za-z0-9_\\-]{20,}$", ""],
    window: ["gitlab"],
    specificity: 90, maskStyle: [6, 4],
  }),
  new Rule({
    platform: "slack", display: "Slack",
    hostnames: ["slack.com"],
    keynames: [["^SLACK_(BOT_)?(TOKEN|WEBHOOK)$", "i"]],
    shape: ["^xox[baprs]-[A-Za-z0-9\\-]{10,}$", ""],
    window: ["slack"],
    specificity: 90, maskStyle: [5, 4],
  }),
  new Rule({
    platform: "vercel", display: "Vercel",
    hostnames: ["api.vercel.com", "vercel.com"],
    keynames: [["^VERCEL_(TOKEN|OIDC_TOKEN)$", "i"]],
    shape: ["^vercel_[A-Za-z0-9]{20,}$", ""],
    window: ["vercel"],
    specificity: 90, maskStyle: [7, 4],
    consoleUrl: "https://vercel.com/account/tokens",
  }),
  new Rule({
    platform: "npm", display: "npm",
    hostnames: ["registry.npmjs.org"],
    keynames: [["^NPM_(TOKEN|AUTH_TOKEN)$", "i"]],
    shape: ["^npm_[A-Za-z0-9]{30,}$", ""],
    window: ["npm", "npmjs"],
    specificity: 90, maskStyle: [4, 4],
  }),
  new Rule({
    platform: "telegram", display: "Telegram Bot",
    hostnames: ["api.telegram.org"],
    keynames: [["^(TELEGRAM|TG)_BOT_(TOKEN|SECRET)$", "i"]],
    shape: ["^[0-9]{6,12}:[A-Za-z0-9_\\-]{30,}$", ""],
    window: ["botfather", "telegram"],
    specificity: 85, maskStyle: [0, 4],
  }),
  new Rule({
    platform: "modelscope", display: "ModelScope",
    hostnames: ["api-inference.modelscope.cn", "modelscope.cn"],
    keynames: [["^MODELSCOPE_", "i"]],
    shape: ["^ms-[A-Za-z0-9\\-]{16,}$", ""],
    window: ["modelscope", "魔搭"],
    specificity: 70, maskStyle: [3, 4],
  }),
  new Rule({
    platform: "wechat-appid", display: "WeChat AppID",
    keynames: [["^(WX|WECHAT)_APP_?ID$", "i"]],
    shape: ["^wx[0-9a-f]{16}$", ""],
    window: ["微信", "wechat", "公众平台"],
    specificity: 80, maskStyle: [2, 4],
    note: "AppID 是标识符；配套的 AppSecret 另存一条",
  }),
  new Rule({
    platform: "stripe", display: "Stripe",
    hostnames: ["api.stripe.com"],
    keynames: [["^STRIPE_(SECRET|API|PUBLISHABLE)_KEY$", "i"]],
    shape: ["^(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{20,}$", ""],
    window: ["stripe"],
    specificity: 90, maskStyle: [9, 4],
  }),
  new Rule({
    platform: "openai-project", display: "OpenAI project key",
    hostnames: ["api.openai.com"],
    shape: ["^sk-proj-[A-Za-z0-9_\\-]{20,}$", ""],
    window: ["openai", "platform.openai.com"],
    specificity: 88, maskStyle: [8, 4],
    note: "sk-proj- 前缀是 OpenAI 独有的，所以它不歧义 —— 不进 openai_compatible 组",
  }),
  new Rule({
    platform: "jwt", display: "JWT（签发方未定）",
    keynames: [["_JWT$", "i"], ["^OIDC_TOKEN$", "i"]],
    shape: ["^eyJ[A-Za-z0-9_\\-]+\\.eyJ[A-Za-z0-9_\\-]+\\.[A-Za-z0-9_\\-]*$", ""],
    specificity: 50, maskStyle: [0, 4], kind: "token",
    note: "JWT 的平台信息在 payload 里，需要解 base64 才知道；v1 不做",
  }),
  new Rule({
    platform: "pem-private-key", display: "PEM 私钥",
    keynames: [["_PRIVATE_KEY$", "i"]],
    shape: ["^-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----\\s*$", "s"],
    specificity: 95, maskStyle: [0, 0], kind: "pem",
  }),
  new Rule({
    platform: "connection-string", display: "带密码的连接串",
    shape: ["^[a-z][a-z0-9+.\\-]*://[^:/@\\s]+:[^@\\s]+@[^\\s]+$", "i"],
    specificity: 70, maskStyle: [0, 0], kind: "connstring",
    note: "形如 scheme://user:pass@host —— 这是真凭据，不是占位符",
  }),
  new Rule({
    platform: "amap", display: "高德 AMap",
    hostnames: ["restapi.amap.com", "lbs.amap.com", "webapi.amap.com"],
    keynames: [["^(AMAP|GAODE)_", "i"]],
    shape: ["^[0-9a-f]{32}$", "i"],
    window: ["高德", "amap", "lbs.amap"],
    needsContext: true,
    specificity: 40, maskStyle: [0, 4],
    consoleUrl: "https://console.amap.com/dev/key/app",
    note: "hex-32 同时匹配 MD5、去掉连字符的 GUID、git 对象名 —— 没有键名或主机名就不认",
  }),
  new Rule({
    platform: "openai", display: "OpenAI",
    hostnames: ["api.openai.com"],
    keynames: [["^OPENAI_(API_KEY|TOKEN|KEY)$", "i"]],
    shape: [SK_SHAPE, ""],
    group: GROUP_OPENAI_COMPATIBLE,
    window: ["openai", "platform.openai.com", "chatgpt"],
    specificity: 30, maskStyle: [3, 4],
    consoleUrl: "https://platform.openai.com/api-keys",
  }),
  new Rule({
    platform: "deepseek", display: "DeepSeek",
    hostnames: ["api.deepseek.com"],
    keynames: [["^DEEPSEEK_(API_KEY|TOKEN|KEY)$", "i"]],
    shape: [SK_SHAPE, ""],
    group: GROUP_OPENAI_COMPATIBLE,
    window: ["deepseek", "深度求索"],
    specificity: 30, maskStyle: [3, 4],
    consoleUrl: "https://platform.deepseek.com/api_keys",
  }),
  new Rule({
    platform: "siliconflow", display: "SiliconFlow",
    hostnames: ["api.siliconflow.cn", "siliconflow.cn"],
    keynames: [["^SILICONFLOW_", "i"]],
    shape: [SK_SHAPE, ""],
    group: GROUP_OPENAI_COMPATIBLE,
    window: ["siliconflow", "硅基流动"],
    specificity: 30, maskStyle: [3, 4],
    consoleUrl: "https://cloud.siliconflow.cn/account/ak",
  }),
  new Rule({
    platform: "moonshot", display: "Moonshot / Kimi",
    hostnames: ["api.moonshot.cn", "api.moonshot.ai"],
    keynames: [["^(MOONSHOT|KIMI)_", "i"]],
    shape: [SK_SHAPE, ""],
    group: GROUP_OPENAI_COMPATIBLE,
    window: ["moonshot", "kimi"],
    specificity: 30, maskStyle: [3, 4],
    consoleUrl: "https://platform.moonshot.cn/console/api-keys",
  }),
  new Rule({
    platform: "zhipu", display: "智谱 Zhipu / GLM",
    hostnames: ["open.bigmodel.cn", "bigmodel.cn"],
    keynames: [["^(ZHIPU|GLM|BIGMODEL)_", "i"]],
    shape: [SK_SHAPE, ""],
    group: GROUP_OPENAI_COMPATIBLE,
    window: ["智谱", "bigmodel", "zhipu", "glm"],
    specificity: 30, maskStyle: [3, 4],
    consoleUrl: "https://open.bigmodel.cn/usercenter/apikeys",
  }),
  new Rule({
    platform: "dashscope", display: "阿里云百炼 DashScope",
    hostnames: ["dashscope.aliyuncs.com", "dashscope.aliyun.com"],
    keynames: [["^(DASHSCOPE|ALIYUN|ALIBABA_CLOUD)_", "i"]],
    shape: [SK_SHAPE, ""],
    group: GROUP_OPENAI_COMPATIBLE,
    window: ["百炼", "dashscope", "阿里云"],
    specificity: 30, maskStyle: [3, 4],
    consoleUrl: "https://bailian.console.aliyun.com/?apiKey=1",
  }),
  // 无形态的标签规则：歧义时的诚实落点。绝不猜一个厂商。
  new Rule({
    platform: "openai-compatible", display: "OpenAI 兼容（未指定厂商）",
    group: GROUP_OPENAI_COMPATIBLE,
    specificity: 0, maskStyle: [3, 4],
    note: "裸 sk- 无法区分厂商时的诚实落点",
  }),
];

export const BY_PLATFORM = new Map(RULES.map((r) => [r.platform, r]));

// 歧义按「共享同一个 shape 字符串」推导（不是 group 标签）。
export const SHAPE_GROUPS = new Map();
for (const rule of RULES) {
  if (!rule.shape) continue;
  const key = rule.shape[0];
  const bucket = SHAPE_GROUPS.get(key) ?? new Set();
  bucket.add(rule.platform);
  SHAPE_GROUPS.set(key, bucket);
}

export const GROUPS = new Map();
for (const rule of RULES) {
  if (!rule.group) continue;
  const bucket = GROUPS.get(rule.group) ?? new Set();
  bucket.add(rule.platform);
  GROUPS.set(rule.group, bucket);
}

// 每个歧义组的「诚实落点」：组里那条没有 shape 的标签规则。
export const GROUP_LABEL = new Map();
for (const gid of GROUPS.keys()) {
  const label = RULES.find((r) => r.group === gid && !r.shape);
  GROUP_LABEL.set(gid, label ? label.platform : "");
}

function indexBy(keyOf) {
  const index = new Map();
  for (const rule of RULES) {
    for (const key of keyOf(rule)) {
      const bucket = index.get(key) ?? [];
      bucket.push(rule);
      index.set(key, bucket);
    }
  }
  return index;
}

export const HOST_INDEX = indexBy((r) => (r.hostnames || []).map((h) => h.toLowerCase()));

export const WINDOW_INDEX = RULES.flatMap(
  (rule) => (rule.window || []).map((needle) => [needle.toLowerCase(), rule]),
);

export const KEYNAME_COMPILED = RULES.flatMap(
  (rule) => rule.keynameRes.map((pattern) => [pattern, rule]),
);

export const SHAPE_COMPILED = RULES.filter((r) => r.compiledShape)
  .map((rule) => [rule.compiledShape, rule]);

export const SHAPE_BY_PLATFORM = new Map(
  SHAPE_COMPILED.map(([pattern, rule]) => [rule.platform, pattern]),
);

/** 环境证据（窗口标题）不能推翻值证据：形态不符即不符。 */
export function shapeFits(platform, valueText) {
  const pattern = SHAPE_BY_PLATFORM.get(platform);
  return pattern === undefined || pattern.test(valueText);
}

export function displayName(platform) {
  const rule = BY_PLATFORM.get(platform);
  return rule ? rule.display : platform;
}

export function allPlatforms() {
  return RULES.map((r) => r.platform);
}

export function groupMembers(group) {
  return [...(GROUPS.get(group) ?? [])].sort();
}

export function ambiguousWith(platform) {
  const rule = BY_PLATFORM.get(platform);
  if (!rule || !rule.shape) return [];
  const members = SHAPE_GROUPS.get(rule.shape[0]) ?? new Set();
  return [...members].filter((m) => m !== platform).sort();
}

export function maskStyleFor(platform) {
  const rule = BY_PLATFORM.get(platform);
  return rule ? rule.maskStyle : [0, 4];
}
