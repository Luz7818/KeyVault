"""copy + 30 秒自动擦除。

问题：kv copy 必须立刻退出（不能霸占终端），但 t+30 s 时得有东西还活着去擦。
三个候选答案：

1. 阻塞 30 秒 —— 不可接受，它霸占终端
2. 计划任务 / 服务 —— 违反「无常驻进程」约束，而且需要提权
3. **短命的分离子进程 + 下次调用时清扫** —— 选定

实测：DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW 起的子进程，
在父进程退出后仍然存活。

关键设计是**比对指纹**：helper 醒来后先看剪切板现在的内容指纹是否还是当初那把
key，不是就跳过并记一条 wipe_skipped。所以它绝不会覆盖你期间复制的别的东西，
而且它**根本不需要明文** —— argv 里传的是一个行 id，不是密钥。

再加一层安全网：任何 kv 调用（以及每个 watch tick）都会清扫过期未擦的行。所以
被关机或崩溃杀掉的 helper，仍然会在下次运行工具时导致一次擦除 —— 而如果那个机密
在几小时后**还**在剪切板上，那正是你最希望它消失的时候。
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from kv import PROGRAM, clock
from kv.capture.clipboard import ClipboardBackend, ClipboardError
from kv.core import masking, repo

BENIGN_TEXT = f"{PROGRAM}: clipboard cleared"
DEFAULT_TTL_SECONDS = 30.0

KV_PY = Path(__file__).resolve().parents[2] / "kv.py"

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


@dataclass(frozen=True)
class CopyResult:
    name: str
    platform: str
    preview: str
    ttl: float
    wipe_id: int | None
    excluded_from_history: bool


def copy_secret(store, name: str, backend: ClipboardBackend, *,
                ttl: float = DEFAULT_TTL_SECONDS, actor: str = "cli",
                wipe: bool = True, exclude_history: bool = True) -> CopyResult:
    """把值放上剪切板，并排定自动擦除。

    明文只存在于 store.reveal() 那个上下文管理器里，退出即逐字节归零。
    写剪切板是不可避免的字符串化 —— 在最窄的函数里、最后一刻才做。
    """
    row = store.get(name)
    fingerprint = ""
    with store.reveal(name, actor=actor, event="copy") as buffer:
        fingerprint = masking.fingerprint(buffer, row.kind)
        written = backend.write(buffer.decode("utf-8", "replace"),
                                     exclude_history=exclude_history)
    if not written:
        raise ClipboardError("写剪切板失败：可能被另一个应用占用，稍后重试")

    wipe_id = None
    if wipe and ttl > 0:
        with store.session() as conn:
            wipe_id = repo.insert_pending_wipe(
                conn, sha256=fingerprint, secret_id=row.id,
                deadline_at=clock.plus_seconds(ttl),
            )
        spawn_helper(wipe_id)

    return CopyResult(
        name=row.name, platform=row.platform,
        preview=row.preview(_mask_char()), ttl=ttl if wipe else 0.0,
        wipe_id=wipe_id, excluded_from_history=exclude_history,
    )


def _mask_char() -> str:
    from kv import console

    return console.mask_char()


def spawn_helper(wipe_id: int) -> int | None:
    """起一个分离子进程去等到点擦除。返回 pid，起不来返回 None（清扫会兜住）。"""
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", str(KV_PY), "_wipe", str(wipe_id)],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except (OSError, ValueError):
        return None
    return process.pid


def run_wipe(store, wipe_id: int, backend: ClipboardBackend, *,
             actor: str = "wipe-helper", wait: bool = True) -> str:
    """擦一次。返回 done / skipped / missing / already-<status>。

    **不需要明文**：只比对指纹。指纹不匹配说明用户期间复制了别的东西，
    那就跳过 —— 覆盖掉用户刚复制的内容比留一把过期 key 在剪切板上更糟。
    """
    with store.read() as conn:
        row = repo.get_pending_wipe(conn, wipe_id)
    if row is None:
        return "missing"
    if row["status"] != "pending":
        return f"already-{row['status']}"

    if wait:
        _sleep_until(row["deadline_at"])

    current = backend.read()
    if current is None or masking.fingerprint(current.encode("utf-8")) != row["sha256"]:
        _finish(store, wipe_id, "skipped", "wipe_skipped", actor,
                detail={"reason": "clipboard-changed" if current is not None else "clipboard-unavailable"})
        return "skipped"

    backend.write(BENIGN_TEXT, exclude_history=True)
    _finish(store, wipe_id, "done", "wipe", actor, detail={})
    return "done"


def sweep_due_wipes(store, backend: ClipboardBackend, *, actor: str = "cli") -> list[str]:
    """清扫过期未擦的行。任何 kv 调用和每个 watch tick 都会调它。

    这是 helper 被关机或崩溃杀掉时的兜底：那把 key 还在剪切板上，而这正是
    最该把它擦掉的时刻。
    """
    with store.read() as conn:
        due = repo.due_wipes(conn)
    return [run_wipe(store, int(row["id"]), backend, actor=actor, wait=False) for row in due]


def _finish(store, wipe_id: int, status: str, event: str, actor: str, detail: dict) -> None:
    with store.session() as conn:
        repo.mark_wipe(conn, wipe_id, status)
        row = repo.get_pending_wipe(conn, wipe_id)
        from kv.core import audit as auditlog

        auditlog.append(
            conn, event, actor,
            secret_id=row["secret_id"] if row else None,
            sha256_prefix=(row["sha256"][:12] if row else None),
            detail=detail,
        )


def _sleep_until(deadline_iso: str) -> None:
    """分片睡到 deadline，好让 Ctrl-C 有机会打断（helper 是分立的，但手动跑时会用到）。"""
    target = clock.parse(deadline_iso)
    if target is None:
        return
    while True:
        remaining = (target - clock.now()).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.5))
