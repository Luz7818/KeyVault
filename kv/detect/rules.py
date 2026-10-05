"""平台规则表 —— 纯数据，无逻辑。

**加一个平台就是往 RULES 里加一行，别处一行都不用改。** 交互菜单、kv fix、
--platform 补全列表、golden 语料的歧义断言全部从这张表推导出来。

三条从现有工程继承的硬约束（都对应真实踩过的坑）：

1. **不给凭据值设长度下限。** 曾经卡过 15 字符下限，一把 15 字符的真令牌刚好漏网。
   只在厂商格式确实要求时才写 {16,}。
2. **正则只在纯 Python re 里跑，绝不 shell 出去。** 曾经把 (?i) 内联进
   `git grep -E`，git 不支持该语法，大小写不敏感规则全部静默失效，而扫描器
   仍然报「全仓干净」。所以本文件的 (?i) 是有效的，且 test_arch.py 禁止 git grep。
3. **needs_context 用于「形态本身不可信」的规则。** 裸 hex-32 既是高德 Key 也是
   MD5 / 去掉连字符的 GUID / git 对象名的一部分。没有键名或主机名佐证时判
   unknown 并拒绝落盘 —— 那是「没识别出来」，不是「被静默放过」。失败必须响亮。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

# 裸 sk- 的形态在 OpenAI / DeepSeek / SiliconFlow / Moonshot / 智谱 / 灵积之间
# **完全相同**，靠正则永远分不出来。所以它们共用这一个形态，并挂在同一个歧义组里。
SK_SHAPE = r"^sk-[A-Za-z0-9_\-]{16,}$"


@dataclass(frozen=True)
class Rule:
    platform: str
    display: str
    hostnames: tuple[str, ...] = ()
    keynames: tuple[str, ...] = ()
    shape: str | None = None
    window: tuple[str, ...] = ()
    group: str | None = None
    kind: str = "token"
    needs_context: bool = False
    specificity: int = 0
    mask_style: tuple[int, int] = (0, 4)
    note: str = ""
    console_url: str = ""

    def compiled_shape(self):
        return re.compile(self.shape) if self.shape else None


GROUP_OPENAI_COMPATIBLE = "openai_compatible"

RULES: tuple[Rule, ...] = (
    # ------------------------------------------------ 形态独有、可直接判定
    Rule(
        platform="anthropic", display="Anthropic",
        hostnames=("api.anthropic.com",),
        keynames=(r"(?i)^ANTHROPIC_(API_)?(KEY|TOKEN|AUTH)$",),
        shape=r"^sk-ant-(?:api03-)?[A-Za-z0-9_\-]{20,}$",
        window=("anthropic", "claude"),
        specificity=90, mask_style=(8, 4),
        console_url="https://console.anthropic.com/settings/keys",
    ),
    Rule(
        platform="github", display="GitHub",
        hostnames=("api.github.com", "github.com"),
        keynames=(r"(?i)^(GITHUB|GH)_(TOKEN|PAT|API_KEY)$", r"(?i)^UI_PRO_MAX_GITHUB_TOKEN$"),
        shape=r"(?i)^(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})$",
        window=("github", "github.com"),
        specificity=90, mask_style=(4, 4),
        console_url="https://github.com/settings/tokens",
    ),
    Rule(
        platform="aws-access-key-id", display="AWS access key ID",
        keynames=(r"(?i)^AWS_ACCESS_KEY_ID$",),
        shape=r"^AKIA[0-9A-Z]{16}$",
        window=("aws", "amazon web services", "iam"),
        specificity=95, mask_style=(4, 4),
        note="这是标识符不是机密本体；配对的 secret access key 另存一条",
    ),
    Rule(
        platform="google", display="Google",
        hostnames=("generativelanguage.googleapis.com", "googleapis.com",
                   "oauth2.googleapis.com"),
        keynames=(r"(?i)^(GOOGLE|GEMINI|GOOGLE_FONTS)_API_KEY$",),
        shape=r"^AIza[0-9A-Za-z_\-]{35}$",
        window=("google cloud", "gemini", "aistudio"),
        specificity=85, mask_style=(4, 4),
        console_url="https://aistudio.google.com/app/apikey",
    ),
    Rule(
        platform="gitlab", display="GitLab",
        hostnames=("gitlab.com",),
        keynames=(r"(?i)^GITLAB_(TOKEN|PAT|API_KEY)$",),
        shape=r"^glpat-[A-Za-z0-9_\-]{20,}$",
        window=("gitlab",),
        specificity=90, mask_style=(6, 4),
    ),
    Rule(
        platform="slack", display="Slack",
        hostnames=("slack.com",),
        keynames=(r"(?i)^SLACK_(BOT_)?(TOKEN|WEBHOOK)$",),
        shape=r"^xox[baprs]-[A-Za-z0-9\-]{10,}$",
        window=("slack",),
        specificity=90, mask_style=(5, 4),
    ),
    Rule(
        platform="vercel", display="Vercel",
        hostnames=("api.vercel.com", "vercel.com"),
        keynames=(r"(?i)^VERCEL_(TOKEN|OIDC_TOKEN)$",),
        shape=r"^vercel_[A-Za-z0-9]{20,}$",
        window=("vercel",),
        specificity=90, mask_style=(7, 4),
        console_url="https://vercel.com/account/tokens",
    ),
    Rule(
        platform="npm", display="npm",
        hostnames=("registry.npmjs.org",),
        keynames=(r"(?i)^NPM_(TOKEN|AUTH_TOKEN)$",),
        shape=r"^npm_[A-Za-z0-9]{30,}$",
        window=("npm", "npmjs"),
        specificity=90, mask_style=(4, 4),
    ),
    Rule(
        platform="telegram", display="Telegram Bot",
        hostnames=("api.telegram.org",),
        keynames=(r"(?i)^(TELEGRAM|TG)_BOT_(TOKEN|SECRET)$",),
        shape=r"^[0-9]{6,12}:[A-Za-z0-9_\-]{30,}$",
        window=("botfather", "telegram"),
        specificity=85, mask_style=(0, 4),
    ),
    Rule(
        platform="modelscope", display="ModelScope",
        hostnames=("api-inference.modelscope.cn", "modelscope.cn"),
        keynames=(r"(?i)^MODELSCOPE_",),
        shape=r"^ms-[A-Za-z0-9\-]{16,}$",
        window=("modelscope", "魔搭"),
        specificity=70, mask_style=(3, 4),
    ),
    Rule(
        platform="wechat-appid", display="WeChat AppID",
        keynames=(r"(?i)^(WX|WECHAT)_APP_?ID$",),
        shape=r"^wx[0-9a-f]{16}$",
        window=("微信", "wechat", "公众平台"),
        specificity=80, mask_style=(2, 4),
        note="AppID 是标识符；配套的 AppSecret 另存一条",
    ),
    Rule(
        platform="stripe", display="Stripe",
        hostnames=("api.stripe.com",),
        keynames=(r"(?i)^STRIPE_(SECRET|API|PUBLISHABLE)_KEY$",),
        shape=r"^(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{20,}$",
        window=("stripe",),
        specificity=90, mask_style=(9, 4),
    ),
    Rule(
        platform="openai-project", display="OpenAI project key",
        hostnames=("api.openai.com",),
        shape=r"^sk-proj-[A-Za-z0-9_\-]{20,}$",
        window=("openai", "platform.openai.com"),
        specificity=88, mask_style=(8, 4),
        note="sk-proj- 前缀是 OpenAI 独有的，所以它**不**歧义 —— 不进 openai_compatible 组",
    ),
    Rule(
        platform="jwt", display="JWT（签发方未定）",
        keynames=(r"(?i)_JWT$", r"(?i)^OIDC_TOKEN$"),
        shape=r"^eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*$",
        specificity=50, mask_style=(0, 4), kind="token",
        note="JWT 的平台信息在 payload 里，需要解 base64 才知道；v1 不做",
    ),
    Rule(
        platform="pem-private-key", display="PEM 私钥",
        keynames=(r"(?i)_PRIVATE_KEY$",),
        shape=r"(?s)^-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----\s*$",
        specificity=95, mask_style=(0, 0), kind="pem",
    ),
    Rule(
        platform="connection-string", display="带密码的连接串",
        shape=r"^[a-z][a-z0-9+.\-]*://[^:/@\s]+:[^@\s]+@[^\s]+$",
        specificity=70, mask_style=(0, 0), kind="connstring",
        note="形如 scheme://user:pass@host —— 这是真凭据，不是占位符",
    ),
    # ------------------------------------------------ 形态需要上下文佐证
    Rule(
        platform="amap", display="高德 AMap",
        hostnames=("restapi.amap.com", "lbs.amap.com", "webapi.amap.com"),
        keynames=(r"(?i)^(AMAP|GAODE)_",),
        shape=r"(?i)^[0-9a-f]{32}$",
        window=("高德", "amap", "lbs.amap"),
        needs_context=True,
        specificity=40, mask_style=(0, 4),
        console_url="https://console.amap.com/dev/key/app",
        note="hex-32 同时匹配 MD5、去掉连字符的 GUID、git 对象名 —— 没有键名或主机名就不认",
    ),
    # ------------------------------------------------ 歧义组：共用裸 sk- 形态
    Rule(
        platform="openai", display="OpenAI",
        hostnames=("api.openai.com",),
        keynames=(r"(?i)^OPENAI_(API_KEY|TOKEN|KEY)$",),
        shape=SK_SHAPE, group=GROUP_OPENAI_COMPATIBLE,
        window=("openai", "platform.openai.com", "chatgpt"),
        specificity=30, mask_style=(3, 4),
        console_url="https://platform.openai.com/api-keys",
    ),
    Rule(
        platform="deepseek", display="DeepSeek",
        hostnames=("api.deepseek.com",),
        keynames=(r"(?i)^DEEPSEEK_(API_KEY|TOKEN|KEY)$",),
        shape=SK_SHAPE, group=GROUP_OPENAI_COMPATIBLE,
        window=("deepseek", "深度求索"),
        specificity=30, mask_style=(3, 4),
        console_url="https://platform.deepseek.com/api_keys",
    ),
    Rule(
        platform="siliconflow", display="SiliconFlow",
        hostnames=("api.siliconflow.cn", "siliconflow.cn"),
        keynames=(r"(?i)^SILICONFLOW_",),
        shape=SK_SHAPE, group=GROUP_OPENAI_COMPATIBLE,
        window=("siliconflow", "硅基流动"),
        specificity=30, mask_style=(3, 4),
        console_url="https://cloud.siliconflow.cn/account/ak",
    ),
    Rule(
        platform="moonshot", display="Moonshot / Kimi",
        hostnames=("api.moonshot.cn", "api.moonshot.ai"),
        keynames=(r"(?i)^(MOONSHOT|KIMI)_",),
        shape=SK_SHAPE, group=GROUP_OPENAI_COMPATIBLE,
        window=("moonshot", "kimi"),
        specificity=30, mask_style=(3, 4),
        console_url="https://platform.moonshot.cn/console/api-keys",
    ),
    Rule(
        platform="zhipu", display="智谱 Zhipu / GLM",
        hostnames=("open.bigmodel.cn", "bigmodel.cn"),
        keynames=(r"(?i)^(ZHIPU|GLM|BIGMODEL)_",),
        shape=SK_SHAPE, group=GROUP_OPENAI_COMPATIBLE,
        window=("智谱", "bigmodel", "zhipu", "glm"),
        specificity=30, mask_style=(3, 4),
        console_url="https://open.bigmodel.cn/usercenter/apikeys",
    ),
    Rule(
        platform="dashscope", display="阿里云百炼 DashScope",
        hostnames=("dashscope.aliyuncs.com", "dashscope.aliyun.com"),
        keynames=(r"(?i)^(DASHSCOPE|ALIYUN|ALIBABA_CLOUD)_",),
        shape=SK_SHAPE, group=GROUP_OPENAI_COMPATIBLE,
        window=("百炼", "dashscope", "阿里云"),
        specificity=30, mask_style=(3, 4),
        console_url="https://bailian.console.aliyun.com/?apiKey=1",
    ),
    # 无形态的标签规则：歧义时的落点。它是一个真平台 id —— 可查询
    # （kv list --platform openai-compatible）、可事后用 kv set-platform 修正。
    Rule(
        platform="openai-compatible", display="OpenAI 兼容（未指定厂商）",
        group=GROUP_OPENAI_COMPATIBLE,
        specificity=0, mask_style=(3, 4),
        note="裸 sk- 无法区分厂商时的诚实落点。**绝不猜一个厂商** —— 猜错会把假事实"
             "写进明文可搜索的永久元数据列",
    ),
)

BY_PLATFORM: dict[str, Rule] = {r.platform: r for r in RULES}

# 歧义是**推导**出来的，不是声明出来的。判据是「共享同一个 shape 字符串」而不是
# 「挂了同一个 group 标签」—— 后者会把 sk-proj-（OpenAI 独有、毫不歧义）也算进
# openai_compatible 的候选菜单里。于是加一个 Together AI 就是往 RULES 里加一行
# 带 shape=SK_SHAPE 的 Rule，歧义菜单、kv fix、golden 语料的候选断言全部自动更新。
SHAPE_GROUPS: dict[str, frozenset[str]] = {}
for _rule in RULES:
    if _rule.shape:
        _bucket = SHAPE_GROUPS.setdefault(_rule.shape, frozenset())
        SHAPE_GROUPS[_rule.shape] = _bucket | {_rule.platform}

# group 标签另有一用：`group:openai_compatible -> deepseek` 这种纠正范围。
# 它按标签聚合，与歧义判定无关。
GROUPS: dict[str, frozenset[str]] = {}
for _rule in RULES:
    if _rule.group:
        _bucket = GROUPS.setdefault(_rule.group, frozenset())
        GROUPS[_rule.group] = _bucket | {_rule.platform}

# 每个歧义组的「诚实落点」：组里那条没有 shape 的标签规则。
# openai_compatible -> "openai-compatible"，它是一个真平台 id，可查询也可事后修正。
GROUP_LABEL: dict[str, str] = {
    gid: next((r.platform for r in RULES if r.group == gid and not r.shape), "")
    for gid in GROUPS
}


def _index_by(keyfunc) -> dict:
    index: dict = {}
    for rule in RULES:
        for key in keyfunc(rule):
            index.setdefault(key, []).append(rule)
    return {k: tuple(v) for k, v in index.items()}


HOST_INDEX: dict[str, tuple[Rule, ...]] = _index_by(
    lambda r: (h.lower() for h in r.hostnames)
)

WINDOW_INDEX: tuple[tuple[str, Rule], ...] = tuple(
    (needle.lower(), rule) for rule in RULES for needle in rule.window
)

KEYNAME_COMPILED: tuple[tuple[re.Pattern, Rule], ...] = tuple(
    (re.compile(pattern), rule) for rule in RULES for pattern in rule.keynames
)

SHAPE_COMPILED: tuple[tuple[re.Pattern, Rule], ...] = tuple(
    (re.compile(rule.shape), rule) for rule in RULES if rule.shape
)

SHAPE_BY_PLATFORM: dict[str, re.Pattern] = {
    rule.platform: pattern for pattern, rule in SHAPE_COMPILED
}


def shape_fits(platform: str, value_text: str) -> bool:
    """这个平台的值形态和眼前这个值相符吗。

    窗口标题那一层要用它：标题里的关键词是**环境**证据，不能推翻**值**证据。
    没有它的后果是实测出来的 —— 裸 sk- 配上标题 "OpenAI API keys" 会被判成
    openai-project，只因为后者 specificity 更高，而 sk-proj- 形态根本不符。
    """
    pattern = SHAPE_BY_PLATFORM.get(platform)
    return pattern is None or bool(pattern.match(value_text))


def table_hash() -> str:
    """整张表的指纹。golden 自检通过后把它记进 setting，之后 kv scan 会拒绝
    在表变过而自检没重跑的情况下运行 —— 检测器静默失效是本设计最贵的失败模式。"""
    payload = json.dumps(
        [
            {
                "platform": r.platform, "hostnames": list(r.hostnames),
                "keynames": list(r.keynames), "shape": r.shape,
                "window": list(r.window), "group": r.group, "kind": r.kind,
                "needs_context": r.needs_context, "specificity": r.specificity,
                "mask_style": list(r.mask_style),
            }
            for r in RULES
        ],
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


TABLE_HASH = table_hash()


def display_name(platform: str) -> str:
    rule = BY_PLATFORM.get(platform)
    return rule.display if rule else platform


def all_platforms() -> tuple[str, ...]:
    return tuple(r.platform for r in RULES)


def group_members(group: str) -> tuple[str, ...]:
    """按 group 标签聚合的成员 —— 用于 `group:` 纠正范围与交互菜单。"""
    return tuple(sorted(GROUPS.get(group, ())))


def ambiguous_with(platform: str) -> tuple[str, ...]:
    """和某个平台共享形态、因而无法靠 shape 区分的其它平台。

    返回空表示这个平台的形态是独有的、shape 命中即可直接判定。
    """
    rule = BY_PLATFORM.get(platform)
    if not rule or not rule.shape:
        return ()
    members = SHAPE_GROUPS.get(rule.shape, frozenset())
    return tuple(sorted(m for m in members if m != platform))


def shape_group_of(shape: str) -> tuple[str, ...]:
    return tuple(sorted(SHAPE_GROUPS.get(shape, ())))


def mask_style_for(platform: str) -> tuple[int, int]:
    rule = BY_PLATFORM.get(platform)
    return rule.mask_style if rule else (0, 4)
