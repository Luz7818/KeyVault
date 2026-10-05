"""把密钥值原子写入 .env 文件。

**从不打印值。** 输出只有行号和键名。这是 kv use 和 kv inject 的共享底层。

原子性：写到临时文件再 os.replace。中途杀进程 → 原文件完好、不留 .tmp。
os.replace 在 Windows 上是 MoveFileExW，同卷内原子。

Git 安全：目标在 git 工作树里且 .env 被跟踪 → 拒绝。密钥不该进版本控制。
--force 覆盖这个检查。--gitignore 自动把 .env 加进 .gitignore。
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from kv import clock, errors
from kv.core import audit as auditlog
from kv.core import repo
from kv.core.vault import VaultStore
from kv.model import InjectResult


def inject(
    store: VaultStore,
    name: str,
    target: Path,
    *,
    key_name: str | None = None,
    force: bool = False,
    gitignore: bool = False,
    actor: str = "cli",
) -> InjectResult:
    """把 name 对应的值写进 target 文件的 KEY=<value> 行。

    key_name 默认用记录里的 key_name 列。如果那个也是空的，用记录名本身
    （大写、连字符转下划线）。

    返回 InjectResult，里面只有路径、键名、行号 —— 绝不包含值。
    """
    row = store.get(name)
    var_name = key_name or row.key_name or _derive_var_name(row.name)

    target = target.resolve()
    if not force:
        _check_git_safety(target)
    if gitignore:
        _ensure_gitignore(target)

    # 解密值到 bytearray，用完归零
    buffer = bytearray()
    try:
        store.protector.unprotect_into(row.value_blob, buffer)
        value_text = buffer.decode("utf-8", "replace")
    finally:
        for i in range(len(buffer)):
            buffer[i] = 0
        buffer.clear()

    result = _write_env_file(target, var_name, value_text)

    # str 不可变，无法逐字节归零；bytearray 已经零过了。
    # 这里解除引用，让 GC 尽早回收。
    del buffer
    value_text = ""

    with store.session() as conn:
        repo.update_secret(conn, row.id, {"last_used_at": clock.now_iso()})
        auditlog.append(
            conn, "inject", actor, secret_id=row.id, name_snapshot=row.name,
            sha256_prefix=row.sha256,
            detail={"path": str(target), "key_name": var_name, "line": result.line_no},
        )

    return result


def _write_env_file(target: Path, var_name: str, value: str) -> InjectResult:
    """原子写 .env：更新已有行或追加新行。保留注释和空行。"""
    target.parent.mkdir(parents=True, exist_ok=True)

    existing_lines: list[str] = []
    if target.exists():
        existing_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)

    # 找 KEY= 行
    updated_line_no = 0
    new_lines: list[str] = []
    found = False
    for i, line in enumerate(existing_lines):
        stripped = line.strip()
        if not found and _matches_var(stripped, var_name):
            # 替换这一行，保留原行尾换行
            newline = _detect_newline(line)
            new_lines.append(f"{var_name}={_quote_value(value)}{newline}")
            found = True
            updated_line_no = i + 1
        else:
            new_lines.append(line)

    if not found:
        # 追加：先确保前面有换行
        newline = "\n"
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append(newline)
        new_lines.append(f"{var_name}={_quote_value(value)}\n")
        updated_line_no = len(new_lines)

    # 原子写：临时文件 + os.replace
    content = "".join(new_lines)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp",
    )
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        os.replace(tmp_path, str(target))
    except BaseException:
        os.close(fd) if not _fd_closed(fd) else None
        _safe_unlink(tmp_path)
        raise

    action = "updated" if found else "written"
    return InjectResult(path=str(target), key_name=var_name,
                        line_no=updated_line_no, action=action)


def _matches_var(line: str, var_name: str) -> bool:
    """一行是不是 `export VAR_NAME=...` 或 `VAR_NAME=...`。"""
    stripped = line.lstrip()
    if stripped.startswith("export "):
        stripped = stripped[7:].lstrip()
    return stripped.startswith(f"{var_name}=")


def _quote_value(value: str) -> str:
    """含空格或特殊字符时加双引号。纯字母数字不加。"""
    if not value:
        return '""'
    if any(ch in value for ch in " \t\n\"'#${}\\"):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    return value


def _detect_newline(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    if line.endswith("\r"):
        return "\r"
    return "\n"


def _derive_var_name(record_name: str) -> str:
    """记录名 → 环境变量名。连字符转下划线，全大写。"""
    return record_name.replace("-", "_").upper()


def _check_git_safety(target: Path) -> None:
    """目标在 git 工作树里且 .env 被跟踪 → 拒绝。"""
    git_dir = _find_git_root(target.parent)
    if git_dir is None:
        return  # 不在 git 工作树里，安全

    # 检查文件是否被跟踪
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(target)],
            cwd=str(git_dir),
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            raise errors.RefusedError(
                f"{target} 被 git 跟踪 —— 密钥不该进版本控制",
                hint="加 --force 强行写入，或把 .env 加进 .gitignore",
            )
    except FileNotFoundError:
        pass  # git 不在 PATH 上，跳过检查
    except subprocess.TimeoutExpired:
        pass


def _find_git_root(start: Path) -> Path | None:
    """从 start 向上找 .git 目录。"""
    current = start
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _ensure_gitignore(target: Path) -> None:
    """确保 target 的文件名在 .gitignore 里。"""
    gitignore = target.parent / ".gitignore"
    entry = target.name
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8", errors="replace")
        if entry in content.splitlines():
            return
        if not content.endswith("\n"):
            content += "\n"
        content += entry + "\n"
    else:
        content = entry + "\n"
    gitignore.write_text(content, encoding="utf-8")


def _fd_closed(fd: int) -> bool:
    """文件描述符是否已关闭。"""
    try:
        os.fstat(fd)
        return False
    except OSError:
        return True


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
