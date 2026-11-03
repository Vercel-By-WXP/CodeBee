# -*- coding: utf-8 -*-
"""发布包完整性：zfix 引擎必须随 npm 包分发（zfix_bridge 的运行前提）。

桥在运行时动态加载 skills/zentao-autofix/zfix——目录不在 package.json files
白名单里时，发布安装包中桥会静默降级（指纹/沉淀/预防全部失效），且没人报错。
这条守测就是防那个静默回归的。
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = [
    "skills/zentao-autofix/zfix",
    "skills/zentao-autofix/knowledge",
    "skills/zentao-autofix/recipes",
]


class ZfixPackagingTests(unittest.TestCase):
    def test_engine_in_npm_files_whitelist(self):
        pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        files = pkg.get("files") or []
        for entry in REQUIRED:
            self.assertIn(entry, files, "package.json files 缺 %s —— 桥在发布包会静默降级" % entry)

    def test_engine_files_exist_on_disk(self):
        # 桥的加载判据：这个文件在，引擎才算可用
        probe = ROOT / "skills" / "zentao-autofix" / "zfix" / "knowledge.py"
        self.assertTrue(probe.is_file(), "引擎缺 %s" % probe)

    def test_no_pyc_shipped(self):
        # files 显式条目会越过 .npmignore 的 **/__pycache__ 规则。缓存会在
        # 运行时不断再生（桥一加载就有），所以这里不是「断言干净」而是
        # 「先清再验」——把清理动作本身固化进发布前检查，顺带验证清得掉。
        import shutil
        base = ROOT / "skills" / "zentao-autofix"
        for cache in base.rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)
        bad = [str(p.relative_to(ROOT)) for p in base.rglob("*.py[co]")]
        self.assertEqual(bad, [], "字节码缓存删不掉：%s" % bad[:5])

    def test_bridge_points_at_whitelisted_engine_dir(self):
        src = (ROOT / "app" / "core" / "zfix_bridge.py").read_text(encoding="utf-8")
        self.assertRegex(src, r"skills.*zentao-autofix",
                         "桥不再指向 skills/zentao-autofix —— package.json files 要同步改")


if __name__ == "__main__":
    unittest.main()
