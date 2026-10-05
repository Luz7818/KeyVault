"""curl / HTTPie 命令行。

这是**信号最值钱**的解析器：命令行里的 URL 给出主机名，而主机名是唯一能把裸
`sk-` 精确判成 DeepSeek 还是 OpenAI 的证据。控制台「复制为 curl」出来的命令因此
比复制裸 key 有用得多。
"""

from __future__ import annotations

import re
import shlex
from typing import Iterator

from kv.model import Candidate, Rejection
from kv.parse.candidate import emit, hosts_in
from kv.parse.placeholders import Context

CURL_START = re.compile(r"(?i)^\s*(?:curl|http|https)\b")

HEADER_FLAGS = frozenset({"-h", "--header"})
USER_FLAGS = frozenset({"-u", "--user"})
VALUE_FLAGS = frozenset({
    "-d", "--data", "--data-raw", "--data-binary", "--data-urlencode",
    "-b", "--cookie", "--url", "-e", "--referer",
})

# 这些头里装的是凭据本体；其余头（Content-Type、Accept）不是。
CREDENTIAL_HEADERS = {
    "authorization": "AUTHORIZATION",
    "proxy-authorization": "PROXY_AUTHORIZATION",
    "x-api-key": "X_API_KEY",
    "api-key": "API_KEY",
    "x-api-token": "X_API_TOKEN",
    "x-auth-token": "X_AUTH_TOKEN",
    "x-goog-api-key": "GOOGLE_API_KEY",
    "x-functions-key": "FUNCTIONS_KEY",
    "x-amz-security-token": "AWS_SESSION_TOKEN",
    "openai-api-key": "OPENAI_API_KEY",
    "anthropic-api-key": "ANTHROPIC_API_KEY",
    "cookie": "COOKIE",
    "x-csrf-token": "CSRF_TOKEN",
}

BEARER_RE = re.compile(r"(?i)^bearer\s+(.+)$")
BASIC_RE = re.compile(r"(?i)^basic\s+([A-Za-z0-9+/=]{8,})$")
TOKEN_PAIR_RE = re.compile(r"(?i)^([A-Za-z0-9_\-]{2,40})\s*[:=]\s*(\S.*)$")


def looks_like_curl(text: str) -> bool:
    return bool(CURL_START.match(text))


def normalize(text: str) -> str:
    """合并反斜杠续行、去掉 PowerShell 的反引号续行，压成一行。"""
    joined = text.replace("\\\r\n", " ").replace("\\\n", " ")
    joined = re.sub(r"`\r?\n", " ", joined)
    return re.sub(r"\s+", " ", joined).strip()


def tokenize(text: str) -> list[str]:
    try:
        return shlex.split(normalize(text), posix=True, comments=False)
    except ValueError:
        # 引号不平衡（从网页复制时常被智能引号污染）。退回按空白切，宁可少抽也不崩。
        return normalize(text).replace("'", '"').split()


def parse_curl(text: str, *, source: str = "curl") -> Iterator[Candidate | Rejection]:
    if not looks_like_curl(text):
        return

    tokens = tokenize(text)
    hosts = hosts_in(text)

    index = 0
    while index < len(tokens):
        token = tokens[index]
        lowered = token.lower()

        if lowered in HEADER_FLAGS and index + 1 < len(tokens):
            yield from _from_header(tokens[index + 1], hosts, source, index)
            index += 2
            continue

        if lowered in USER_FLAGS and index + 1 < len(tokens):
            yield from _from_user(tokens[index + 1], hosts, source, index)
            index += 2
            continue

        if lowered in VALUE_FLAGS and index + 1 < len(tokens):
            # 请求体里可能是一整段 JSON，交给 jsonblob 处理；这里只记位置。
            index += 2
            continue

        index += 1


def _from_header(raw: str, hosts: tuple[str, ...], source: str,
                 position: int) -> Iterator[Candidate | Rejection]:
    match = TOKEN_PAIR_RE.match(raw.strip())
    if not match:
        return
    header, payload = match.group(1).lower(), match.group(2).strip()
    span = f"curl arg {position}"
    context = Context(source=source, line_no=position, line_text=raw)

    bearer = BEARER_RE.match(payload)
    if bearer:
        yield emit(bearer.group(1).strip(), key_name=CREDENTIAL_HEADERS.get(header, header.upper()),
                   hostnames=hosts, source=source, span=span,
                   extra={"header": header, "scheme": "bearer"}, context=context)
        return

    basic = BASIC_RE.match(payload)
    if basic:
        yield emit(basic.group(1), key_name=CREDENTIAL_HEADERS.get(header, header.upper()),
                   hostnames=hosts, source=source, span=span,
                   extra={"header": header, "scheme": "basic"}, context=context)
        return

    if header in CREDENTIAL_HEADERS:
        yield emit(payload, key_name=CREDENTIAL_HEADERS[header], hostnames=hosts,
                   source=source, span=span, extra={"header": header}, context=context)


def _from_user(raw: str, hosts: tuple[str, ...], source: str,
               position: int) -> Iterator[Candidate | Rejection]:
    user, _, password = raw.partition(":")
    if not password:
        return
    yield emit(
        password,
        key_name="HTTP_BASIC_PASSWORD",
        hostnames=hosts,
        source=source,
        span=f"curl arg {position}",
        extra={"basic_user_len": len(user)},
        context=Context(source=source, line_no=position, line_text="-u <user>:<pass>"),
    )
