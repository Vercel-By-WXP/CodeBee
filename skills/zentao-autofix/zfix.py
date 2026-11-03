#!/usr/bin/env python3
"""zfix 启动器：不依赖安装、不依赖 PYTHONPATH，直接 `python zfix.py <命令>`。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from zfix.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
