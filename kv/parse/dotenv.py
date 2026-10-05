"""KEY=value 与多行 .env。

处理：`export ` 前缀、单双引号、`#` 注释（含引号内的 # 不算注释）、CRLF、
引号内的换行。一次给出一整段 .env 的所有键 —— 那是「批量导入」的基础。
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterator

from kv.model import Candidate, Rejection
from kv.parse.candidate import emit, hosts_in
from kv.parse.placeholders import Context, is_credential_key_name

KEY_RE = r"[A-Za-z_][A-Za-z0-9_.\-]*"
PAIR_RE = re.compile(rf"^\s*(?:export\s+)?({KEY_RE})\s*=\s*(.*)$")


def unquote(raw: str) -> str:
    """去引号、去行尾注释。

    顺序要紧：**先**按引号取内容，**再**考虑行尾注释。反过来的话
    `SLACK_TOKEN="xoxb-abc" # 机器人` 会先被 ` #` 切一刀，剩下的
    `"xoxb-abc"` 首尾字符不再相等，引号就剥不掉了。
    引号内的 `#` 因此天然存活。
    """
    text = raw.strip()
    if text[:1] in ('"', "'"):
        quote = text[0]
        end = text.find(quote, 1)
        if end < 0:
            return text[1:]  # 未闭合：只去掉开头那个引号，别吃掉内容
        inner = text[1:end]
        if quote == '"':
            return inner.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        return inner
    return text.split(" #", 1)[0].strip()


def split_pairs(text: str) -> Iterator[tuple[int, str, str]]:
    """逐行产出 (行号, 键名, 原始值)。跳过注释与空行。"""
    for number, line in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = PAIR_RE.match(line)
        if not match:
            continue
        yield number, match.group(1), match.group(2)


def parse_kvline(text: str, *, source: str = "manual") -> Iterator[Candidate | Rejection]:
    """单行 KEY=value。多行交给 parse_dotenv。"""
    if "\n" in text.strip():
        return
    for number, key, raw in split_pairs(text):
        yield _one(number, key, raw, source, ())


def parse_dotenv(text: str, *, source: str = "manual") -> Iterator[Candidate | Rejection]:
    """整段 .env。

    主机名传播是保守的：只有当整段里恰好一个凭据候选、且文中出现过主机名时才传播。
    理由 —— 一段同时含 OPENAI_API_KEY 和 DEEPSEEK_API_KEY 的 .env 里，把唯一的
    base_url 传播给两者会给其中一把贴上错平台，而那是个**看起来确定**的错判。
    """
    pairs = list(split_pairs(text))
    if len(pairs) < 2:
        return
    block_hosts = hosts_in(text)

    results = [_one(number, key, raw, source, ()) for number, key, raw in pairs]

    # 块级主机名传播给「键名像凭据、且自己没有直接主机名」的候选，放进
    # context_hostnames（弱证据）而不是 hostnames（强证据）。
    #
    # 为什么不像原来那样只在「恰好一个凭据候选」时传播：真实的 .env 里
    # base_url 旁边常常还有别家的 key（GITLAB_TOKEN 等），那条规则于是永不触发，
    # LLM_API_KEY 这种最常见的写法反而丢了信号。
    # 为什么不能直接传播成强证据：hostname 级压过 keyname，于是同一块里的
    # deepseek base_url 会把 GITLAB_TOKEN 判成 deepseek。
    # 分成两级、并让 detect 把弱证据排在 keyname/shape 之后，两个问题一起解决。
    targets = {
        id(r) for r in results
        if isinstance(r, Candidate) and not r.hostnames and is_credential_key_name(r.key_name)
    }
    if targets and block_hosts:
        results = [
            replace(r, context_hostnames=block_hosts,
                    extra={**r.extra, "hosts_from_block": True})
            if id(r) in targets else r
            for r in results
        ]

    yield from results


def _one(number: int, key: str, raw: str, source: str,
         hosts: tuple[str, ...]) -> Candidate | Rejection:
    value = unquote(raw)
    context = Context(source=source, line_no=number, line_text=f"{key}={raw}")
    return emit(
        value,
        kind=_kind_for(key, value),
        key_name=key,
        hostnames=hosts or hosts_in(value),
        source=source,
        span=f"line {number}",
        context=context,
    )


def _kind_for(key: str, value: str) -> str:
    if value.startswith("-----BEGIN"):
        return "pem"
    if value.lstrip().startswith(("{", "[")):
        return "json"
    lowered = key.lower()
    if "connection" in lowered or "conn_str" in lowered or "database_url" in lowered:
        return "connstring"
    return "token"


def looks_like_dotenv(text: str) -> bool:
    return len(list(split_pairs(text))) >= 2
