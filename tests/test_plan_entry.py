# -*- coding: utf-8 -*-
"""活计划 UI 入口（批3 09-25）单测：_has_task_plan 存在性帮手
（resolve+parents 守卫）+ file 端点通道（read_run_file 按名取 checkbox 正文）。

跑法：python -m unittest discover -s tests -p "test_plan_entry.py" -v
"""
from __future__ import annotations

import os
import sys

from base import BaseTest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "app"))


class PlanEntryTests(BaseTest):

    def test_flag_true_when_plan_exists(self):
        """计划文件在位 → True；正文含 checkbox 勾选事实。"""
        import main as srv
        from app.core import store
        d = self.workdir / ".codebee"
        d.mkdir(parents=True, exist_ok=True)
        (d / "task_plan.md").write_text(
            "# 任务计划\n\n1. [x] 已完成\n2. [ ] 未跑\n", encoding="utf-8")
        self.assertTrue(srv._has_task_plan(str(self.workdir)))
        # file 通道：read_run_file 按名可取（路径守卫放行 .codebee）
        task = store.create_task({"type": "code", "title": "通道", "goal": "g",
                                  "workdir": str(self.workdir)})
        run = store.create_run("orchestration", "通道", task_id=task["id"])
        data, err = store.read_run_file(run["id"], ".codebee/task_plan.md")
        self.assertIsNone(err, err)
        self.assertIn("任务计划", data.decode("utf-8"))
        self.assertIn("[x]", data.decode("utf-8"))

    def test_flag_false_when_absent_or_bad(self):
        """无计划/无目录/空参 → False 静默。"""
        import main as srv
        self.assertFalse(srv._has_task_plan(str(self.workdir)))
        self.assertFalse(srv._has_task_plan(""))
        blocker = self.tmp / "afile2"
        blocker.write_text("x", encoding="utf-8")
        self.assertFalse(srv._has_task_plan(str(blocker)))


if __name__ == "__main__":
    import unittest
    unittest.main()
