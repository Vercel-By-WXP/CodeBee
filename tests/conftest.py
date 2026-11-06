"""pytest 路径基线：让单跑 / 组合跑也能 import ``core.*``。

历史坑（2026-11-06）：全量 ``pytest tests -q`` 一直绿，靠的是按文件名排序时
靠前的某几个测试文件在顶层执行了 ``sys.path.insert(0, app)``，收集期顺手
把路径开出来。显式列文件时（``pytest tests/test_quality_appeal.py -q``）
这些开路文件不在收集列表里，就会 ``ModuleNotFoundError: No module named
'core'``，很容易被误判成「代码回归」或「新守测写坏了」。

conftest 在任何测试模块导入之前加载，在这里一次性建好路径即可。只加仓库内
的 ``app/``，不改 cwd、不改 import mode、不引入第三方依赖；各守测文件里已有
的 insert 重复也无害。
"""
import sys
from pathlib import Path

_APP = str(Path(__file__).resolve().parent.parent / "app")
if _APP not in sys.path:
    sys.path.insert(0, _APP)
