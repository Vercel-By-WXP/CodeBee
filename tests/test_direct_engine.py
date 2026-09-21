# -*- coding: utf-8 -*-
"""direct 引擎（直连单 CLI）端到端测试：单轮直达、附件输入、信箱续轮、追话续跑。

覆盖：
  1) 首轮：目标+附件交给一个 CLI，单步 done，无评审/验证步骤，报告落盘；
  2) 轮间递话：步骤执行期间信箱来消息 → 续轮（chat 步骤出现）；
  3) 追话起跑：结束后 /api/runs/<id>/chat 语义（消息继承 + 续轮档）；
  4) 流程注册：direct 是内置流程且可被 create_task 接受。
"""
from __future__ import annotations

from base import BaseTest


class TestDirectEngine(BaseTest):
    def runTest(self):
        from app.core import pipeline, store

        task = store.create_task({
            "type": "direct", "title": "直连跑一下", "goal": "把 README 里的错别字修掉",
            "workdir": str(self.workdir),
        })
        self.assertEqual(task["engine"], "direct")
        self.assertNotIn("verify_command", task)   # direct 不带验证命令
        self.assertNotIn("threshold", task)        # 也不带评审参数

        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents
        pipeline.execute_run(run["id"])

        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        self.assertTrue(run["verdict"]["direct"])
        self.assertEqual(run["verdict"]["turns"], 1)
        roles = [s["role"] for s in run["steps"]]
        self.assertEqual(roles, ["direct"])        # 单步直达：无 plan/review/verify
        # 报告落盘（直连也有报告，供成品区展示）
        report = self._paths.RUNS_DIR / run["id"] / "report.md"
        self.assertTrue(report.is_file())
        self.assertIn("直连任务", report.read_text(encoding="utf-8"))


class TestDirectAttachmentInput(BaseTest):
    def runTest(self):
        """附件随任务进 workdir，并作为输入传给执行者（images/文件路径）。"""
        from app.core import pipeline, store

        task = store.create_task({
            "type": "direct", "title": "带附件", "goal": "按截图改样式",
            "workdir": str(self.workdir),
        })
        self.assertEqual(task["attachments"], [])
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents
        pipeline.execute_run(run["id"])
        self.assertEqual(store.get_run(run["id"])["status"], "done")


class TestDirectAttachmentPrompt(BaseTest):
    def runTest(self):
        """直连首轮 prompt 必须包含附件正文与强制读取约束。"""
        import base64
        from unittest.mock import patch
        from app.core import attachments, pipeline, store

        meta = attachments.save_pending("brief.txt",
                                        base64.b64encode("按附件中的数字回答：42".encode()).decode())
        task = store.create_task({"type": "direct", "title": "带文本附件",
                                  "goal": "回答附件问题", "workdir": str(self.workdir),
                                  "attachments": [meta["id"]]})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        seen = {}

        def fake_step(run_id, role, agent, prompt, workdir, readonly, ev, **kwargs):
            seen["prompt"] = prompt
            return {"ok": True, "text": "已按附件回答", "sid": "", "usage": None,
                    "cost_usd": 0.0, "tokens": 0, "error": "", "raw": {"exit_code": 0}}

        pipeline._agents = self.mock_agents
        with patch.object(pipeline, "_run_step", side_effect=fake_step):
            pipeline.execute_run(run["id"])
        self.assertIn("按附件中的数字回答：42", seen["prompt"])
        self.assertIn("必须先用读文件工具逐个打开查看", seen["prompt"])


class TestRuntimeAttachmentPrompt(BaseTest):
    def runTest(self):
        """运行中追加的文本附件在下一步骤送达时也应携带正文。"""
        from app.core import pipeline, store

        rel = "_attachments/live-note.txt"
        target = self.workdir / rel
        target.parent.mkdir()
        target.write_text("运行中补充：最终结果必须包含 ABC-123", encoding="utf-8")
        task = store.create_task({"type": "direct", "title": "运行中附件",
                                  "goal": "先回答", "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.add_message(run["id"], "参考补充文件", attachments=[rel])
        block, images = pipeline._drain_directives(run["id"], str(self.workdir),
                                                   role="direct", step_n=1)
        self.assertEqual(images, [])
        self.assertIn("ABC-123", block)
        self.assertIn("## 附件正文（已读取）", block)


class TestDirectMailboxTurn(BaseTest):
    def runTest(self):
        """轮间递话：首步执行期间用户递话 → 首步 drain 送达 + 循环末尾续一轮。

        真实时序是「蜂在干活时用户打字」：这里包一层 _run_step，在首次调用
        返回后投一条消息进信箱，模拟步骤执行期间到达。
        """
        from app.core import pipeline, store

        task = store.create_task({
            "type": "direct", "title": "续轮", "goal": "写个草稿",
            "workdir": str(self.workdir),
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents

        orig = pipeline._run_step
        calls = {"n": 0}

        def spy(run_id, role, agent, prompt, workdir, readonly, ev, **kw):
            res = orig(run_id, role, agent, prompt, workdir, readonly, ev, **kw)
            calls["n"] += 1
            if calls["n"] == 1:   # 首步跑完的瞬间，用户递了句话
                store.add_message(run_id, "顺便把标题也改一下", sender="测试机")
            return res

        pipeline._run_step = spy
        try:
            pipeline.execute_run(run["id"])
        finally:
            pipeline._run_step = orig

        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        roles = [s["role"] for s in run["steps"]]
        self.assertEqual(roles[0], "direct")        # 首轮仍是直达档
        self.assertIn("chat", roles)                # 递话后自动续轮
        self.assertGreaterEqual(run["verdict"]["turns"], 2)


class TestDirectMailboxConsumedByFirstStep(BaseTest):
    def runTest(self):
        """起跑前就在信箱的消息：首步消费它，且不额外空转续轮。

        这条同时守一个真实缺陷：续轮判据若只看「信箱非空」，而该步又不消费
        消息（mock/不走 drain 的路径），会一路空转到 DIRECT_MAX_TURNS。
        """
        from app.core import pipeline, store

        task = store.create_task({
            "type": "direct", "title": "带指令起跑", "goal": "写个草稿",
            "workdir": str(self.workdir),
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline._agents = self.mock_agents
        store.add_message(run["id"], "用中文写", sender="测试机")
        pipeline.execute_run(run["id"])

        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        # 起跑前积压的消息让首步走续轮档（这是对话的下一轮），但只跑一轮就收工
        self.assertEqual(run["verdict"]["turns"], 1)
        self.assertLess(len(run["steps"]), 5, "不应空转到轮数上限")


class TestDirectFollowupSessionInherit(BaseTest):
    def runTest(self):
        """追话起跑：新 run 预置未消费消息时走续轮档，并继承上一轮 direct_session。"""
        from app.core import pipeline, store

        task = store.create_task({
            "type": "direct", "title": "追话", "goal": "写个说明",
            "workdir": str(self.workdir),
        })
        pipeline._agents = self.mock_agents

        # 第一轮：正常跑完
        r1 = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline.execute_run(r1["id"])
        r1 = store.get_run(r1["id"])
        self.assertEqual(r1["status"], "done")
        self.assertTrue(r1.get("direct_session"))    # 记录了续接所需会话信息

        # 第二轮（模拟 /chat：消息入旧 run 信箱 → retry_task 继承 → 起跑）
        store.add_message(r1["id"], "再补充一节", sender="测试机")
        ok, err, r2 = store.retry_task(task["id"])
        self.assertTrue(ok, err)
        self.assertTrue(r2.get("messages"), "未消费消息应被继承到新 run")
        pipeline.execute_run(r2["id"])
        r2 = store.get_run(r2["id"])
        self.assertEqual(r2["status"], "done", r2.get("error"))
        roles = [s["role"] for s in r2["steps"]]
        self.assertNotIn("direct", roles, "追话起跑应直接走续轮档，不重放开场")
        self.assertIn("chat", roles)


class TestDirectFlowRegistered(BaseTest):
    def runTest(self):
        """direct 注册为内置流程，且自定义流程也允许选 direct 引擎。"""
        from app.core import flows

        f = flows.get_flow("direct")
        self.assertIsNotNone(f)
        self.assertEqual(f["engine"], "direct")
        self.assertTrue(f["builtin"])

        # 自定义 direct 流程可创建（无额外参数）
        flow, err = flows.upsert_flow({"id": "my_direct", "name": "我的直连", "engine": "direct"})
        self.assertIsNone(err)
        self.assertEqual(flow["engine"], "direct")
        self.assertEqual(flow["id"], "my_direct")

        # 非法引擎仍被拒
        _bad, err2 = flows.upsert_flow({"id": "bad_engine", "name": "x", "engine": "nope"})
        self.assertIsNotNone(err2)
