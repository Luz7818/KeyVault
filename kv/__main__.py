"""让 python -m kv 可用。"""

import sys

from kv.cli import main

if __name__ == "__main__":
    sys.exit(main())
