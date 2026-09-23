# -*- coding: utf-8 -*-
"""活计划回写（planning-with-files 第二层借鉴）单测：
task_plan.md 的勾选框随执行实时推进（[>] 进行中 → [x] 完成 / [!] 失败），
崩溃/换将/续跑时看文件即知断点；回写幂等且失败静默。

跑法：python -m unittest discover -s tests -p "test_task_plan_live.py" -v
"""
from __future__ import annotations

from base import BaseTest


def _mk_plan(pipeline, workdir, titles):
    plan = {"source": "orchestrator",
            "steps": [{"title": t, "detail": t} for t in titles]}
    return pipeline._write_task_plan({"id": "t"}, str(workdir), plan)


class MarkTaskPlanTests(BaseTest):

    def _mark(self, idx, status):
        from app.core import pipeline
        return pipeline.mark_task_plan(str(self.workdir), idx, status)

    def test_run_done_flip(self):
        """起跑 [>] → 完成 [x]，标题保持不变。"""
        from app.core import pipeline
        _mk_plan(pipeline, self.workdir, ["第一步", "第二步"])
        self.assertTrue(self._mark(1, "run"))
        self.assertTrue(self._mark(1, "done"))
        text = (self.workdir / ".codebee" / "task_plan.md").read_text(encoding="utf-8")
        self.assertIn("1. [x] 第一步", text)
        self.assertIn("2. [ ] 第二步", text)      # 未动

    def test_fail_then_retry_success(self):
        """失败标 [!] 后换将重试成功翻回 [x]（幂等覆盖语义）。"""
        from app.core import pipeline
        _mk_plan(pipeline, self.workdir, ["唯一步"])
        self.assertTrue(self._mark(1, "fail"))
        text = (self.workdir / ".codebee" / "task_plan.md").read_text(encoding="utf-8")
        self.assertIn("1. [!] 唯一步", text)
        self.assertTrue(self._mark(1, "done"))
        text = (self.workdir / ".codebee" / "task_plan.md").read_text(encoding="utf-8")
        self.assertIn("1. [x] 唯一步", text)

    def test_mark_idempotent(self):
        """重复标 done 不重复改写、内容稳定。"""
        from app.core import pipeline
        _mk_plan(pipeline, self.workdir, ["a"])
        self.assertTrue(self._mark(1, "done"))
        first = (self.workdir / ".codebee" / "task_plan.md").read_text(encoding="utf-8")
        self.assertTrue(self._mark(1, "done"))
        second = (self.workdir / ".codebee" / "task_plan.md").read_text(encoding="utf-8")
        self.assertEqual(first, second)

    def test_silent_failures(self):
        """文件不存在/越界序号/非法状态/空 workdir → False 静默不抛。"""
        from app.core import pipeline
        # 未落盘
        self.assertFalse(self._mark(1, "done"))
        _mk_plan(pipeline, self.workdir, ["a"])
        self.assertFalse(self._mark(0, "done"))     # 序号 1 基
        self.assertFalse(self._mark(9, "done"))     # 越界
        self.assertFalse(self._mark(1, "bogus"))    # 非法状态
        self.assertFalse(pipeline.mark_task_plan("", 1, "done"))
        # 非法状态不改内容
        text = (self.workdir / ".codebee" / "task_plan.md").read_text(encoding="utf-8")
        self.assertIn("1. [ ] a", text)


class LivePlanE2ETests(BaseTest):
    def runTest(self):
        """全链路：mock code 任务跑完，task_plan.md 全部翻成 [x]。"""
        from app.core import pipeline, store
        task = store.create_task({
            "type": "code", "title": "活计划", "goal": "g",
            "workdir": str(self.workdir), "mode": "auto",
            "verify_command": "exit 0",
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents
        pipeline.execute_run(run["id"])
        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        p = self.workdir / ".codebee" / "task_plan.md"
        self.assertTrue(p.exists(), "计划文件应落盘")
        text = p.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^\d+\. \[x\] ", msg=text)   # 至少一项已勾


if __name__ == "__main__":
    import unittest
    unittest.main()
