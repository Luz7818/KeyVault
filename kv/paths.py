"""vault 目录定位与危险位置拒绝。

数据默认放 %LOCALAPPDATA%\\KeyVault\\，不是 %APPDATA% —— 后者会被域漫游同步，
一份密钥库跟着你在域内每台机器上跑，不是我们要的。

vault 目录会自动写入内容为 * + !.gitignore 的自排除规则：git 会读取任意层级目录
里的 .gitignore，所以它对任何外层仓库都生效 —— 即使将来有人在 Project/ 下
git init 再 git add -A，也只会收到那份 .gitignore 本身。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from kv.errors import UsageError

VAULT_DIR_NAME = "KeyVault"
ENV_OVERRIDE = "KV_VAULT_DIR"

CLOUD_SYNC_MARKERS = (
    "onedrive",
    "dropbox",
    "google drive",
    "googledrive",
    "坚果云",
    "baidusyncdisk",
    "baiducloud",
    "阿里云盘",
    "nutstore",
)

GITIGNORE_BODY = "*\n!.gitignore\n"


def vault_dir() -> Path:
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    base = os.environ.get("LOCALAPPDATA", "").strip()
    if not base:
        raise UsageError(
            "找不到 %LOCALAPPDATA%，无法定位 vault 目录",
            hint=f"设 {ENV_OVERRIDE} 环境变量显式指定一个目录",
        )
    return (Path(base) / VAULT_DIR_NAME).resolve()


def vault_db() -> Path:
    return vault_dir() / "vault.db"


def is_cloud_synced(path: Path) -> str:
    """命中云同步目录则返回命中的标记，否则返回空串。

    放在 OneDrive/ 里的 .kvb 距离明文转储只差一个口令。
    """
    lowered = [part.lower() for part in path.parts]
    joined = " ".join(lowered)
    for marker in CLOUD_SYNC_MARKERS:
        if marker in lowered or marker in joined:
            return marker
    return ""


def refuse_unsafe_dir(path: Path, *, what: str, force: bool = False) -> None:
    """拒绝把密钥文件写进云同步目录。--force 可越过，但要大声说。"""
    marker = is_cloud_synced(path)
    if marker and not force:
        raise UsageError(
            f"拒绝把{what}写进云同步目录（命中 {marker}）：{path}",
            hint="云同步盘会把密钥复制到你不控制的机器上。换一个本地目录，或加 --force 明确越过",
        )


def ensure_self_excluding_gitignore(directory: Path) -> Path:
    """在目录里写入自排除 .gitignore。已存在且内容正确则不动。"""
    target = directory / ".gitignore"
    if target.exists():
        current = target.read_text(encoding="utf-8")
        if current == GITIGNORE_BODY:
            return target
        raise UsageError(
            f"{target} 已存在但内容不是自排除规则",
            hint="不要改窄它 —— 保持整目录自排除。若确实要覆盖，先手动删掉这个文件",
        )
    target.write_text(GITIGNORE_BODY, encoding="utf-8", newline="\n")
    return target


def git_toplevel(path: Path) -> Path | None:
    """path 所在 git 工作树的根，不在任何工作树里则返回 None。"""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    return Path(top) if top else None


def is_git_tracked(path: Path) -> bool:
    """path 是否被 git 跟踪（不是「在不在工作树里」，是「会不会入库」）。"""
    parent = path.parent if path.is_file() or not path.exists() else path
    try:
        result = subprocess.run(
            ["git", "-C", str(parent), "ls-files", "--error-unmatch", "--", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def is_git_ignored(path: Path) -> bool:
    parent = path.parent if path.is_file() or not path.exists() else path
    try:
        result = subprocess.run(
            ["git", "-C", str(parent), "check-ignore", "-q", "--", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0
