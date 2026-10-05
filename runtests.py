"""测试入口。

为什么不直接用 python -I -m unittest：-I 隐含 -P 和 -E，既不把 cwd 放进
sys.path，也忽略 PYTHONPATH。于是 `python -I -m unittest tests.test_x` 会报
ModuleNotFoundError: No module named 'tests'，而 discover -t . 虽然能跑却没法
按名字选单个用例。这个 shim 一次解决两件事。

用法：
    python -I runtests.py                       跑全部
    python -I runtests.py tests.test_cli_m0     跑一个模块
    python -I runtests.py tests.test_x.TestY.test_z
    python -I runtests.py -v                    详细输出
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    argv = sys.argv[1:]
    if not argv or all(a.startswith("-") for a in argv):
        argv = ["discover", "-s", "tests", "-t", str(ROOT), *argv]
    result = unittest.main(module=None, argv=[sys.argv[0], *argv], exit=False)
    return 0 if result.result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
