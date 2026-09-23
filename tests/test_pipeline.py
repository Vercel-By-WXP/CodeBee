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
        self.assertEqual(run["task_spec"]["type"], "novel")
        self.assertEqual(run["task_spec"]["engine"], "review")
        self.assertIn("implement", run["route_plan"])
        self.assertIn("candidates", run["route_plan"]["implement"])
        self.assertEqual(run["route_plan"]["implement"]["selected"], "mock-a")
        self.assertEqual(run["route_plan"]["review"]["participants"],
                         ["mock-a", "mock-b"])
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
        self.assertEqual(r1["route_plan"]["implement"]["selected"], "mock-a")
        self.assertTrue(r1["route_plan"]["review"]["selected"])
        self.assertTrue((self.workdir / "mock-impl.txt").is_file())

        # 验证失败 → 整体不通过（即使 mock 评审说 pass）
        t2 = make("exit 3")
        r2 = store.create_run("orchestration", t2["title"], task_id=t2["id"])
        pipeline.execute_run(r2["id"])
        r2 = store.get_run(r2["id"])
        self.assertEqual(r2["status"], "done", r2.get("error"))
        self.assertFalse(r2["verdict"]["pass"])
        self.assertFalse(r2["verdict"]["verify_pass"])


class TestCodeFallbackRoute(BaseTest):
    def runTest(self):
        import threading
        from unittest.mock import patch
        from app.core import pipeline, store, task_compile

        agents = [
            {"id": "primary", "label": "Primary", "kind": "codex", "mode": "real"},
            {"id": "fallback", "label": "Fallback", "kind": "opencode", "mode": "real"},
        ]
        task = store.create_task({
            "type": "code", "title": "换将路由", "goal": "完成代码任务",
            "workdir": str(self.workdir), "verify_command": "exit 0",
        })
        task = dict(task)
        task["difficulty"] = "default"
        task["engine"] = "code"
        task["_compiled_spec"] = task_compile.compile_task(task)
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")

        def fake_step(_run_id, _role, agent, *_args, **_kwargs):
            return {"ok": agent["id"] == "fallback", "error": "primary failed",
                    "text": "", "sid": ""}

        plan = {"source": "test", "steps": [{"title": "实现", "detail": "实现"}]}
        review = {"pass": True, "scores": {"正确性": 9}, "issues": []}
        with patch.object(pipeline.planner, "make_code_plan", return_value=plan), \
                patch.object(pipeline.modelhub, "bind_agent", side_effect=lambda a, *_: a), \
                patch.object(pipeline, "_run_step", side_effect=fake_step), \
                patch.object(pipeline, "_run_review", return_value=review), \
                patch.object(pipeline, "_run_verify", return_value=(True, True)):
            pipeline._run_code(run, task, agents, threading.Event(), {}, "auto")

        saved = store.get_run(run["id"])
        self.assertEqual(saved["status"], "done", saved.get("error"))
        self.assertEqual(saved["route_plan"]["implement"]["selected"], "fallback")
        self.assertEqual(saved["route_plan"]["implement"]["participants"], ["fallback"])
        self.assertNotIn("fallback", saved["route_plan"]["implement"]["fallback"])


class TestCodeAuthFailureStopsCliSwitch(BaseTest):
    def runTest(self):
        import threading
        from unittest.mock import patch
        from app.core import pipeline, store, task_compile

        primary = {"id": "primary", "label": "Primary", "kind": "codex",
                   "mode": "real"}
        fallback = {"id": "fallback", "label": "Fallback", "kind": "opencode",
                    "mode": "real"}
        agents = [primary, fallback]
        task = store.create_task({
            "type": "code", "title": "认证失败不换 CLI", "goal": "完成代码任务",
            "workdir": str(self.workdir), "verify_command": "exit 0",
            "implementer": "primary",
        })
        task = dict(task, difficulty="default", engine="code")
        task["_compiled_spec"] = task_compile.compile_task(task)
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        attempted = []

        def fake_step(_run_id, _role, agent, *_args, **_kwargs):
            attempted.append(agent["id"])
            if agent["id"] == "primary":
                return {"ok": False, "error": "退出码 1；stderr/stdout: HTTP 401 Unauthorized",
                        "text": "", "sid": ""}
            return {"ok": True, "error": "", "text": "done", "sid": ""}

        plan = {"source": "test", "steps": [{"title": "实现", "detail": "实现"}]}
        with patch.object(pipeline.planner, "make_code_plan", return_value=plan), \
                patch.object(pipeline.modelhub, "bind_agent", side_effect=lambda a, *_: a), \
                patch.object(pipeline, "_run_step", side_effect=fake_step):
            pipeline._run_code(store.get_run(run["id"]), task, agents,
                               threading.Event(), {}, "auto")

        saved = store.get_run(run["id"])
        self.assertEqual(attempted, ["primary"])
        self.assertEqual(saved["status"], "failed")
        self.assertIn("认证", saved.get("error", ""))


class TestCodeFallbackThenPrimaryRepairRoute(BaseTest):
    def runTest(self):
        import threading
        from unittest.mock import patch
        from app.core import pipeline, store, task_compile

        primary = {"id": "primary", "label": "Primary", "kind": "codex",
                   "mode": "real"}
        fallback = {"id": "fallback", "label": "Fallback", "kind": "opencode",
                    "mode": "real"}
        agents = [primary, fallback]
        task = store.create_task({
            "type": "code", "title": "兜底后修复回切", "goal": "完成代码任务",
            "workdir": str(self.workdir), "verify_command": "exit 0",
        })
        task = dict(task)
        task["context"] = "ATTACHMENT-CONTEXT-REQUIRED"
        task["difficulty"] = "hard"
        task["engine"] = "code"
        task["_compiled_spec"] = task_compile.compile_task(task)
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        primary_implements = [0]
        fix_prompts = []

        def fake_step(_run_id, role, agent, *_args, **_kwargs):
            if role.startswith("fix-"):
                fix_prompts.append(_args[0])
            if role == "implement" and agent["id"] == "primary":
                primary_implements[0] += 1
                return {"ok": False, "error": "primary initial failed",
                        "text": "", "sid": ""}
            return {"ok": True, "error": "", "text": "", "sid": ""}

        reviews = [
            {"pass": False, "scores": {"正确性": 5}, "issues": []},
            {"pass": True, "scores": {"正确性": 9}, "issues": []},
        ]
        plan = {"source": "test", "steps": [{"title": "实现", "detail": "实现"}]}
        with patch.object(pipeline.planner, "make_code_plan", return_value=plan), \
                patch.object(pipeline.modelhub, "bind_agent", side_effect=lambda a, *_: a), \
                patch.object(pipeline, "_run_step", side_effect=fake_step), \
                patch.object(pipeline, "_run_review", side_effect=reviews), \
                patch.object(pipeline, "_run_verify", return_value=(True, True)):
            pipeline._run_code(run, task, agents, threading.Event(), {}, "auto")

        saved = store.get_run(run["id"])
        self.assertEqual(saved["status"], "done", saved.get("error"))
        self.assertTrue(saved["verdict"]["pass"])
        self.assertEqual(primary_implements[0], 1)
        self.assertTrue(fix_prompts)
        self.assertIn("ATTACHMENT-CONTEXT-REQUIRED", fix_prompts[0])
        self.assertEqual(saved["route_plan"]["implement"]["selected"], "primary")
        self.assertIn("修复轮", saved["route_plan"]["implement"]["selection_reason"])


class TestProjectMemoryKeepsAttachmentContext(BaseTest):
    def runTest(self):
        """项目记忆只限制自身长度，不能截断已有附件正文或结束标记。"""
        import base64
        import threading
        from unittest.mock import patch
        from app.core import attachments, pipeline, store, task_compile

        meta = attachments.save_pending(
            "long-spec.txt", base64.b64encode(("附件要求" * 1800).encode()).decode())
        task = store.create_task({"type": "code", "title": "长附件", "goal": "实现需求",
                                  "workdir": str(self.workdir), "attachments": [meta["id"]]})
        memory = self.workdir / ".codebee" / "project-memory.md"
        memory.parent.mkdir()
        memory.write_text("历史架构事实" * 1000, encoding="utf-8")
        task = dict(task, difficulty="default", _compiled_spec=task_compile.compile_task(task))
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        seen = {}

        def plan(task_arg, *_args, **_kwargs):
            seen["context"] = task_arg["context"]
            return {"source": "test", "steps": [{"title": "实现", "detail": "实现"}]}

        agents = self.mock_agents()
        with patch.object(pipeline.planner, "make_code_plan", side_effect=plan), \
                patch.object(pipeline, "_run_review", return_value={"pass": True, "issues": []}), \
                patch.object(pipeline, "_run_verify", return_value=(True, False)):
            pipeline._run_code(run, task, agents, threading.Event(), {}, "auto")
        self.assertIn("<!-- codebee-attachments:end -->", seen["context"])
        self.assertIn("历史架构事实", seen["context"])


class TestSerialFallbackRouteReturnsToPrimary(BaseTest):
    def runTest(self):
        from unittest.mock import patch
        from app.core import pipeline, store, task_compile

        class NoWaitEvent:
            @staticmethod
            def is_set():
                return False

            @staticmethod
            def wait(_seconds):
                return False

        primary = {"id": "author-a", "label": "Author A", "kind": "codex",
                   "mode": "real", "command": "a"}
        backup = {"id": "author-b", "label": "Author B", "kind": "opencode",
                  "mode": "real", "command": "b"}
        critic = {"id": "mock-b", "label": "Mock critic", "kind": "mock",
                  "mode": "mock", "command": ""}
        agents = [primary, backup, critic]
        task = store.create_task({
            "type": "serial_novel", "title": "作者回切", "goal": "写两章",
            "workdir": str(self.workdir), "threshold": 5.0,
            "serial": {"chapters": 2, "words_per_chapter": 300},
        })
        task = dict(task)
        task["difficulty"] = "default"
        task["_compiled_spec"] = task_compile.compile_task(task)
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")

        outline = {"book_title": "测试书", "source": "test", "chapters": [
            {"title": "第一章", "beats": "起", "hook": "钩子"},
            {"title": "第二章", "beats": "承", "hook": "钩子"},
        ]}

        def fake_step(_run_id, role, agent, *_args, **_kwargs):
            if role == "draft-c1" and agent["id"] == "author-a":
                return {"ok": False, "error": "primary unavailable", "text": "", "sid": ""}
            if role.startswith("draft-c"):
                chapter = int(role.split("c", 1)[1].split("-", 1)[0])
                pipeline._write_chapter(str(self.workdir), chapter, "山" * 400)
            elif role.startswith("revise-c"):
                chapter = int(role.split("c", 1)[1])
                pipeline._write_chapter(str(self.workdir), chapter, "海" * 400)
            return {"ok": True, "error": "", "text": "", "sid": ""}

        with patch.object(pipeline.planner, "make_serial_outline", return_value=outline), \
                patch.object(pipeline.modelhub, "bind_agent", side_effect=lambda a, *_: a), \
                patch.object(pipeline, "_run_step", side_effect=fake_step), \
                patch.object(pipeline.time, "sleep", return_value=None):
            pipeline._run_serial_review(
                run, task, agents, NoWaitEvent(), {}, "auto", [critic],
                primary, {"author": "初始主选", "critics": "mock 评审"},
                None, "default")

        saved = store.get_run(run["id"])
        self.assertEqual(saved["status"], "done", saved.get("error"))
        self.assertEqual(saved["route_plan"]["implement"]["selected"], "author-a")
        self.assertEqual(saved["route_plan"]["implement"]["participants"],
                         ["author-a", "author-b"])


class TestSerialDelayedFallbackRoute(BaseTest):
    def runTest(self):
        from unittest.mock import patch
        from app.core import pipeline, store, task_compile

        class NoWaitEvent:
            @staticmethod
            def is_set():
                return False

            @staticmethod
            def wait(_seconds):
                return False

        primary = {"id": "author-a", "label": "Author A", "kind": "codex",
                   "mode": "real", "command": "a"}
        backup = {"id": "author-b", "label": "Author B", "kind": "opencode",
                  "mode": "real", "command": "b"}
        critic = {"id": "mock-b", "label": "Mock critic", "kind": "mock",
                  "mode": "mock", "command": ""}
        agents = [primary, backup, critic]
        task = store.create_task({
            "type": "serial_novel", "title": "延迟落盘归因", "goal": "写一章",
            "workdir": str(self.workdir), "threshold": 5.0,
            "serial": {"chapters": 1, "words_per_chapter": 300},
        })
        task = dict(task)
        task["difficulty"] = "default"
        task["_compiled_spec"] = task_compile.compile_task(task)
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        outline = {"book_title": "测试书", "source": "test", "chapters": [
            {"title": "第一章", "beats": "起", "hook": "钩子"},
        ]}

        def fake_step(_run_id, role, agent, *_args, **_kwargs):
            if role.startswith("draft-c"):
                return {"ok": False, "error": "%s delayed" % agent["id"],
                        "text": "", "sid": ""}
            return {"ok": True, "error": "", "text": "", "sid": ""}

        def delayed_write(seconds):
            if seconds == 3:
                pipeline._write_chapter(str(self.workdir), 1, "山" * 400)

        with patch.object(pipeline.planner, "make_serial_outline", return_value=outline), \
                patch.object(pipeline.modelhub, "bind_agent", side_effect=lambda a, *_: a), \
                patch.object(pipeline, "_run_step", side_effect=fake_step), \
                patch.object(pipeline.time, "sleep", side_effect=delayed_write):
            pipeline._run_serial_review(
                run, task, agents, NoWaitEvent(), {}, "auto", [critic],
                primary, {"author": "初始主选", "critics": "mock 评审"},
                None, "default")

        saved = store.get_run(run["id"])
        self.assertEqual(saved["status"], "done", saved.get("error"))
        self.assertEqual(saved["route_plan"]["implement"]["selected"], "author-b")
        self.assertIn("章节起草换将", saved["route_plan"]["implement"]["selection_reason"])
        self.assertEqual(saved["route_plan"]["implement"]["participants"],
                         ["author-a", "author-b"])


class TestContentDraftCliFallback(BaseTest):
    def setUp(self):
        super().setUp()
        from app.core import pipeline
        self.pipeline = pipeline
        self.primary = {"id": "claude-code", "label": "Claude Code",
                        "kind": "claude", "mode": "real"}
        self.backup = {"id": "codex-cli", "label": "Codex CLI",
                       "kind": "codex", "mode": "real"}
        self.task = {"type": "doc", "title": "报告", "goal": "写报告",
                     "workdir": str(self.workdir)}

    def _retry(self, mode="auto", first=None, resume_ctx=None):
        from unittest.mock import patch
        p = self.pipeline
        first = first or {
            "ok": False,
            "error": "codex: stream disconnected before completion",
            "raw": {"timed_out": False, "cancelled": False},
        }
        with patch.object(p.router, "pick_switch_candidate",
                          return_value=(self.backup, "异厂商备用")) as pick, \
                patch.object(p.router, "agent_upstreams", return_value=set()), \
                patch.object(p.modelhub, "bind_agent", side_effect=lambda a, *_: a), \
                patch.object(p, "_run_step", return_value={
                    "ok": True, "text": "报告正文", "error": "", "raw": {}}) as run_step:
            result = p._retry_content_draft_with_cli(
                run_id="r-test", task=self.task, agents=[self.primary, self.backup],
                impl=self.primary, difficulty="default", mode=mode, stats={},
                prompt="写报告", workdir=str(self.workdir),
                step_wd=str(self.workdir), ev=None, resume_ctx=resume_ctx,
                draft_note="原路由", draft_res=first)
        return result, pick, run_step

    def test_auto_mode_switches_cli_after_codex_stream_disconnect(self):
        (result, impl, note), pick, run_step = self._retry()
        self.assertTrue(result["ok"])
        self.assertEqual(impl["id"], "codex-cli")
        self.assertIn("claude-code → codex-cli", note)
        self.assertEqual(run_step.call_args.args[1], "draft")
        self.assertIs(run_step.call_args.args[2], self.backup)
        pick.assert_called_once()

    def test_manual_mode_does_not_switch_cli(self):
        (result, impl, _), pick, run_step = self._retry(mode="manual")
        self.assertFalse(result["ok"])
        self.assertEqual(impl["id"], "claude-code")
        pick.assert_not_called()
        run_step.assert_not_called()

    def test_user_cancellation_does_not_switch_cli(self):
        cancelled = {"ok": False, "error": "cancelled",
                     "raw": {"timed_out": False, "cancelled": True}}
        (result, impl, _), pick, run_step = self._retry(first=cancelled)
        self.assertTrue(result["raw"]["cancelled"])
        self.assertEqual(impl["id"], "claude-code")
        pick.assert_not_called()
        run_step.assert_not_called()

    def test_resume_session_stays_with_its_original_cli(self):
        (result, impl, _), pick, run_step = self._retry(
            resume_ctx={"agent": self.primary, "session": "session-1"})
        self.assertFalse(result["ok"])
        self.assertEqual(impl["id"], "claude-code")
        pick.assert_not_called()
        run_step.assert_not_called()

    def test_review_pipeline_records_switched_author_and_excludes_self_from_critics(self):
        import json
        import threading
        from unittest.mock import patch
        from app.core import store

        primary = self.primary
        backup = self.backup
        agents = [primary, backup]
        task = dict(self.task, rubric=["accuracy"], threshold=7, rounds=1,
                    difficulty="easy", manuscript="report.md")
        run = store.create_run("orchestration", "报告", task_id="task-draft-fallback")
        store.update_run(run["id"], status="running")
        used_as_critic = []

        def critics(_agents, _ttype, _stats, impl=None):
            candidate = primary if impl is backup else backup
            return [candidate], "cross-CLI critic"

        def run_step(_run_id, role, agent, *_args, **_kwargs):
            if role == "draft" and agent is primary:
                return {"ok": False, "error": "stream disconnected", "raw": {}}
            if role == "draft":
                (self.workdir / "report.md").write_text("草稿", encoding="utf-8")
                return {"ok": True, "text": "草稿", "raw": {}}
            used_as_critic.append(agent["id"])
            return {"ok": True, "text": json.dumps({
                "scores": {"accuracy": 9}, "issues": [], "summary": "通过"}),
                "raw": {}}

        with patch.object(self.pipeline.router, "pick", return_value=(primary, "primary")), \
                patch.object(self.pipeline.router, "pick_critics", side_effect=critics), \
                patch.object(self.pipeline.router, "pick_switch_candidate",
                             return_value=(backup, "备用 CLI")), \
                patch.object(self.pipeline.router, "agent_upstreams", return_value=set()), \
                patch.object(self.pipeline.modelhub, "bind_agent",
                             side_effect=lambda agent, *_args, **_kwargs: agent), \
                patch.object(self.pipeline, "_run_step", side_effect=run_step):
            self.pipeline._run_content_review(run, task, agents, threading.Event(), {}, "auto")

        saved = store.get_run(run["id"])
        self.assertEqual(saved["status"], "done", saved.get("error"))
        self.assertEqual(saved["route_plan"]["implement"]["selected"], "codex-cli")
        self.assertEqual(saved["route_plan"]["review"]["participants"], ["claude-code"])
        self.assertIn("Codex CLI", saved["plan"]["steps"][0]["detail"])
        self.assertIn("Claude Code", saved["plan"]["steps"][1]["detail"])
        self.assertEqual(used_as_critic, ["claude-code"])


if __name__ == "__main__":
    import unittest as _u
    _u.main()
