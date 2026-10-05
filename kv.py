"""启动 shim。

python -I 在这台机器上**不是干净环境**：一个 editable 安装的 .pth
（__editable__.zhishuxing-2.1.0.pth）会把另一个项目的 src 泄漏到 sys.path 上。
所以这里显式把仓库根插到 sys.path[0]，且 kv/ 内部一律用绝对的包限定导入
（from kv.core.repo import ...，绝不 import repo）。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 必须在 import kv.cli **之前**做：本仓库路径含中文，而 Windows 控制台 CP 是 936。
# 不先切 UTF-8 的话，导入期的报错（SyntaxError、ImportError）里的路径全是乱码 ——
# 而那恰恰是最需要看清路径的时刻。cli.main() 里还会再调一次，幂等。
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

from kv.cli import main  # noqa: E402  —— 必须在 sys.path 修好之后导入

if __name__ == "__main__":
    sys.exit(main())
