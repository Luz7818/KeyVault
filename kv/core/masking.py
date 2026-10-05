"""规范化、指纹、掩码。

全项目唯一决定「两个值算不算同一把 key」的地方。去重能不能工作全看
canonicalize：剪切板复制经常带尾部换行，不规范化的话每次重新复制都会产生
一条重复行。
"""

from __future__ import annotations

import hashlib
import json
import re

MASK_RUN = 4

KIND_TOKEN = "token"
KIND_JSON = "json"
KIND_PEM = "pem"
KIND_PAIR = "pair"
KIND_CONNSTRING = "connstring"
KIND_OTHER = "other"

KINDS = (KIND_TOKEN, KIND_JSON, KIND_PEM, KIND_PAIR, KIND_CONNSTRING, KIND_OTHER)

_STRIP = b"\r\n\t \x0b\x0c"


def canonicalize(value: bytes, kind: str = KIND_TOKEN) -> bytes:
    """把同一个逻辑值的各种写法收敛成唯一字节串。幂等。"""
    if kind == KIND_JSON:
        return _canonical_json(value)
    if kind == KIND_PEM:
        return value.replace(b"\r\n", b"\n").strip(b"\n") + b"\n"
    return value.strip(_STRIP)


def _canonical_json(value: bytes) -> bytes:
    """键排序 + 紧凑分隔符重序列化，让别的工具的重新序列化不产生重复行。"""
    try:
        obj = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return value.strip(_STRIP)
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def fingerprint(value: bytes, kind: str = KIND_TOKEN) -> str:
    """canonicalize 后的 sha256。幂等：传已规范化的值结果相同。"""
    return hashlib.sha256(canonicalize(value, kind)).hexdigest()


def mask(
    value: bytes,
    keep_head: int = 0,
    keep_tail: int = 4,
    *,
    char: str = "*",
) -> str:
    """一次性算出预览串。落库请用 split_mask + preview，好让掩码字符可后改。

    (0, 4) -> ****c31f     (4, 4) -> AKIA****WXYZ     (0, 0) -> ****
    """
    head, tail = split_mask(value, keep_head, keep_tail)
    return preview(head, tail, char)


def split_mask(value: bytes, keep_head: int = 0, keep_tail: int = 4) -> tuple[str, str]:
    """取出要露在掩码两侧的头尾两段原文。

    分开存这两段、而不是存合成好的掩码串，是为了让掩码字符成为纯显示偏好：
    改配置立刻生效，永远不需要重算全库（存合成串的话，改配置会静默什么都不做）。
    值太短、留头留尾就等于泄漏全部时，两段都返回空串，渲染出来是全掩。
    """
    text = value.decode("utf-8", "replace").replace("\n", "\\n").replace("\r", "")
    if len(text) <= keep_head + keep_tail:
        return "", ""
    head = text[:keep_head] if keep_head else ""
    tail = text[len(text) - keep_tail :] if keep_tail else ""
    return head, tail


def preview(mask_head: str, mask_tail: str, char: str = "*", run: int = MASK_RUN) -> str:
    """把头尾两段和掩码字符合成预览串。渲染期调用，不需要解密任何东西。"""
    return f"{mask_head}{char * run}{mask_tail}"


def is_encodable(text: str, encoding: str) -> bool:
    """探测一个字符能否用目标编码写出。掩码字符选择用它。"""
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


_PREFIX = re.compile(rb"^([A-Za-z]{2,6}[-_])")


def infer_mask_style(value: bytes) -> tuple[int, int]:
    """没有平台规则指定掩码风格时的回退：保住可识别的前缀。

    sk-... -> (3, 4)，于是显示成 sk-****c31f 而不是 ****c31f —— 前缀本身不含
    机密，却足以让人一眼认出这是哪一族的 key。M1 的规则表会给每个平台指定自己的
    风格，这个函数继续作为 unknown 平台的回退。
    """
    match = _PREFIX.match(canonicalize(value))
    return (len(match.group(1)), 4) if match else (0, 4)
