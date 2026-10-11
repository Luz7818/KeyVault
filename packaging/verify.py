"""打包产物校验 —— build.bat 的最后一步。

五查（任一失败退出码 1，不依赖人工目视）：
  1. 版本号   dist/kv-cli.exe --version 与 kv/__init__.py 的 __version__ 一致
  2. init     kv-cli.exe 能在临时目录建库（覆盖 schema.sql 进包）
  3. golden   kv-cli.exe selftest --golden 通过（覆盖 tests/corpus 进包；
              scan 的闸门全靠它，语料缺了 exe 用户的 scan 会被永久拒绝）
  4. _wipe    kv.exe 的冻结态 CLI 短路立即退出、不弹窗口（copy 擦除 helper 的宿主）
  5. GUI      kv.exe 真启动（窗口标题出现即算），随后只回收「本次新起」的进程 ——
              用户自己开着的 kv.exe 绝不动

用法（仓库根或 packaging/ 均可）：
    python packaging/verify.py
    python packaging/verify.py --skip-gui       # 无桌面环境时跳过第 5 查
    python packaging/verify.py --expect 9.9.9   # 覆盖期望版本（自测失败路径用）
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kv import console  # noqa: E402

DIST = ROOT / "dist"
KV_CLI = DIST / "kv-cli.exe"
KV_GUI = DIST / "kv.exe"
GUI_TITLE = "KeyVault"
GUI_WAIT_SECONDS = 40

FAILURES: list[str] = []


def run(argv: list[str], *, env: dict | None = None, timeout: int = 120):
    return subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, check=False, env=env,
    )


def expected_version() -> str:
    """从 kv/__init__.py 读 __version__ —— 版本号的单源就是它。"""
    text = (ROOT / "kv" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("kv/__init__.py 里找不到 __version__")
    return match.group(1)


def check_version(expect: str) -> None:
    result = run([str(KV_CLI), "--version"])
    got = (result.stdout or "").strip()
    want = f"kv {expect}"
    if result.returncode == 0 and got == want:
        console.ok(f"版本号      {got}")
    else:
        FAILURES.append(f"版本号不符：期望 {want!r}，得到 {got!r}（exit {result.returncode}）")


def kv_processes() -> dict[int, str]:
    """{pid: 窗口标题}。tasklist /V 的 Window Title 是最后一列；无匹配时它打
    一行本地化提示（不以引号开头），按列数过滤掉。"""
    out = run(["tasklist", "/FI", "IMAGENAME eq kv.exe", "/V", "/FO", "CSV", "/NH"],
              timeout=30)
    table: dict[int, str] = {}
    for row in csv.reader(io.StringIO(out.stdout or "")):
        if len(row) >= 9 and row[0].strip().lower() == "kv.exe":
            try:
                table[int(row[1])] = row[-1]
            except ValueError:
                continue
    return table


def kill_pids(pids: set[int]) -> None:
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, check=False)


def check_init_and_golden(vault_dir: Path) -> None:
    result = run([str(KV_CLI), "init", "--vault-dir", str(vault_dir)])
    if result.returncode == 0 and (vault_dir / "vault.db").exists():
        console.ok(f"init        kv-cli.exe 建库成功（schema.sql 已进包）")
    else:
        FAILURES.append(f"init 失败：exit {result.returncode} {(result.stderr or '').strip()}")
    result = run([str(KV_CLI), "selftest", "--golden", "--vault-dir", str(vault_dir)])
    if result.returncode == 0 and "全 PASS" in (result.stdout or ""):
        console.ok("golden      selftest --golden 通过（tests/corpus 已进包）")
    else:
        FAILURES.append(f"selftest --golden 失败：exit {result.returncode} "
                        f"{(result.stderr or result.stdout or '').strip()[:200]}")


def check_wipe_dispatch(vault_dir: Path) -> None:
    """一个不存在的 wipe id：命令面可达 → EXIT_NOT_FOUND(2)，且不弹窗口。"""
    result = run([str(KV_GUI), "_wipe", "999999999", "--vault-dir", str(vault_dir)],
                 timeout=90)
    if result.returncode == 2:
        console.ok("_wipe       kv.exe 冻结态 CLI 短路正常（3 秒内退出，非 GUI）")
    else:
        FAILURES.append(f"_wipe 分发异常：exit {result.returncode}（期望 2）")


def check_gui_starts(vault_dir: Path) -> None:
    before = set(kv_processes())
    env = {**os.environ, "KV_VAULT_DIR": str(vault_dir)}
    subprocess.Popen([str(KV_GUI)], env=env,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    new_pids: set[int] = set()
    deadline = time.monotonic() + GUI_WAIT_SECONDS
    started = False
    try:
        while time.monotonic() < deadline:
            current = kv_processes()
            new_pids = set(current) - before
            if any(current.get(pid) == GUI_TITLE for pid in new_pids):
                started = True
                break
            time.sleep(0.5)
        if started:
            console.ok(f"GUI         窗口「{GUI_TITLE}」已出现（新起 {len(new_pids)} 个进程）")
        else:
            FAILURES.append(f"GUI {GUI_WAIT_SECONDS}s 内未见窗口「{GUI_TITLE}」"
                            f"（新起进程 {sorted(new_pids)}）")
    finally:
        # 只回收本次新起的；用户自己开的 kv.exe 不在差集里
        kill_pids(set(kv_processes()) - before)


def main() -> int:
    parser = argparse.ArgumentParser(description="打包产物校验")
    parser.add_argument("--expect", default="", help="覆盖期望版本（自测失败路径用）")
    parser.add_argument("--skip-gui", action="store_true", help="跳过 GUI 启动检查")
    args = parser.parse_args()

    for exe in (KV_CLI, KV_GUI):
        if not exe.exists():
            console.error(f"缺少产物：{exe} —— 先跑 packaging/build.bat")
            return 1

    expect = args.expect or expected_version()
    console.echo(f"校验 dist/ 产物（期望版本 {expect}）")
    check_version(expect)

    with tempfile.TemporaryDirectory(prefix="kv-verify-") as tmp:
        vault_dir = Path(tmp) / "vault"
        check_init_and_golden(vault_dir)
        check_wipe_dispatch(vault_dir)
        if args.skip_gui:
            console.warn("GUI 启动检查已跳过（--skip-gui）")
        else:
            check_gui_starts(vault_dir)

    if FAILURES:
        console.echo("")
        for item in FAILURES:
            console.error(item)
        console.error(f"校验失败：{len(FAILURES)} 项")
        return 1
    console.echo("")
    console.ok("产物校验全过，可以分发")
    return 0


if __name__ == "__main__":
    sys.exit(main())
