# -*- coding: utf-8 -*-
"""路由函数体内禁止函数级 import store（遮蔽闸）。

函数内 `import store` / `from core import ..., store` 会把 store 编译成
整个路由函数的局部名：凡在**该 import 行之前**执行到 `store.xxx` 的路由
一律 UnboundLocalError。065c030b（0.1.70 全线写接口 Failed to fetch 实案）
与 2026-10-20（90e24d1 复发，记忆中心/运行详情/检查点全线 500）同一病根。
store 已在 app/main.py 模块顶层导入，路由体内永远不需要再 import。
"""

import ast
import os
import unittest

MAIN_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "app", "main.py")

ROUTER_FUNCS = {"do_GET", "_route_get", "do_POST", "_route_post"}
POISON_NAME = "store"


def _imports_binding_store(node):
    """Return import alias lines inside `node` that bind the local name `store`."""
    hits = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Import):
            for alias in sub.names:
                bound = alias.asname or alias.name.split(".")[0]
                if bound == POISON_NAME:
                    hits.append((sub.lineno, ast.dump(sub)[:80]))
        elif isinstance(sub, ast.ImportFrom):
            for alias in sub.names:
                bound = alias.asname or alias.name
                if bound == POISON_NAME:
                    hits.append((sub.lineno, "%s line %d" % (ast.dump(sub)[:60], sub.lineno)))
    return hits


class TestRouteStoreShadowGuard(unittest.TestCase):
    def test_main_compiles(self):
        with open(MAIN_PATH, "rb") as fh:
            source = fh.read()
        compile(source, MAIN_PATH, "exec")

    def test_routers_free_of_local_store_import(self):
        with open(MAIN_PATH, "rb") as fh:
            tree = ast.parse(fh.read().decode("utf-8"))
        poisons = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in ROUTER_FUNCS:
                for lineno, dump in _imports_binding_store(node):
                    poisons.append("%s: %s -> %s" % (node.name, dump, lineno))
        self.assertEqual(
            poisons, [],
            "路由函数体内出现函数级 import store（遮蔽整个函数，见模块 docstring）：%s"
            % "; ".join(poisons))


if __name__ == "__main__":
    unittest.main()
