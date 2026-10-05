"""控制台输出与交互。

本机控制台 CP 是 936、sys.stdout.encoding 是 gbk，而仓库路径本身是中文
（D:\\东南大学\\...）。不做 setup_stdio() 的话中文一律乱码，掩码字符 '•'
(U+2022) 在 gbk 下根本无法编码 —— 输出被重定向时直接 UnicodeEncodeError。

所以：setup_stdio() 必须在任何输出之前调用，stdout 和 stderr 都要处理，
掩码字符必须在启动时探测一次。
"""

from __future__ import annotations

import sys
import unicodedata
from collections.abc import Sequence

from kv.core.masking import is_encodable

DEFAULT_MASK_CHAR = "*"
PREFERRED_MASK_CHAR = "\u2022"

_MASK_CHAR: str = DEFAULT_MASK_CHAR


def setup_stdio() -> None:
    """把 stdout/stderr 切到 UTF-8。幂等，且在流不是 TextIOWrapper 时静默跳过。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            continue


def encoding_of(stream) -> str:
    return getattr(stream, "encoding", None) or "utf-8"


def init_mask_char(preferred: str | None = None) -> str:
    """探测掩码字符。preferred 不可编码时回退到 '*'。返回最终选定的字符。"""
    global _MASK_CHAR
    candidate = PREFERRED_MASK_CHAR if preferred is None else preferred
    enc = encoding_of(sys.stdout)
    _MASK_CHAR = candidate if candidate and is_encodable(candidate, enc) else DEFAULT_MASK_CHAR
    return _MASK_CHAR


def mask_char() -> str:
    return _MASK_CHAR


def echo(text: str = "") -> None:
    print(text)


def _to_stderr(prefix: str, text: str) -> None:
    """先冲 stdout 再写 stderr。

    stdout 是行缓冲/块缓冲而 stderr 无缓冲，不冲的话两条流会交错 ——
    实测过：init 的导出警告会跑到「vault 已建在」前面，读起来像是对一个
    还不存在的库发警告。
    """
    sys.stdout.flush()
    print(f"{prefix}{text}", file=sys.stderr)


def warn(text: str) -> None:
    _to_stderr("[警告] ", text)


def error(text: str) -> None:
    _to_stderr("[错误] ", text)


def hint(text: str) -> None:
    """补救提示。跟 error 一样走 stderr —— 出错的命令，它的提示不该被
    当成正常输出混进管道。"""
    _to_stderr("       ", text)


def ok(text: str) -> None:
    print(f"[OK] {text}")


LABEL_WIDTH = 18


def echo_field(label: str, value: str, *, indent: str = "  ", width: int = LABEL_WIDTH) -> None:
    """标签列对齐的键值行。宽度按显示列算，中文标签才不会把值顶歪。"""
    gap = max(1, width - display_width(label))
    print(f"{indent}{label}{' ' * gap}{value}")


def display_width(text: str) -> int:
    """终端里占几列。中文/全角字符占 2 列，len() 只算 1 —— 不修正表格就会歪。"""
    total = 0
    for ch in text:
        total += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return total


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def print_table(rows: list[Sequence[str]], headers: Sequence[str] | None = None) -> None:
    """等宽对齐的表格。任何单元格都不该含密钥值 —— 调用方的责任。

    行长度不齐时补空格而不是抛 IndexError：一个表格渲染器不该因为调用方少给一列
    就把整个命令炸掉，尤其是在一个安全工具里（炸掉的命令会让人以为库坏了）。
    """
    if not rows and not headers:
        return
    table = [[str(c) for c in headers]] if headers else []
    table.extend([[str(c) for c in row] for row in rows])
    columns = max(len(row) for row in table)
    table = [row + [""] * (columns - len(row)) for row in table]
    widths = [max(display_width(row[i]) for row in table) for i in range(columns)]
    for index, row in enumerate(table):
        print("  ".join(pad(cell, widths[i]) for i, cell in enumerate(row)).rstrip())
        if headers and index == 0:
            print("  ".join("-" * w for w in widths))


def ask(prompt: str, default: str = "") -> str:
    """读一行。直接回车返回 default。"""
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return answer or default


def confirm(prompt: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = ask(f"{prompt} ({hint})", "y" if default else "n").lower()
    return answer in ("y", "yes")


def hidden_input(prompt: str) -> str:
    """不回显地读一行。优先 msvcrt.getwch，回退 getpass。"""
    try:
        import msvcrt
    except ImportError:
        import getpass

        return getpass.getpass(prompt)

    sys.stdout.write(prompt)
    sys.stdout.flush()
    chars: list[str] = []
    while True:
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            break
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch in ("\x08", "\x7f"):
            if chars:
                chars.pop()
            continue
        if ch == "\x00" or ch == "\xe0":
            msvcrt.getwch()
            continue
        chars.append(ch)
    sys.stdout.write("\n")
    sys.stdout.flush()
    return "".join(chars)


def ask_menu(title: str, options: Sequence[tuple[str, str]], extra: Sequence[tuple[str, str]] = ()) -> str:
    """数字/字母菜单。options 是 (键, 标签)，extra 是非数字选项如 ('s','跳过')。

    返回被选中的键。EOF 或空输入返回 extra 里第一个键（通常是跳过）。
    """
    print(title)
    for key, label in list(options) + list(extra):
        print(f"  {key} {label}")
    valid = {k for k, _ in list(options) + list(extra)}
    fallback = extra[0][0] if extra else ""
    while True:
        choice = ask(">", fallback).lower()
        if choice in valid:
            return choice
