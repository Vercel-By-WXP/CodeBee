# -*- coding: utf-8 -*-
"""端到端流水线测试：mock 智能体跑完小说与代码全流程（不碰真实 CLI）。"""
from __future__ import annotations

from base import BaseTest


class TestNovelPipeline(BaseTest):
    def runTest(self):
        from app.core import pipeline, store

        task = store.create_task({
            "type": "novel", "title": "测试章节", "goal": "写一个 800 字的开篇",
            "workdir": str(self.workdir), "implementer": "mock-a",
            "critics": ["mock-a", "mock-b"], "rounds": 2, "threshold": 7.0,
            "manuscript": "manuscript.md",
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents  # 只用 mock
        pipeline.execute_run(run["id"])

        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        self.assertTrue(run["verdict"]["publishable"])       # mock 第 2 轮必须达标
        self.assertEqual(run["verdict"]["rounds_used"], 2)   # 第 1 轮不达标 → 走了修订
        roles = [s["role"] for s in run["steps"]]
        self.assertIn("draft", roles)
        self.assertIn("revise-r1", roles)
        self.assertEqual(roles.count("critique-r1"), 2)      # 两个评审
        # 稿件与报告落盘
        ms = self.workdir / "manuscript.md"
        self.assertTrue(ms.is_file())
        self.assertIn("第 2 轮", ms.read_text(encoding="utf-8"))
        report = self._paths.RUNS_DIR / run["id"] / "report.md"
        self.assertTrue(report.is_file())
        self.assertIn("达到发布标准", report.read_text(encoding="utf-8"))


class TestNovelOneRoundPass(BaseTest):
    def runTest(self):
        from app.core import pipeline, store
        # 阈值压低 → 第 1 轮直接达标，不再修订
        task = store.create_task({
            "type": "novel", "title": "低阈值", "goal": "g",
            "workdir": str(self.workdir), "implementer": "mock-a",
            "critics": ["mock-b"], "rounds": 3, "threshold": 1.0,
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents
        pipeline.execute_run(run["id"])
        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        self.assertEqual(run["verdict"]["rounds_used"], 1)
        self.assertTrue(run["verdict"]["publishable"])


class TestCodePipeline(BaseTest):
    def runTest(self):
        from app.core import pipeline, store

        def make(verify):
            return store.create_task({
                "type": "code", "title": "代码任务", "goal": "加一个函数",
                "workdir": str(self.workdir), "implementer": "mock-a",
                "verify_command": verify,
            })

        # 验证通过 → 整体通过
        t1 = make("exit 0")
        r1 = store.create_run("orchestration", t1["title"], task_id=t1["id"])
        pipeline._agents = self.mock_agents
        pipeline.execute_run(r1["id"])
        r1 = store.get_run(r1["id"])
        self.assertEqual(r1["status"], "done", r1.get("error"))
        self.assertTrue(r1["verdict"]["pass"])
        self.assertTrue(r1["verdict"]["verify_pass"])
        self.assertTrue((self.workdir / "mock-impl.txt").is_file())

        # 验证失败 → 整体不通过（即使 mock 评审说 pass）
        t2 = make("exit 3")
        r2 = store.create_run("orchestration", t2["title"], task_id=t2["id"])
        pipeline.execute_run(r2["id"])
        r2 = store.get_run(r2["id"])
        self.assertEqual(r2["status"], "done", r2.get("error"))
        self.assertFalse(r2["verdict"]["pass"])
        self.assertFalse(r2["verdict"]["verify_pass"])


if __name__ == "__main__":
    import unittest as _u
    _u.main()
