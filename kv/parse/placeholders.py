"""占位符与非机密闸门。

**它是闸门，不是打分器**：占位符产生 Rejection（带原因，不含值），绝不产生一个
低置信的 Candidate。低置信候选会被存进库，而一个 `sk-xxxxxxxx` 存进去之后，
半年后没人记得那是模板还是真 key。

两条从现有工程继承的硬约束：

* **白名单必须窄。** 判「不是机密」只在可枚举的上下文里发生（.git/ 下、锁文件、
  integrity 字段），外加 40/64 位纯 hex 这种确定性的哈希形态。曾经加过一条
  「看起来像普通标识符」的宽规则，把一把 32 位 hex 的真高德 Key 一起放过了。
* **不设长度下限。** 曾经卡过 15 字符下限，一把 15 字符的真令牌刚好漏网。
  判别占位符靠的是模式，不是长度。

还有一个顺序陷阱：`https://user:pass@host` 是**真凭据**。所以连接串豁免必须跑在
`^https?://` 这条占位符规则**之前**，否则所有带密码的 URL 都会被当成「这是个网址」
而放过。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REASON_PLACEHOLDER = "placeholder"
REASON_NOT_A_SECRET = "not-a-secret"
REASON_EMPTY = "empty"
REASON_TOO_LONG = "too-long"

MAX_VALUE_LEN = 20000

# 连接串豁免。必须在 URL 占位符规则之前跑。
CONNECTION_STRING = re.compile(r"(?i)^[a-z][a-z0-9+.\-]*://[^:/@\s]+:[^@\s]+@[^\s]+$")

PLACEHOLDER_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"(?i)^sk-(?:key|xxx+|your|test|demo|prod|live|please|请)[a-z0-9_\-]*$"), "sk- 模板"),
    (re.compile(r"(?i)^(?:x{4,}|\*{3,}|-{3,}|_{3,}|\.{3,}|0{4,}|1{4,})$"), "重复字符填充"),
    (re.compile(r"(?i)your[-_]?(?:api|secret|access)?[-_]?(?:key|token|secret)"), "your-key 模板"),
    (re.compile(r"(?i)(?:^|[^a-z0-9])(?:changeme|change_me|change-me|replace_?me|replaceme)(?:[^a-z0-9]|$)"), "change_me 模板"),
    (re.compile(r"(?i)(?<![a-z0-9])(?:dummy|fake|sample|example|placeholder|todo|tbd|fixme|none-yet)(?![a-z])"), "dummy/sample 模板"),
    (re.compile(r"(?i)insert[-_ ]?here"), "insert-here 模板"),
    (re.compile(r"请替换|请填写|替换为|填入|占位|示例|待填|你的密钥|你的令牌"), "中文占位提示"),
    (re.compile(r"^<[^>]*>$"), "尖括号占位"),
    (re.compile(r"^\$\{[^}]*\}$"), "shell 变量展开"),
    # 嵌在中间也要拦：`OPENAI_API_KEY=${OPENAI_API_KEY}` 是 .env 里最常见的
    # 「看起来配好了其实没配」，只匹配整串会漏掉带前缀的写法。
    (re.compile(r"\$\{[^}]*\}"), "含 shell 变量展开"),
    (re.compile(r"%[A-Za-z_][A-Za-z0-9_]*%"), "含 Windows 环境变量展开"),
    (re.compile(r"^%[A-Za-z_][A-Za-z0-9_]*%$"), "Windows 环境变量展开"),
    (re.compile(r"^\$[A-Za-z_][A-Za-z0-9_]*$"), "shell 变量引用"),
    (re.compile(r"(?i)^(?:none|null|nil|undefined|true|false|nan|empty|todo)$"), "字面空值"),
    (re.compile(r"^(?:str|int|bool|float|bytes|list|dict|set|tuple|Optional|List|Dict|Set|Tuple|Any|Union)\b"), "类型标注"),
    (re.compile(r"(?i)^(?:https?|ftp)://"), "裸 URL（不含凭据）"),
    (re.compile(r"(?i)(?:^|[./@])example\.(?:com|org|net|cn)(?:[./]|$)"), "example.com 域名"),
    (re.compile(r"(?i)^(?:api[_-]?key|token|secret|password|passwd)\s*$"), "只有键名没有值"),
)

# 确定性的哈希形态 —— 与上下文无关，一律不是凭据。
GIT_SHA1 = re.compile(r"^[0-9a-f]{40}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
SHA512_HEX = re.compile(r"^[0-9a-f]{128}$")
UUID_FORM = re.compile(r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
HEX_RUN = re.compile(r"(?i)^[0-9a-f]{16,}$")
DECIMAL_ONLY = re.compile(r"^[0-9]+$")

# 可枚举的「这里的 hex 不是凭据」上下文。窄是故意的。
LOCK_FILES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "go.sum", "poetry.lock",
    "composer.lock", "cargo.lock", "gemfile.lock", "pipfile.lock", "uv.lock",
})
INTEGRITY_HINT = re.compile(r"(?i)\b(?:integrity|sha1|sha256|sha512|checksum|digest|etag)\b")

# 键名拒绝清单。**必须是拒绝清单，不能是允许清单** —— 现有工程踩过一次
# 「键名过滤误杀真凭据」：它用了允许清单，于是任何没被想到的键名一律放过或一律
# 杀掉，两个方向都错。这里只排掉「明确不可能是凭据」的键名，其余一律放行。
#
# 清单必须窄。曾经写过 `_id$`，结果把 AWS_ACCESS_KEY_ID 一起杀了 —— 那正是
# 上面那个坑的另一种长相。所以：不排 `_id$`、不排任何含 key/token/secret 的键名，
# 并且 03-negative.jsonl 里有一条专门的回归用例守着这件事。
NON_CREDENTIAL_KEY = re.compile(
    r"(?i)(?:^(?:model|models|timeout|retries|retry|region|version|name|id|host|port|"
    r"scheme|transport|workspace|debug|verbose|log_?level|env|environment|stage|"
    r"user|username|tenant|project|app_?name|site|locale|lang|timezone|tz)$"
    r"|_model$|_models$|_timeout$|_retries$|_region$|_version$"
    r"|_host$|_port$|_url$|_uri$|_endpoint$|_base$|_dir$|_path$|_file$|_mode$"
    r"|_enabled$|_flag$|_interval(?:_sec)?$|_max_tokens$|_min_call_interval(?:_sec)?$"
    r"|_transport$|_encoding$|_format$|_type$|_level$|_count$|_limit$"
    r"|^max_|^min_|^default_|^allowed_|^cors_)"
)


def is_non_credential_key(key_name: str | None) -> bool:
    """这个键名明确不是凭据（MODEL / TIMEOUT / BASE_URL / MAX_TOKENS ...）。

    只在有键名的场合用（dotenv / kvline / json）。裸值没有键名，不适用。
    """
    return bool(key_name) and bool(NON_CREDENTIAL_KEY.search(key_name or ""))


CREDENTIAL_KEY_HINT = re.compile(
    r"(?i)(?:key|token|secret|passw|passwd|pwd|credential|apikey|api_key"
    r"|auth|cert|private|signature|salt|seed|dsn)"
)


def is_credential_key_name(key_name: str | None) -> bool:
    """键名看起来像凭据吗。**宽松**判定 —— 这是用来决定「要不要把同块的主机名
    传播给它」，判错的代价是少传播一次（保守），不是误杀。"""
    if not key_name:
        return False
    if is_non_credential_key(key_name):
        return False
    return bool(CREDENTIAL_KEY_HINT.search(key_name))


@dataclass(frozen=True)
class Context:
    """值来自哪里。只带非机密的定位信息。"""

    source: str = "manual"
    path: str | None = None
    line_no: int | None = None
    line_text: str = ""
    key_name: str | None = None

    @property
    def file_name(self) -> str:
        if not self.path:
            return ""
        return self.path.replace("\\", "/").rsplit("/", 1)[-1]

    def in_dot_git(self) -> bool:
        return bool(self.path) and "/.git/" in (self.path.replace("\\", "/") + "/")

    def enumerable_hex_context(self) -> bool:
        """这个上下文里的 hex 串可以确定性地判为「不是凭据」。

        只有三种：.git/ 内部、锁文件、以及行内出现 integrity/checksum 这类词。
        **不含**「看起来像普通标识符」—— 那条规则曾经放走过一把真高德 Key。
        """
        if self.in_dot_git():
            return True
        if self.file_name.lower() in LOCK_FILES:
            return True
        return bool(INTEGRITY_HINT.search(self.line_text))


def classify(value: str, context: Context | None = None) -> tuple[str, str] | None:
    """返回 (原因码, 人话说明)，或 None 表示放行。

    返回值里**不含 value 本身** —— 拒绝原因会被打印出来。
    """
    context = context or Context()
    text = value.strip()

    if not text:
        return (REASON_EMPTY, "空值")
    if len(text) > MAX_VALUE_LEN:
        return (REASON_TOO_LONG, f"超过 {MAX_VALUE_LEN} 字符，不像凭据")

    if is_non_credential_key(context.key_name):
        return (REASON_NOT_A_SECRET, f"键名 {context.key_name} 明确不是凭据")

    # 顺序要紧：连接串是真凭据，必须先豁免，再跑 URL 占位符规则。
    if CONNECTION_STRING.match(text):
        return None

    for pattern, why in PLACEHOLDER_PATTERNS:
        if pattern.search(text):
            return (REASON_PLACEHOLDER, why)

    if GIT_SHA1.match(text) or SHA256_HEX.match(text) or SHA512_HEX.match(text):
        return (REASON_NOT_A_SECRET, "纯哈希形态（40/64/128 位 hex）")
    if UUID_FORM.match(text):
        return (REASON_NOT_A_SECRET, "UUID 形态")
    if DECIMAL_ONLY.match(text):
        return (REASON_NOT_A_SECRET, "纯数字")
    if HEX_RUN.match(text) and context.enumerable_hex_context():
        return (REASON_NOT_A_SECRET, f"可枚举上下文里的 hex 串（{context.file_name or '.git'}）")

    return None


def is_acceptable(value: str, context: Context | None = None) -> bool:
    return classify(value, context) is None
