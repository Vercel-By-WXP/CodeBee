# -*- coding: utf-8 -*-
"""Stop gate（planning-with-files 第四件）单测：收工时活计划勾选完整性可见。

跑法：python -m unittest discover -s tests -p "test_plan_stopgate.py" -v
"""
from __future__ import annotations

from base import BaseTest


class PlanStopgateUnitTests(BaseTest):

    def _gate(self):
        from app.core import pipeline
        return pipeline.plan_stopgate(str(self.workdir))

    def _write_plan(self, text):
        from pathlib import Path
        d = self.workdir / ".codebee"
        d.mkdir(parents=True, exist_ok=True)
        (d / "task_plan.md").write_text(text, encoding="utf-8")

    def test_no_plan_file_passes(self):
        """无计划文件 → (0, [])（无计划不拦）。"""
        self.assertEqual(self._gate(), (0, []))

    def test_all_checked_passes(self):
        """全 [x] → 总数对、零未完成。"""
        self._write_plan("# 任务计划\n\n来源：test\n\n1. [x] a\n2. [x] b\n")
        total, left = self._gate()
        self.assertEqual((total, left), (2, []))

    def test_leftovers_flagged(self):
        """[ ]（未跑）与 [!]（失败）与 [>]（中断）都算未完成，带序号与标记。"""
        self._write_plan("# 任务计划\n\n来源：test\n\n1. [x] a\n2. [ ] b\n3. [!] c\n4. [>] d\n")
        total, left = self._gate()
        self.assertEqual(total, 4)
        self.assertEqual([(n, mk) for n, mk, _ in left], [(2, " "), (3, "!"), (4, ">")])
        self.assertEqual(left[0][2], "b")


class PlanStopgateWiringTests(BaseTest):
    def runTest(self):
        """全链路双面：全勾时摘要零噪声；有剩项时摘要点名 ⚠ 且 verdict 带 plan_gate。"""
        from app.core import pipeline, store

        # 正面：mock 全链路（勾选随活计划回写全 [x]）→ 摘要不含 ⚠、verdict 无 plan_gate
        task = store.create_task({
            "type": "code", "title": "闸门正面", "goal": "g",
            "workdir": str(self.workdir), "mode": "auto", "verify_command": "exit 0",
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents
        pipeline.execute_run(run["id"])
        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        self.assertNotIn("⚠", run.get("summary") or "")
        self.assertNotIn("plan_gate", run.get("verdict") or {})

        # 反面：补一个未勾项再跑一轮（gate 直接读文件事实）
        from pathlib import Path
        plan = self.workdir / ".codebee" / "task_plan.md"
        plan.write_text(plan.read_text(encoding="utf-8") + "2. [ ] 漏掉的一步\n",
                        encoding="utf-8")
        task2 = store.create_task({
            "type": "code", "title": "闸门反面", "goal": "g2",
            "workdir": str(self.workdir), "mode": "auto", "verify_command": "exit 0",
        })
        run2 = store.create_run("orchestration", task2["title"], task_id=task2["id"])
        # 钉住计划重写：让本轮沿用已有计划文件（模拟赛马/异常路径不回写的存量态）
        orig_write = pipeline._write_task_plan
        pipeline._write_task_plan = lambda *a, **k: ""
        try:
            pipeline.execute_run(run2["id"])
        finally:
            pipeline._write_task_plan = orig_write
        run2 = store.get_run(run2["id"])
        self.assertEqual(run2["status"], "done", run2.get("error"))
        self.assertIn("⚠ 活计划", run2.get("summary") or "")
        self.assertIn("plan_gate", run2.get("verdict") or {})


if __name__ == "__main__":
    import unittest
    unittest.main()
