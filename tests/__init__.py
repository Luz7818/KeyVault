"""让 tests/ 在 python -I 下也能 import kv。

-I 隐含 -P，不会把 cwd 放进 sys.path；而这台机器上还有一个 editable 安装的
.pth（__editable__.zhishuxing-2.1.0.pth）会把别的项目的 src 泄漏进来。所以这里
显式把仓库根插到 sys.path[0]。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
