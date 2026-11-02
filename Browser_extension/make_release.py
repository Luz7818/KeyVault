"""打浏览器扩展发布包（Chrome Web Store / Edge 加载项 上传用的 zip）。

只收运行时文件：manifest、页面、脚本、src/、icons/。
排除：tests/、package.json（node 开发用）、README.md（开发者文档）。
产物：dist/keyvault-browser-extension-v<版本>.zip（dist 不入库，约定产物位）。

用法：python Browser_extension/make_release.py
"""

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(__file__).resolve().parent
DIST = ROOT / "dist"

INCLUDE = [
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
]
INCLUDE_DIRS = ["src", "icons"]


def version() -> str:
    text = (SRC / "manifest.json").read_text(encoding="utf-8")
    match = re.search(r'"version"\s*:\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("manifest.json 里找不到 version")
    return match.group(1)


def main() -> int:
    version_ = version()
    DIST.mkdir(exist_ok=True)
    target = DIST / f"keyvault-browser-extension-v{version_}.zip"
    if target.exists():
        target.unlink()

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in INCLUDE:
            source = SRC / name
            if not source.exists():
                raise SystemExit(f"缺少运行时文件：{name}")
            zf.write(source, name)
        for folder in INCLUDE_DIRS:
            for path in sorted((SRC / folder).rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(SRC).as_posix())

    print(f"[OK] 发布包：{target}")
    with zipfile.ZipFile(target) as zf:
        for name in sorted(zf.namelist()):
            print(f"  {name}")
        print(f"  共 {len(zf.namelist())} 个文件，{target.stat().st_size} 字节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
