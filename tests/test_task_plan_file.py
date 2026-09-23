# -*- coding: utf-8 -*-
"""task_plan.md 计划落盘（planning-with-files 借鉴）单测。

跑法：python -m unittest discover -s tests -p "test_task_plan_file.py" -v
"""
from __future__ import annotations

import os

from base import BaseTest


class TaskPlanFileTests(BaseTest):

    def test_written_with_steps(self):
        from app.core import pipeline
        plan = {"source": "orchestrator",
                "steps": [{"title": "搭骨架", "detail": "先建目录"},
                          {"title": "写逻辑", "detail": "实现核心"}]}
        p = pipeline._write_task_plan({"id": "t"}, str(self.workdir), plan)
        self.assertTrue(p and os.path.isfile(p))
        text = (self.workdir / ".codebee" / "task_plan.md").read_text(encoding="utf-8")
        self.assertIn("# 任务计划", text)
        self.assertIn("orchestrator", text)
        self.assertIn("1. [ ] 先建目录", text)   # 活计划：checkbox 形态（2026-09-24 起）
        self.assertIn("2. [ ] 实现核心", text)

    def test_empty_plan_silent(self):
        from app.core import pipeline
        p = pipeline._write_task_plan({"id": "t"}, str(self.workdir), {})
        self.assertEqual(p, "")   # 无步骤：不产空计划文件
        self.assertFalse((self.workdir / ".codebee" / "task_plan.md").exists())

    def test_bad_workdir_silent(self):
        from app.core import pipeline
        blocker = self.tmp / "afile"
        blocker.write_text("x", encoding="utf-8")
        self.assertEqual(pipeline._write_task_plan({"id": "t"}, str(blocker),
                                                   {"steps": [{"title": "a"}]}), "")


if __name__ == "__main__":
    unittest.main()
