"""host 命令：浏览器原生消息宿主的注册管理（install / uninstall / status）。

这里只做「参数 → 调 kv/host/install → 输出」；注册表与 manifest 的全部
动作在 kv/host/registry.py 与 kv/host/install.py。默认浏览器集合 = 全部
（chrome + edge），用户没给扩展 ID 时交互询问。
"""

from __future__ import annotations

from kv import console, errors
from kv.host import registry


def cmd_install(args) -> int:
    from kv.host import install as installmod

    browsers = _browsers(args.browser)
    ids = _extension_ids(args.extension_id)
    host_path = args.host_path or installmod.default_host_path()
    manifest = installmod.install(
        browsers, ids, host_path=host_path,
        target_dir=args.manifest_dir or None,
    )
    console.ok(f"宿主已注册：{registry.HOST_NAME}")
    for browser in browsers:
        console.echo(f"  {browser:<8} {registry.key_path(browser)}")
    console.echo(f"  manifest {manifest}")
    console.echo(f"  宿主     {host_path}")
    console.echo("")
    console.echo("接下来：")
    console.echo("  1. 浏览器打开 chrome://extensions（Edge: edge://extensions）")
    console.echo("  2. 开发者模式 → 加载解压缩的扩展 → 选仓库的 extension/ 目录")
    console.echo("  3. 若重新加载后 ID 变化，重跑本命令补注册新 ID")
    return 0


def cmd_uninstall(args) -> int:
    from kv.host import install as installmod

    browsers = _browsers(args.browser)
    removed = installmod.uninstall(browsers)
    if removed:
        for browser in removed:
            console.echo(f"已移除 {browser} 的注册键")
    else:
        console.echo("没有已注册的宿主键")
    skipped = [b for b in browsers if b not in removed]
    if skipped:
        console.echo(f"（{', '.join(skipped)} 本就未注册）")
    return 0


def cmd_status(args) -> int:
    from kv.host import install as installmod

    report = installmod.status()
    for browser, entry in report.items():
        if not entry["registered"]:
            console.echo(f"{browser:<8} 未注册")
            continue
        state = "manifest 正常" if entry["manifest_ok"] else "manifest 缺失/宿主路径失效"
        ids = ", ".join(entry["extensions"]) or "（无允许的扩展 ID）"
        console.echo(f"{browser:<8} {state}  允许 {ids}")
        console.echo(f"         {entry['registered']}")
    return 0


def _browsers(choice: str) -> list[str]:
    if choice == "all":
        return list(registry.BROWSERS)
    return [choice]


def _extension_ids(raw: str) -> list[str]:
    raw = (raw or "").strip()
    if not raw:
        raw = console.ask(
            "扩展 ID（在 chrome://extensions 开发者模式下可见，32 位小写字母）: "
        ).strip()
        if not raw:
            console.error("未提供扩展 ID")
            raise errors.UsageError("缺少 --extension-id")
    return [part.strip() for part in raw.split(",") if part.strip()]
