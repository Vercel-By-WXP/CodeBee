# -*- coding: utf-8 -*-
"""多线剧情推演（branching，inkos 借鉴）单测。

跑法：python -m unittest discover -s tests -p "test_branching.py" -v
契约：branches>=2 启用；一次调用产 N 条+pick 择优；失败静默回落；
审计文件追加；store 归一 branches 参数。
"""
from __future__ import annotations

import json
import os
from unittest import mock

from base import BaseTest


def _orch_reply(branches, pick=0):
    return {"ok": True, "text": "```json\n%s\n```" % json.dumps(
        {"branches": branches, "pick": pick}, ensure_ascii=False), "usage": None}


class BranchingTests(BaseTest):
    def test_disabled_returns_none(self):
        from app.core import branching
        self.assertIsNone(branching.plan_branches(
            "r-x", {}, 1, "g", "o", "p", 1))

    def test_no_orchestrator_returns_none(self):
        from app.core import branching, modelhub
        with mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=None):
            self.assertIsNone(branching.plan_branches(
                "r-x", {}, 1, "g", "o", "p", 2))

    def test_plan_picks_and_returns_beats_hook(self):
        from app.core import branching, modelhub, runner, store
        task = store.create_task({"type": "serial_novel", "title": "分支", "goal": "g",
                                  "workdir": str(self.workdir),
                                  "serial": {"chapters": 2, "branches": 2}})
        run = store.create_run("orchestration", "分支", task_id=task["id"])
        branches = [
            {"beats": "分支甲：主角正面硬刚", "hook": "钩甲", "why": "更贴大纲"},
            {"beats": "分支乙：主角绕后偷袭", "hook": "钩乙", "why": "风险大"},
        ]
        with mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=({"id": "p"}, "m")), \
             mock.patch.object(modelhub, "chat",
                               return_value=_orch_reply(branches, pick=1)):
            got = branching.plan_branches(run["id"], task, 1, "g", "o", "p", 2)
        self.assertIsNotNone(got)
        beats, hook = got
        self.assertIn("绕后偷袭", beats)          # pick=1 生效
        self.assertEqual(hook, "钩乙")
        # 审计文件：全部分支落盘，pick 标 ✅
        audit = (self.workdir / ".codebee" / "branch-plans.md").read_text(encoding="utf-8")
        self.assertIn("分支甲", audit)
        self.assertIn("分支乙", audit)
        self.assertIn("✅", audit)

    def test_failure_falls_back_silent(self):
        from app.core import branching, modelhub
        with mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=({"id": "p"}, "m")), \
             mock.patch.object(modelhub, "chat",
                               return_value={"ok": False, "text": "", "error": "x"}):
            self.assertIsNone(branching.plan_branches(
                "r-x", {}, 1, "g", "o", "p", 2))
        # 坏 JSON 同样静默
        with mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=({"id": "p"}, "m")), \
             mock.patch.object(modelhub, "chat",
                               return_value={"ok": True, "text": "not json"}), \
             mock.patch.object(runner_mod(), "extract_json", return_value=None):
            self.assertIsNone(branching.plan_branches(
                "r-x", {}, 1, "g", "o", "p", 2))

    def test_pick_out_of_range_clamped(self):
        from app.core import branching, modelhub, runner
        branches = [{"beats": "分支甲：有内容", "hook": "h", "why": "w"},
                    {"beats": "分支乙：也有内容", "hook": "h2", "why": "w2"}]
        with mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=({"id": "p"}, "m")), \
             mock.patch.object(modelhub, "chat",
                               return_value=_orch_reply(branches, pick=5)), \
             mock.patch.object(runner, "extract_json",
                               return_value={"branches": branches, "pick": 5}):
            got = branching.plan_branches("r-x", {}, 1, "g", "o", "p", 2)
        self.assertIsNotNone(got)
        self.assertIn("分支乙", got[0])   # pick=5 钳到末位（下标 1）

    def test_store_normalizes_branches(self):
        from app.core import store
        t = store.create_task({"type": "serial_novel", "title": "t", "goal": "g",
                               "workdir": str(self.workdir),
                               "serial": {"chapters": 2, "branches": 3}})
        self.assertEqual(t["serial"]["branches"], 3)
        t2 = store.create_task({"type": "serial_novel", "title": "t2", "goal": "g",
                                "workdir": str(self.workdir),
                                "serial": {"chapters": 2, "branches": 9}})
        self.assertEqual(t2["serial"]["branches"], 3)   # 越界钳到 3（同 variants 口径）
        t3 = store.create_task({"type": "serial_novel", "title": "t3", "goal": "g",
                                "workdir": str(self.workdir),
                                "serial": {"chapters": 2}})
        self.assertNotIn("branches", t3["serial"])   # 默认关


def runner_mod():
    from app.core import runner
    return runner


if __name__ == "__main__":
    import unittest
    unittest.main()
