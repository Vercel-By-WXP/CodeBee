# -*- coding: utf-8 -*-
"""代码任务断点续跑回归（A+B）：取消/失败后点重试不再从零烧 token。

A 跨 run 会话续跑：代码 run 把实现者 CLI 会话落盘为 impl_session；
   retry_task 继承给新 run（inherit_session），implement_all 仅在
   「同一实现者 + 同一工作目录 + 该 CLI 支持续会话」时复用；继承会话
   失效时丢弃原样重跑一次（认证/配额死因不重试）。
B 断点简报：失败/取消/超时的代码 run 收尾组装 handoff（走到哪/改了什么/
   还剩什么），retry 继承后注入 __CONTEXT__；活计划 [x] 勾选跨重写保留。

对照基线：连载已有 inherit（大纲+成章）、direct 已有 direct_session——
代码任务此前两头不沾，重试=全新会话+重新规划+重新探索。
"""
from __future__ import annotations

from base import BaseTest

FAKE_IMPL = {"id": "fake-cli", "label": "Fake CLI", "kind": "codex", "mode": "real",
             "command": "fake"}


def _ok_res(agent_id, sid=None):
    return {"ok": True, "text": "已完成", "json": None, "cost_usd": 0.0,
            "tokens": 1, "usage": None, "error": "",
            "sid": sid or ("sess-%s" % agent_id),
            "raw": {"exit_code": 0}, "kind": "codex", "model": None}


def _err_res(msg):
    return {"ok": False, "text": "", "json": None, "cost_usd": 0.0,
            "tokens": 0, "usage": None, "error": msg, "sid": "",
            "raw": {"exit_code": 1}, "kind": "codex", "model": None}


class TestCodeResume(BaseTest):

    def _code_task(self, title="修BUG"):
        from app.core import store
        return store.create_task({"type": "code", "title": title,
                                  "goal": "修复崩溃并让验证通过",
                                  "workdir": str(self.workdir), "mode": "fast",
                                  "verify_command": "exit 0"})

    def _execute(self, run_id, spawn=None, verify=(True, True), verify_raise=None):
        """全链路跑一遍 execute_run：桩掉 _spawn_step/_run_verify，不碰真实 CLI。

        返回 _spawn_step 的调用记录（role/resume/agent/prompt）。
        """
        from app.core import pipeline, store
        calls = []
        orig_spawn = pipeline._spawn_step
        orig_verify = pipeline._run_verify
        orig_agents = pipeline._agents

        def fake_spawn(**kw):
            calls.append({"role": kw.get("role"), "resume": kw.get("resume"),
                          "agent": (kw.get("agent") or {}).get("id"),
                          "prompt": kw.get("prompt") or ""})
            if spawn:
                return spawn(calls[-1])
            return _ok_res((kw.get("agent") or {}).get("id") or "fake-cli")

        def fake_verify(*a, **k):
            if verify_raise is not None:
                raise verify_raise
            return verify

        pipeline._spawn_step = fake_spawn
        pipeline._run_verify = fake_verify
        pipeline._agents = lambda: [dict(FAKE_IMPL)]
        store.update_task_status(store.get_run(run_id)["task_id"], "queued")
        try:
            pipeline.execute_run(run_id)
        finally:
            pipeline._spawn_step = orig_spawn
            pipeline._run_verify = orig_verify
            pipeline._agents = orig_agents
        return calls

    # ---- B：handoff 组装 + 注入 -------------------------------------------------

    def test_assemble_and_brief(self):
        """终态 run 组装断点简报；空简报零噪音。"""
        from app.core import pipeline
        (self.workdir / ".codebee").mkdir()
        (self.workdir / ".codebee" / "task_plan.md").write_text(
            "# 任务计划\n\n来源：test\n\n1. [x] 已完成项\n2. [ ] 未完成项\n",
            encoding="utf-8")
        run = {"status": "failed", "error": "boom", "ended_at": "2026-10-08 00:00:00",
               "steps": [{"role": "implement", "status": "done", "summary": "改了 a"},
                          {"role": "verify", "status": "failed", "summary": "验证挂了"}],
               "changes": {"files": [{"path": "a.py"}, {"path": "b.py"}]}}
        h = pipeline._assemble_handoff({"workdir": str(self.workdir)}, run)
        self.assertEqual(h["pending"], "verify（验证挂了）")
        self.assertEqual(h["plan_left"], [[2, " ", "未完成项"]])
        self.assertEqual(h["changed_files"], ["a.py", "b.py"])
        self.assertEqual(h["done_steps"][0]["role"], "implement")
        brief = pipeline._handoff_brief(h)
        self.assertIn("断点简报", brief)
        self.assertIn("failed", brief)
        self.assertIn("a.py", brief)
        self.assertIn("verify", brief)
        self.assertIn("未完成项", brief)
        # 空简报：零噪音
        self.assertEqual(pipeline._handoff_brief({}), "")
        self.assertEqual(pipeline._handoff_brief(None), "")
        self.assertEqual(pipeline._handoff_brief({"status": "failed"}), "")

    def test_failed_run_assembles_handoff_and_retry_injects(self):
        """实现成功后 verify 崩溃 → run 真实 failed 收口并组装 handoff；
        retry 继承后新 run 实现步提示词带断点简报。"""
        from app.core import store
        task = self._code_task()
        r1 = store.create_run("orchestration", task["title"], task_id=task["id"])
        self._execute(r1["id"], verify_raise=RuntimeError("verify-exploded"))
        r1f = store.get_run(r1["id"])
        self.assertEqual(r1f["status"], "failed", "步骤异常必须把 run 收口为 failed")
        h = r1f.get("handoff") or {}
        self.assertEqual(h.get("status"), "failed", "失败收尾必须组装断点简报")
        self.assertTrue(h.get("done_steps") or h.get("pending"), "简报必须携带现场信息")

        ok, err, r2 = store.retry_task(task["id"])
        self.assertTrue(ok, err)
        self.assertIn("handoff", r2, "失败 run 的断点简报必须被重试继承")
        self.assertEqual(r2["handoff"]["status"], "failed")

        calls = self._execute(r2["id"])
        impl_calls = [c for c in calls if str(c["role"]).startswith("implement")]
        self.assertTrue(impl_calls)
        self.assertIn("断点简报", impl_calls[0]["prompt"],
                      "断点简报必须注入实现步提示词（__CONTEXT__）")
        self.assertIn("verify-exploded", impl_calls[0]["prompt"],
                      "简报必须带上次失败原因")
        self.assertEqual(store.get_run(r2["id"])["status"], "done")

    # ---- A：会话跨 run 续跑 -----------------------------------------------------

    def test_retry_inherits_session_and_resume_used(self):
        """实现会话落盘 → 中断后 retry 继承 → 新 run 实现步带上 resume 会话 id。"""
        from app.core import store
        task = self._code_task()
        r1 = store.create_run("orchestration", task["title"], task_id=task["id"])
        calls = self._execute(r1["id"])
        r1f = store.get_run(r1["id"])
        self.assertEqual(r1f["status"], "done")
        self.assertEqual((r1f.get("impl_session") or {}).get("session"), "sess-fake-cli",
                         "实现步成功后必须把 CLI 会话 id 落盘 run")
        self.assertEqual((r1f.get("impl_session") or {}).get("agent"), "fake-cli")
        impl_calls = [c for c in calls if str(c["role"]).startswith("implement")]
        self.assertIsNone(impl_calls[0]["resume"], "首遍全新执行不应带 resume")

        # 模拟中断：把 done 翻成 cancelled（与 jobs.cancel 同一字段语义）
        store.update_run(r1["id"], expected_status="done", status="cancelled",
                         error="用户主动取消", ended_at="t")
        ok, err, r2 = store.retry_task(task["id"])
        self.assertTrue(ok, err)
        self.assertEqual((r2.get("inherit_session") or {}).get("session"), "sess-fake-cli",
                         "中断 run 的实现会话必须被重试继承")

        calls2 = self._execute(r2["id"])
        impl2 = [c for c in calls2 if str(c["role"]).startswith("implement")]
        self.assertEqual(impl2[0]["resume"], "sess-fake-cli",
                         "重试的实现步必须续用上一遍的 CLI 会话")

    def test_inherited_session_fallback_on_dead_session(self):
        """继承会话失效（非认证/配额死因）→ 丢弃 resume 原样重跑一次。"""
        from app.core import store
        task = self._code_task()
        r1 = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(r1["id"], expected_status="queued", status="failed",
                         error="x", ended_at="t",
                         impl_session={"agent": "fake-cli", "session": "dead-sid",
                                       "workdir": str(self.workdir)})
        ok, err, r2 = store.retry_task(task["id"])
        self.assertTrue(ok, err)
        self.assertEqual((r2.get("inherit_session") or {}).get("session"), "dead-sid")

        def spawn_by_resume(call):
            if call["resume"]:
                return _err_res("会话不存在或已过期")
            return _ok_res("fake-cli")

        calls = self._execute(r2["id"], spawn=spawn_by_resume)
        impl_calls = [c for c in calls if str(c["role"]).startswith("implement")]
        self.assertEqual(impl_calls[0]["resume"], "dead-sid")
        self.assertIsNone(impl_calls[1]["resume"], "失效继承会话必须被丢弃重跑")
        self.assertEqual(impl_calls[1]["agent"], "fake-cli")
        self.assertEqual(store.get_run(r2["id"])["status"], "done")

    def test_retry_skips_inherit_for_done_runs(self):
        """done 的 run 重试 = 主动重跑，不继承会话与简报。"""
        from app.core import store
        task = self._code_task()
        r1 = store.create_run("orchestration", task["title"], task_id=task["id"])
        self._execute(r1["id"])
        store.update_run(r1["id"], handoff={"status": "done", "done_steps": [{"role": "x"}]})
        ok, err, r2 = store.retry_task(task["id"])
        self.assertTrue(ok, err)
        self.assertNotIn("inherit_session", r2)
        self.assertNotIn("handoff", r2)

    # ---- B：活计划勾选跨重写保留 -------------------------------------------------

    def test_task_plan_carries_done_marks(self):
        """重写计划按标题继承 [x]；换标题不误继承；[!]/[>] 不继承。"""
        from app.core import pipeline
        plan = {"source": "test", "steps": [{"title": "定位崩溃点"}, {"title": "修复并自检"}]}
        self.assertTrue(pipeline._write_task_plan({}, str(self.workdir), plan))
        self.assertTrue(pipeline.mark_task_plan(str(self.workdir), 1, "done"))
        self.assertTrue(pipeline.mark_task_plan(str(self.workdir), 2, "fail"))
        # 重试重新规划：同标题 → [x] 保留、[!] 重置
        self.assertTrue(pipeline._write_task_plan({}, str(self.workdir), plan))
        text = (self.workdir / ".codebee" / "task_plan.md").read_text(encoding="utf-8")
        self.assertIn("1. [x] 定位崩溃点", text)
        self.assertIn("2. [ ] 修复并自检", text)
        # 换了标题的新计划：不误继承旧勾选
        plan2 = {"source": "test", "steps": [{"title": "全新步骤"}]}
        pipeline._write_task_plan({}, str(self.workdir), plan2)
        text2 = (self.workdir / ".codebee" / "task_plan.md").read_text(encoding="utf-8")
        self.assertIn("1. [ ] 全新步骤", text2)

    # ---- 工具函数 ---------------------------------------------------------------

    def test_same_workdir(self):
        from app.core import pipeline
        wd = str(self.workdir)
        self.assertTrue(pipeline._same_workdir(wd, wd))
        self.assertTrue(pipeline._same_workdir(wd.upper(), wd))  # Windows 大小写不敏感
        self.assertFalse(pipeline._same_workdir(wd, ""))
        self.assertFalse(pipeline._same_workdir("", wd))
        sub = str(self.workdir / "sub")
        self.assertFalse(pipeline._same_workdir(wd, sub))
