"""解析链：文本 → ParseResult。

顺序有讲究 —— 结构化解析器必须在 bare 之前跑，否则一条 curl 命令会被当成
一串垃圾「裸 key」。generic_scan 只在前面全部落空时才跑：它是散文里捞 key 的
最后手段，产出的候选噪声最大，不该抢在结构化解析前面。

链条之后按 (sha256, key_name) 去重：同一段文本常被多个解析器同时命中
（例如 curl 里的 -d JSON 会被 curl 和 jsonblob 各抽一次）。
"""

from __future__ import annotations

import re
from typing import Iterator

from kv.model import Candidate, ParseResult, Rejection
from kv.parse import awscsv, bare, curl, dotenv, jsonblob
from kv.parse.candidate import emit, hosts_in
from kv.parse.placeholders import Context

# 通用 token 扫。故意不含平台特有前缀 —— 那会和 detect/rules.py 的 shape 表
# 重复，而两份手写的同一列表一定会漂移。这里只负责「找出值的边界」，
# 判平台是 detect 的事。
TOKEN_RUN = re.compile(r"[A-Za-z0-9_\-]{16,}")
JWT_RUN = re.compile(r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*")
PEM_BLOCK = re.compile(
    r"(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----"
)
CONNSTRING_RUN = re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^:/@\s]+:[^@\s]+@[^\s'\"<>]+")

ORDER = (curl.parse_curl, jsonblob.parse_jsonblob, awscsv.parse_awscsv,
         dotenv.parse_dotenv, dotenv.parse_kvline, bare.parse_bare)


def parse(text: str, *, source_hint: str = "manual") -> ParseResult:
    """主入口。纯函数：不开库、不碰剪切板、不写任何东西。"""
    if not text or not text.strip():
        return ParseResult()

    candidates: list[Candidate] = []
    rejected: list[Rejection] = []
    for parser in ORDER:
        for item in parser(text, source=source_hint):
            (candidates if isinstance(item, Candidate) else rejected).append(item)

    if not candidates:
        for item in _generic_scan(text, source_hint):
            (candidates if isinstance(item, Candidate) else rejected).append(item)

    unique, seen = [], set()
    for candidate in candidates:
        key = (candidate.sha256(), candidate.key_name)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)

    return ParseResult(candidates=tuple(unique), rejected=_dedupe_rejections(rejected))


def _dedupe_rejections(rejected: list[Rejection]) -> tuple[Rejection, ...]:
    seen, unique = set(), []
    for item in rejected:
        key = (item.reason, item.detail, item.span)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return tuple(unique)


def _generic_scan(text: str, source: str) -> Iterator[Candidate | Rejection]:
    """散文里捞 key。只在结构化解析全部落空时才跑。

    跨行的形态（PEM）必须在**整段文本**上找，不能按行找 —— 按行找的话
    PEM 永远匹配不到，反而会把中间的 base64 主体当成一把裸 token。
    已被跨行形态吃掉的区间要记下来，后续扫描跳过。
    """
    hosts = hosts_in(text)
    consumed: list[tuple[int, int]] = []

    for match in PEM_BLOCK.finditer(text):
        consumed.append(match.span())
        number = text.count("\n", 0, match.start()) + 1
        yield emit(match.group(0), kind="pem", hostnames=hosts, source=source,
                   span=f"line {number}",
                   context=Context(source=source, line_no=number, line_text="PEM block"))

    for match in CONNSTRING_RUN.finditer(text):
        if _inside(match.span(), consumed):
            continue
        number = text.count("\n", 0, match.start()) + 1
        yield emit(match.group(0), kind="connstring",
                   hostnames=hosts_in(match.group(0)) or hosts, source=source,
                   span=f"line {number}",
                   context=Context(source=source, line_no=number, line_text="connection string"))

    for match in JWT_RUN.finditer(text):
        if _inside(match.span(), consumed):
            continue
        number = text.count("\n", 0, match.start()) + 1
        yield emit(match.group(0), hostnames=hosts, source=source, span=f"line {number}",
                   context=Context(source=source, line_no=number, line_text="JWT"))

    offset = 0
    for number, line in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        for match in TOKEN_RUN.finditer(line):
            if _inside((offset + match.start(), offset + match.end()), consumed):
                continue
            token = match.group(0)
            if not _plausible_token(token):
                continue
            yield emit(token, hostnames=hosts, source=source, span=f"line {number}",
                       context=Context(source=source, line_no=number, line_text=line[:200]))
        offset += len(line) + 1


def _inside(span: tuple[int, int], ranges: list[tuple[int, int]]) -> bool:
    return any(start <= span[0] and span[1] <= end for start, end in ranges)


def _plausible_token(token: str) -> bool:
    """过滤散文噪声，但**不按长度卡** —— 长度下限曾经放走过一把 15 字符的真令牌。

    要求：至少含一个数字，或者同时含大小写，或者含 - / _ 分隔。
    纯字母的长单词（"authentication"）因此被滤掉，而真 key 几乎总带数字或分隔符。
    """
    if any(ch.isdigit() for ch in token):
        return True
    if "-" in token or "_" in token:
        return True
    return any(ch.isupper() for ch in token) and any(ch.islower() for ch in token)
