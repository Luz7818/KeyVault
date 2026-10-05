"""整段文本就是一个值。

排在链条末尾附近 —— 结构化解析器必须先跑，否则一条 curl 命令会被当成一串
垃圾「裸 key」。
"""

from __future__ import annotations

import re
from typing import Iterator

from kv.model import Candidate, Rejection
from kv.parse.candidate import emit, hosts_in
from kv.parse.placeholders import Context

MAX_BARE_LEN = 20000
WHITESPACE = re.compile(r"\s")
KEY_VALUE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*=")
# 规则表里每一种凭据形态都是可打印 ASCII。要求 ASCII 是最窄的散文过滤：
# 它挡掉中文说明文字，而不需要任何「看起来像不像标识符」的宽判断。
ASCII_TOKEN = re.compile(r"^[!-~]+$")


def parse_bare(text: str, *, source: str = "manual") -> Iterator[Candidate | Rejection]:
    value = text.strip()
    if not value or not _is_bare_value(value):
        return
    context = Context(source=source, line_text=value)
    yield emit(
        value,
        kind=_infer_kind(value),
        hostnames=hosts_in(value),
        source=source,
        span="whole input",
        context=context,
    )


def _is_bare_value(value: str) -> bool:
    """「整段文本就是一个值」的判据。四条排除，每条都对应一个具体案例：

    * 多行 → 交给 dotenv / jsonblob / generic_scan
    * 含空白 → 那是命令或散文，不是一把 key（否则整条 curl 命令会被当成裸值）
    * 以 { 或 [ 开头 → 那是 JSON（紧凑序列化的 JSON 没有空白，会绕过上一条）
    * 形如 KEY=value → 那是 kvline 的活，重复处理会产生两条候选
    """
    if "\n" in value or len(value) > MAX_BARE_LEN:
        return False
    if WHITESPACE.search(value):
        return False
    if value[0] in "{[":
        return False
    if KEY_VALUE.search(value):
        return False
    return bool(ASCII_TOKEN.match(value))


def _infer_kind(value: str) -> str:
    if value.startswith("-----BEGIN"):
        return "pem"
    if "://" in value and "@" in value.split("://", 1)[1][:80]:
        return "connstring"
    return "token"
