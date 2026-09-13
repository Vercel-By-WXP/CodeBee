# -*- coding: utf-8 -*-
"""任务流程（类型）注册表 + 运行设置 + 并发执行测试。

覆盖：内置 6 类、自定义流程 CRUD（内置不可改删）、create_task 固化流程参数、
自定义 review 流程跑通 mock 全流程、并发 worker 池多任务互不打扰、缩容。
"""
from __future__ import annotations

import json
import threading
import time

from base import BaseTest


class TestFlows(BaseTest):
    def test_flows_crud_and_task(self):
        from app.core import flows, store
        flows._FILE = self.data_dir / "flows.json"

        # 1) 内置流程：code + 多个 review 类，字段完整
        ids = [f["id"] for f in flows.list_flows()]
        for expect in ("code", "novel", "doc", "translation", "research", "speech"):
            self.assertIn(expect, ids)
        novel = flows.get_flow("novel")
        self.assertEqual(novel["engine"], "review")
        self.assertIn("情节", novel["rubric"])

        # 2) 自定义流程 upsert：合法创建 / 校验拒绝
        flow, err = flows.upsert_flow({
            "id": "podcast", "name": "播客脚本", "icon": "🎙", "engine": "review",
            "manuscript": "script.md",
            "rubric": ["选题", "结构", "口语化", "钩子"],
            "threshold": 7.5, "rounds": 3,
            "note": "单集脚本产出",
        })
        self.assertIsNone(err, err)
        self.assertFalse(flow["builtin"])
        self.assertEqual(flows.get_flow("podcast")["threshold"], 7.5)
        self.assertTrue(flows.upsert_flow({"id": "x!", "name": "坏 id", "engine": "review",
                                           "rubric": ["a"]})[1])
        # 自定义 id 再次 upsert = 原地更新（合法）
        upd, err = flows.upsert_flow({"id": "podcast", "name": "播客脚本V2", "engine": "review",
                                      "rubric": ["选题", "结构"]})
        self.assertIsNone(err, err)
        self.assertEqual(upd["name"], "播客脚本V2")
        # 预置流程现在可编辑（改后带 edited 标记），可一键恢复默认；不可删除
        edited, err = flows.upsert_flow({"id": "novel", "name": "小说", "engine": "review",
                                         "threshold": 8.5, "rubric": ["情节", "人物"]})
        self.assertIsNone(err, err)
        self.assertEqual(edited["threshold"], 8.5)
        self.assertTrue(edited["edited"])
        self.assertEqual(flows.get_flow("novel")["rubric"], ["情节", "人物"])
        self.assertTrue(flows.delete_flow("novel"))          # 预置流程不可删除
        self.assertIsNone(flows.reset_flow("novel"))          # 恢复默认
        back = flows.get_flow("novel")
        self.assertEqual(back["threshold"], 7.0)
        self.assertEqual(back["rubric"], ["情节", "人物", "文笔", "节奏", "吸引力"])
        self.assertFalse(back.get("edited"))
        self.assertIsNone(flows.delete_flow("podcast"))      # 自定义可删
        self.assertIsNone(flows.get_flow("podcast"))

        # 3) create_task：未知类型拒绝；自定义 flow 参数固化到任务
        flow, _ = flows.upsert_flow({
            "id": "podcast", "name": "播客脚本", "engine": "review",
            "manuscript": "script.md", "rubric": ["选题", "结构"],
            "threshold": 7.5, "rounds": 3})
        t = store.create_task({"type": "podcast", "goal": "做一期关于并发的播客脚本",
                               "workdir": str(self.workdir)})
        self.assertEqual(t["engine"], "review")
        self.assertEqual(t["manuscript"], "script.md")
        self.assertEqual(t["rubric"], ["选题", "结构"])
        self.assertEqual(t["threshold"], 7.5)
        self.assertEqual(t["rounds"], 3)
        try:
            store.create_task({"type": "nope", "goal": "g", "workdir": str(self.workdir)})
            self.fail("未知类型应被拒绝")
        except ValueError:
            pass
        # 任务级覆盖优先于 flow 默认
        t2 = store.create_task({"type": "podcast", "goal": "g", "workdir": str(self.workdir),
                                "threshold": 9.0, "manuscript": "override.md"})
        self.assertEqual(t2["threshold"], 9.0)
        self.assertEqual(t2["manuscript"], "override.md")

    def test_builtin_code_task_still_works(self):
        from app.core import store
        t = store.create_task({"type": "code", "goal": "g", "workdir": str(self.workdir),
                               "verify_command": "exit 0"})
        self.assertEqual(t["engine"], "code")
        self.assertEqual(t["verify_command"], "exit 0")


class TestCustomFlowPipeline(BaseTest):
    """自定义 review 流程端到端（mock 智能体）：起草→评审→门禁，产出落盘。"""

    def runTest(self):
        from app.core import flows, pipeline, store
        flows._FILE = self.data_dir / "flows.json"
        flow, err = flows.upsert_flow({
            "id": "podcast", "name": "播客脚本", "engine": "review",
            "manuscript": "script.md", "rubric": ["选题", "结构"],
            "threshold": 6.0, "rounds": 2})
        self.assertIsNone(err, err)
        pipeline._agents = self.mock_agents
        task = store.create_task({"type": "podcast", "title": "并发主题",
                                  "goal": "聊多线程", "workdir": str(self.workdir),
                                  "mode": "auto"})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline.execute_run(run["id"])
        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        v = run["verdict"]
        self.assertEqual(v["engine"], "review")
        self.assertEqual(v["type"], "podcast")
        self.assertTrue((self.workdir / "script.md").is_file())
        roles = [s["role"] for s in run["steps"]]
        self.assertTrue(any(r.startswith("draft") for r in roles))
        self.assertTrue(any(r.startswith("critique") for r in roles))


class TestSettingsAndConcurrency(BaseTest):
    def runTest(self):
        from app.core import jobs, settings
        settings._FILE = self.data_dir / "settings.json"

        # 1) 设置读写与边界校验
        self.assertEqual(settings.load()["max_concurrent_jobs"], 3)
        view, err = settings.save({"max_concurrent_jobs": 5})
        self.assertIsNone(err)
        self.assertEqual(view["max_concurrent_jobs"], 5)
        _, err = settings.save({"max_concurrent_jobs": 99})
        self.assertTrue(err)
        _, err = settings.save({"max_concurrent_jobs": "abc"})
        self.assertTrue(err)

        # 2) 并发执行：3 个任务同时跑、互不打扰（用事件栅栏验证重叠）
        jobs.configure(3)
        jobs.start_worker()
        from app.core import pipeline
        lock = threading.Lock()
        state = {"in_flight": 0, "peak": 0, "ran": []}
        orig = pipeline.execute_run

        def fake_execute(run_id):
            with lock:
                state["in_flight"] += 1
                state["peak"] = max(state["peak"], state["in_flight"])
                state["ran"].append(run_id)
            time.sleep(0.6)   # 模拟一次真实编排调用
            with lock:
                state["in_flight"] -= 1

        pipeline.execute_run = fake_execute
        try:
            for i in range(3):
                jobs.enqueue({"kind": "orchestration", "run_id": "r-conc-%d" % i})
            deadline = time.time() + 15
            while time.time() < deadline:
                with lock:
                    if len(state["ran"]) == 3 and state["in_flight"] == 0:
                        break
                time.sleep(0.1)
        finally:
            pipeline.execute_run = orig
        self.assertEqual(len(state["ran"]), 3)
        self.assertEqual(state["peak"], 3, "3 个任务必须同时在跑（峰值并发=3）")

        # 3) run 之间互不干扰：CANCELS 按 run_id 隔离
        ev1 = jobs.cancel_event_for("r-x")
        ev2 = jobs.cancel_event_for("r-y")
        ev1.set()
        self.assertFalse(ev2.is_set())

        # 4) 缩容：目标并发调小后，空闲的多余线程在检查点退出
        jobs.configure(1)
        deadline = time.time() + 12   # worker 空转检查点 5s 一次
        while time.time() < deadline:
            if jobs.workers_info()["alive"] <= 1:
                break
            time.sleep(0.3)
        self.assertLessEqual(jobs.workers_info()["alive"], 1)
        jobs.configure(3)   # 恢复，避免影响其他测试


class TestSerialPipeline(BaseTest):
    """连载模式端到端（mock）：大纲 → 逐章起草/评审/修订 → 全局评审 → 合并成书。"""

    def test_serial_e2e(self):
        from app.core import pipeline, store
        pipeline._agents = self.mock_agents
        task = store.create_task({
            "type": "serial_novel", "title": "连载测试", "mode": "auto",
            "goal": "写一部短篇连载", "workdir": str(self.workdir),
            "serial": {"chapters": 3, "words_per_chapter": 1200},
            "threshold": 7.0,
        })
        self.assertEqual(task["serial"], {"chapters": 3, "words_per_chapter": 1200})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        pipeline.execute_run(run["id"])
        run = store.get_run(run["id"])
        self.assertEqual(run["status"], "done", run.get("error"))
        v = run["verdict"]
        self.assertTrue(v["serial"])
        self.assertEqual(v["chapters_used"], 3)
        self.assertTrue(v["publishable"], v)
        self.assertTrue(v["global_pass"])
        # 三章文件 + 合并稿件都落盘
        for i in (1, 2, 3):
            self.assertTrue((self.workdir / ("chapter-%02d.md" % i)).is_file())
        ms = (self.workdir / "manuscript.md")
        self.assertTrue(ms.is_file())
        self.assertIn("# ", ms.read_text(encoding="utf-8"))
        # 步骤覆盖：outline + 每章 draft/critique + global + merge
        roles = [s["role"] for s in run["steps"]]
        self.assertEqual(roles[0], "outline")
        for i in (1, 2, 3):
            self.assertIn("draft-c%d" % i, roles)
            self.assertIn("critique-c%d" % i, roles)
        self.assertIn("global-critique", roles)
        self.assertIn("merge", roles)
        # mock 确定性：第 1 轮评审不达标触发修订（至少一章走了 revise）
        self.assertTrue(any(r.startswith("revise-c") for r in roles), roles)
        # 报告包含各章得分表
        report = (run["id"] and (self._paths.RUNS_DIR / run["id"] / "report.md").read_text(encoding="utf-8"))
        self.assertIn("各章得分", report)

    def test_serial_validation_bounds(self):
        from app.core import store
        t = store.create_task({"type": "serial_novel", "goal": "g", "workdir": str(self.workdir),
                               "serial": {"chapters": 99, "words_per_chapter": 100}})
        self.assertEqual(t["serial"]["chapters"], 20)      # 上限钳制
        self.assertEqual(t["serial"]["words_per_chapter"], 500)


class TestSerialResume(BaseTest):
    """连载断点续跑：retry 继承上一遍大纲与已完成章，只补写缺失章。"""

    def runTest(self):
        from app.core import pipeline, store
        pipeline._agents = self.mock_agents
        task = store.create_task({
            "type": "serial_novel", "title": "续跑测试", "mode": "auto",
            "goal": "短篇连载", "workdir": str(self.workdir),
            "serial": {"chapters": 3, "words_per_chapter": 800},
            "threshold": 6.0,
        })
        # 模拟一次中断的运行：大纲 + 前两章已起草完成
        run1 = store.create_run("orchestration", task["title"], task_id=task["id"])
        outline = {"book_title": "续跑书", "chapters": [
            {"title": "一", "beats": "b1", "hook": ""},
            {"title": "二", "beats": "b2", "hook": ""},
            {"title": "三", "beats": "b3", "hook": ""}],
            "source": "template"}
        store.update_run(run1["id"], outline=outline)
        for i in (1, 2):
            step, _ = store.add_step(run1["id"], "draft-c%d" % i, "mock-a", "mock")
            store.finish_step(run1["id"], step["n"], "done", summary="ok")
            with (self.workdir / ("chapter-%02d.md" % i)).open("w", encoding="utf-8") as f:
                f.write("第 %d 章旧稿内容" % i)
        store.update_run(run1["id"], status="failed", error="中断", ended_at="x")

        ok, err, run2 = store.retry_task(task["id"])
        self.assertTrue(ok, err)
        self.assertEqual(run2["inherit"]["done_chapters"], [1, 2])
        self.assertEqual(run2["inherit"]["outline"]["book_title"], "续跑书")
        pipeline.execute_run(run2["id"])
        run2 = store.get_run(run2["id"])
        self.assertEqual(run2["status"], "done", run2.get("error"))
        v = run2["verdict"]
        self.assertEqual(v["chapters_used"], 3)
        self.assertTrue(v["publishable"], v)
        roles = [(s["role"], s.get("note") or "") for s in run2["steps"]]
        d1 = [n for n, note in roles if n == "draft-c1"]
        self.assertTrue(d1, roles)
        # 前两章的起草步骤应标注断点续跑（复用旧稿），且全部三份稿件在盘
        self.assertEqual(len(d1), 1)
        resumed = [s for s in run2["steps"]
                   if s["role"] == "draft-c1" and "断点续跑" in (s.get("summary") or "")]
        self.assertTrue(resumed, [s["role"] + ":" + (s.get("summary") or "") for s in run2["steps"]])
        for i in (1, 2, 3):
            self.assertTrue((self.workdir / ("chapter-%02d.md" % i)).is_file())
        # 第 3 章是新起草的（mock 内容与旧稿不同——这里只验证步骤数：c3 起草 1 次无续跑标注）
        c3 = [s for s in run2["steps"] if s["role"] == "draft-c3"]
        self.assertEqual(len(c3), 1)
        self.assertNotIn("断点续跑", c3[0].get("summary") or "")


class TestAutopilotPolish(BaseTest):
    """自驱打磨：全局评审不过 → 自动重改最弱章并重评（无人干预）。

    用确定性 mock 评审：第 1 轮全书「节奏」故意低于阈值，打磨后达标——
    验证系统会自己定位弱章、重写、重评，而不是把问题丢给用户。
    """

    def runTest(self):
        from app.core import pipeline, skills, store
        skills._FILE = self.data_dir / "skills.json"
        calls = {"global": 0}

        # 桩：mock 评审固定给分；全局评审第 1 次说节奏 6.0（不过），第 2 次 8.0
        import app.core.mocks as mocks

        def fake_critique(agent_id, round_no, dims, threshold):
            return {"scores": {d: 8.5 for d in dims}, "issues": [], "summary": "mock 达标"}

        def fake_global(run_id, role, agent, prompt, workdir, readonly, ev, timeout=None, **kw):
            calls["global"] += 1
            score = 6.0 if calls["global"] == 1 else 8.5
            step, log_abs = store.add_step(run_id, role, agent["id"], agent.get("label"))
            store.finish_step(run_id, step["n"], "done", summary="mock 全局评审")
            return {"ok": True, "text": json.dumps({"scores": {"节奏": score, "情节": 8.5},
                                                    "issues": [], "summary": "mock"}),
                    "cost_usd": 0, "tokens": 0, "error": "", "raw": {"exit_code": 0}}

        # 全局评审在连载里走 _run_step；这里直接给 critics 换成"真实"智能体并桩掉 _run_step
        orig_step = pipeline._run_step
        orig_crit = mocks.critique

        def fake_step(run_id, role, agent, prompt, workdir, readonly, ev, timeout=None, note="", resume=None):
            """全部桩化（单测不得真跑 CLI）：全局评审按次数给分，其余给达标分。"""
            if role == "global-critique":
                return fake_global(run_id, role, agent, prompt, workdir, readonly, ev, timeout)
            step, _log = store.add_step(run_id, role, agent["id"], agent.get("label"))
            store.finish_step(run_id, step["n"], "done", summary="桩评审")
            return {"ok": True, "cost_usd": 0, "tokens": 0, "error": "",
                    "text": json.dumps({"scores": {d: 8.5 for d in ("情节", "人物", "文笔", "节奏", "吸引力")},
                                        "issues": [], "summary": "桩评审达标"}),
                    "raw": {"exit_code": 0}}

        mocks.critique = fake_critique
        pipeline._run_step = fake_step
        # 作者用 mock（草稿走 mock 分支），评审用一个"真实"智能体 → 评审/全局都经 _run_step，
        # 由 fake_step 接管打分（模拟"第 1 次全局不过、打磨后通过"）
        fake_real = {"id": "fake-reviewer", "label": "桩评审", "kind": "claude",
                     "mode": "real", "command": "claude", "env": {}}
        pipeline._agents = lambda: self.mock_agents() + [dict(fake_real)]
        try:
            task = store.create_task({
                "type": "serial_novel", "title": "自驱打磨", "mode": "manual",
                "implementer": "mock-a", "critics": ["fake-reviewer"],
                "goal": "短篇", "workdir": str(self.workdir),
                "serial": {"chapters": 2, "words_per_chapter": 600},
                "threshold": 7.0})
            run = store.create_run("orchestration", task["title"], task_id=task["id"])
            pipeline.execute_run(run["id"])
            run = store.get_run(run["id"])
        finally:
            mocks.critique = orig_crit
            pipeline._run_step = orig_step

        self.assertEqual(run["status"], "done", run.get("error"))
        v = run["verdict"]
        # 第 1 次全局不过 → 自动打磨 → 第 2 次通过
        self.assertEqual(calls["global"], 2, "全局评审应被调用两次（打磨前/后）")
        self.assertTrue(v["global_pass"], v.get("global_scores"))
        self.assertTrue(v["publishable"], v)
        roles = [s["role"] for s in run["steps"]]
        self.assertIn("polish-r1", roles, roles)
        polished = [c for c in v["chapter_scores"] if c.get("polished")]
        self.assertTrue(polished, v["chapter_scores"])


if __name__ == "__main__":
    import unittest as _u
    _u.main()
