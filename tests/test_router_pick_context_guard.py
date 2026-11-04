# -*- coding: utf-8 -*-
"""路由影子对账 context 接线守测：生产 router.pick 调用点必须传 context。

机制闸（Raven 借鉴一期）收了影子台账（data/strategy_shadow.jsonl），但生产
pick 调用点全没传 context={"run_id","task_id"}——影子记录对不了账，
strategy_lab.report 全落 unresolved（2026-11-05 补线）。本守测防回归：
新增 pick 调用点不带 context 直接红。

已知例外：bookmeta._resolve_author 作用域里只有 task 没有 run，不硬造
关联键（真要接需先把 run 穿进签名）。若将来接上了，把该文件的期望清零。
"""
import ast
import unittest
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "app" / "core"

# 文件 → 允许的「无 context 路由调用」数量（pick / pick_reviewer / pick_critics）
EXPECTED_MISSING = {
    "pipeline.py": 0,        # 选将×3 + 复核评审×3 + 评审组×5（2026-11-05 扩面）
    "pipeline_code.py": 0,   # 选将×2 + 评审×2
    "jobs_mgmt.py": 0,       # _ai_repair
    "bookmeta.py": 1,        # 已知例外：_resolve_author 无 run 作用域
}

_ROUTING_SHAPES = ("pick", "pick_reviewer", "pick_critics")


def _pick_calls_without_context(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    missing = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _ROUTING_SHAPES):
            if not any(kw.arg == "context" for kw in node.keywords):
                missing.append(node.lineno)
    return missing


class RouterPickContextGuard(unittest.TestCase):
    def test_call_sites_pass_context(self):
        for fname, allowed in EXPECTED_MISSING.items():
            with self.subTest(file=fname):
                missing = _pick_calls_without_context(CORE / fname)
                self.assertEqual(
                    len(missing), allowed,
                    "%s 里 %d 处 router.pick 没传 context（行号 %s）——影子对账"
                    "会落 unresolved；请传 context={\"run_id\",\"task_id\"}"
                    % (fname, len(missing), missing))


if __name__ == "__main__":
    unittest.main()
