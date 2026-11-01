"""manifest 生成与安装/卸载/状态查询。

manifest 放在 `%LOCALAPPDATA%\\KeyVault\\host\\`（vault 目录的 host 子目录
——它不是密钥数据，放 vault 目录旁是「本机自持、随 KV_VAULT_DIR 可重定向」
的自然位置，测试也借此隔离）。宿主可执行文件：
- 打包态：kv-host.exe 自身（sys.executable）；
- 开发态：没有独立 exe，必须用 --host-path 显式指定（native messaging 的
  manifest 只有 path 一个字段，指向 python 脚本需要 .bat 包装，而中文路径
  在 cmd 的本地编码下是坑——不做，保持注册的都是真实可执行文件）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from kv import errors
from kv.host import registry
from kv.paths import vault_dir

EXTENSION_ID = re.compile(r"^[a-p]{32}$")


def manifest_dir() -> Path:
    return vault_dir() / "host"


def default_host_path() -> Path:
    """打包态找同目录的 kv-host.exe；开发态明确报错（见模块 docstring）。

    不能直接用 sys.executable：`kv host install` 常从 kv-cli.exe 跑——
    把 CLI 注册成宿主的话，浏览器拉起它只会得到 argparse 的报错。
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        if exe.name.lower() == "kv-host.exe":
            return exe
        sibling = exe.parent / "kv-host.exe"
        if sibling.exists():
            return sibling
        raise errors.UsageError(
            f"没有在 {exe.parent} 找到 kv-host.exe",
            hint="先跑 packaging\\build.bat 产出三件套，或用 --host-path 显式指定宿主",
        )
    raise errors.UsageError(
        "开发态没有独立的宿主可执行文件可注册",
        hint="先跑 packaging\\build.bat 产出 dist\\kv-host.exe，"
             "或用 --host-path 显式指定一个宿主可执行文件",
    )


def install(browsers: list[str], extension_ids: list[str], *, host_path: Path,
            target_dir: Path | None = None) -> Path:
    """写 manifest 并逐浏览器注册。返回 manifest 路径。"""
    host_path = Path(host_path)
    ids = _validated_ids(extension_ids)
    if not host_path.exists():
        raise errors.UsageError(f"宿主可执行文件不存在：{host_path}")
    target = Path(target_dir) if target_dir else manifest_dir()
    target.mkdir(parents=True, exist_ok=True)
    manifest = target / f"{registry.HOST_NAME}.json"
    manifest.write_text(
        json.dumps({
            "name": registry.HOST_NAME,
            "description": "KeyVault - local secret vault connector",
            "path": str(host_path.resolve()),
            "type": "stdio",
            "allowed_origins": [f"chrome-extension://{eid}/" for eid in ids],
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for browser in browsers:
        registry.register(browser, manifest)
    return manifest


def uninstall(browsers: list[str]) -> list[str]:
    """逐浏览器删注册键（幂等）。manifest 文件保留——重新 install 即恢复。"""
    return [browser for browser in browsers if registry.unregister(browser)]


def status() -> dict[str, dict]:
    """各浏览器的注册状态：注册路径、manifest 是否还在、允许的扩展 ID。"""
    report: dict[str, dict] = {}
    for browser in registry.BROWSERS:
        path = registry.lookup(browser)
        entry: dict = {"registered": path, "manifest_ok": False, "extensions": []}
        if path:
            try:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            else:
                entry["manifest_ok"] = Path(data.get("path", "")).exists()
                entry["extensions"] = [
                    origin.split("//")[-1].rstrip("/")
                    for origin in data.get("allowed_origins", [])
                ]
        report[browser] = entry
    return report


def _validated_ids(extension_ids: list[str]) -> list[str]:
    if not extension_ids:
        raise errors.UsageError(
            "至少需要一个扩展 ID",
            hint="在 chrome://extensions 打开开发者模式加载扩展后，从卡片上复制 ID",
        )
    seen: list[str] = []
    for raw in extension_ids:
        eid = raw.strip().lower()
        if not EXTENSION_ID.match(eid):
            raise errors.UsageError(
                f"扩展 ID 格式不对：{raw!r}",
                hint="扩展 ID 是 32 位小写字母（a-p），在扩展管理页卡片上可见",
            )
        if eid not in seen:
            seen.append(eid)
    return seen
