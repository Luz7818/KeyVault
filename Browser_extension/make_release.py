"""打 KeyVault 浏览器扩展发布包。

用法（任意目录可跑，输出固定在 Browser_extension/dist/ 下）：

    python -I Browser_extension/make_release.py

产出 dist/keyvault-browser-extension-v<版本>.zip——解压后目录即「加载已解压的
扩展程序」可直接载入的形态。浏览器扩展的发布件就是 zip：桌面端的
PyInstaller / build.bat / dist exe 链路在这里没有对应物，也不该有。

两条硬校验，任何一条不过都拒绝打包（发布物的不完整是最贵的静默失效——
装上一个缺文件的扩展，检测器可以从此失效却一切显示正常）：

1. 运行时文件齐全。清单显式列出（对齐 CONTRACTS.md C8 的文件独占划分），
   缺任何一个都报错退出。src 清单冻结是发布物语义的一部分：发布物内规则
   冻结不再变（C9#6），上游增删 src 文件必须同步本清单再打发布包。
2. 版本单源一致。版本号单源读 manifest.json；package.json 仅镜像，
   两处不一致即报错退出（CONTRACTS.md C7）。

清单恰好排除 tests/、package.json、CONTRACTS.md 与一切文档：它们是开发期
与发布流程的文件，扩展运行时一个都用不到。
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "manifest.json"
PACKAGE_PATH = ROOT / "package.json"
DIST_DIR = ROOT / "dist"

# 运行时文件全清单——manifest 声明的入口 + offscreen 页 + 图标 + src 整树。
# background/content/popup/options 由 app-background / app-content /
# app-popup / app-options 模块提供（CONTRACTS.md C8），缺失即拒绝打包。
RUNTIME_FILES: tuple[str, ...] = (
    "manifest.json",
    "background.js",
    "content.js",
    "offscreen.html",
    "offscreen.js",
    "popup.html",
    "popup.css",
    "popup.js",
    "options.html",
    "options.css",
    "options.js",
    "icons/icon.png",
    "src/core/rules.js",
    "src/core/placeholders.js",
    "src/core/candidate.js",
    "src/core/masking.js",
    "src/core/parse.js",
    "src/core/resolve.js",
    "src/core/corrections.js",
    "src/core/sha256.js",
    "src/vault/crypto.js",
    "src/vault/db.js",
    "src/vault/memory-backend.js",
    "src/vault/store.js",
    "src/vault/scan.js",
    "src/vault/detect.js",
    "src/vault/generator.js",
    "src/vault/importers.js",
    "src/vault/totp.js",
)


def setup_stdio() -> None:
    """把 stdout/stderr 切到 UTF-8（中文路径 + cp936 控制台，同 kv/console.py）。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            continue


def fail(message: str) -> None:
    raise SystemExit(f"make_release: {message}")


def read_version(path: Path, label: str) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail(f"{label} 不可读：{exc}")
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        fail(f"{label} 缺少 version 字段")
    return version.strip()


def check_versions() -> str:
    """版本单源 manifest.json，package.json 仅镜像（CONTRACTS.md C7）。"""
    manifest_version = read_version(MANIFEST_PATH, "manifest.json")
    package_version = read_version(PACKAGE_PATH, "package.json")
    if manifest_version != package_version:
        fail(
            f"版本不一致：manifest.json 是 {manifest_version}，"
            f"package.json 是 {package_version}"
            "——版本单源 manifest.json，package.json 仅镜像，改齐再打包"
        )
    return manifest_version


def collect_files() -> list[Path]:
    missing = [name for name in RUNTIME_FILES if not (ROOT / name).is_file()]
    if missing:
        fail("运行时文件缺失，拒绝打包：\n  " + "\n  ".join(missing))
    return [ROOT / name for name in RUNTIME_FILES]


def build_zip(version: str, files: list[Path]) -> Path:
    DIST_DIR.mkdir(exist_ok=True)
    zip_path = DIST_DIR / f"keyvault-browser-extension-v{version}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    # 回读校验：zip 条目与清单恰好一一对应，防打包库静默漏文件。
    with zipfile.ZipFile(zip_path) as archive:
        bad = archive.testzip()
        if bad is not None:
            fail(f"zip 完整性校验失败：{bad}")
        archived = archive.namelist()
    if archived != [name for name in RUNTIME_FILES]:
        fail(f"zip 条目与运行时清单不符：{archived}")
    return zip_path


def main() -> None:
    setup_stdio()
    version = check_versions()
    files = collect_files()
    zip_path = build_zip(version, files)
    print(f"发布包  {zip_path}")
    print(f"版本    {version}（单源 manifest.json，package.json 镜像一致）")
    print(f"共 {len(files)} 个运行时文件")
    for name in RUNTIME_FILES:
        print(f"  {name}")


if __name__ == "__main__":
    main()
