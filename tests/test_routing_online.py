# -*- coding: utf-8 -*-
"""在线调度指标、事件回放与代码验证顺序回归。"""
from __future__ import annotations

import json
import threading
from unittest.mock import patch

from base import BaseTest


class TestOnlineRouting(BaseTest):
    def test_auto_critics_cap_and_prefer_low_latency(self):
        from app.core import router

        agents = [
            {"id": "author", "kind": "codex", "mode": "real"},
            {"id": "slow", "kind": "opencode", "mode": "real"},
            {"id": "fast", "kind": "opencode", "mode": "real"},
            {"id": "medium", "kind": "opencode", "mode": "real"},
        ]

        def metrics(**kwargs):
            p95 = {"slow": 180.0, "fast": 8.0, "medium": 20.0}.get(
                kwargs.get("agent"), 10.0)
            return {"samples": 6, "successes": 6, "success_rate": 1.0,
                    "p95_duration_s": p95, "avg_cost_usd": 0.001}

        with patch("app.core.usage.routing_stats", side_effect=metrics):
            critics, note = router.pick_critics(
                agents, "serial_novel", {}, impl=agents[0])
        self.assertEqual([x["id"] for x in critics], ["fast", "medium"])
        self.assertIn("前 2 名", note)

    def test_usage_stats_smooths_percentiles_and_cost(self):
        from app.core import usage

        rows = ((True, 10.0, 0.0100), (True, 20.0, 0.0200),
                (True, 30.0, 0.0300), (False, 40.0, 0.0400))
        for ok, duration, cost in rows:
            usage.record(source="test", task_type="code", role="implement",
                         agent="codex", model="gpt-x", provider="p1", ok=ok,
                         duration_s=duration, cost_usd=cost,
                         usage={"total": 100})

        stats = usage.routing_stats(task_type="code", role="implement",
                                    agent="codex", model="gpt-x", provider="p1",
                                    days=0, min_samples=3)
        self.assertEqual(stats["samples"], 4)
        self.assertEqual(stats["successes"], 3)
        self.assertEqual(stats["success_rate"], 0.75)
        self.assertEqual(stats["p50_duration_s"], 25.0)
        self.assertEqual(stats["p95_duration_s"], 38.5)
        self.assertEqual(stats["avg_cost_usd"], 0.025)
        self.assertEqual(stats["fallback"], "exact")

    def test_stats_fallback_and_cache_are_directory_safe(self):
        from app.core import paths, usage

        usage.record(source="test", task_type="article", role="review",
                     agent="other", ok=True, duration_s=2, usage={"total": 1})
        usage.record(source="test", task_type="article", role="review",
                     agent="other", ok=True, duration_s=3, usage={"total": 1})
        usage.record(source="test", task_type="article", role="review",
                     agent="other", ok=False, duration_s=4, usage={"total": 1})
        stats = usage.routing_stats(task_type="article", role="review",
                                    agent="target", days=0, min_samples=3)
        self.assertEqual(stats["samples"], 3)
        self.assertEqual(stats["fallback"], "task-role")
        self.assertGreater(stats["success_rate"], 0.5)

        # 切换 DATA_DIR 后同一查询不能命中旧目录缓存。
        new_dir = self.tmp / "other-data"
        paths.DATA_DIR = new_dir
        paths.USAGE_DIR = new_dir / "usage"
        fresh = usage.routing_stats(task_type="article", role="review",
                                    agent="target", days=0, min_samples=3)
        self.assertEqual(fresh["samples"], 0)
        self.assertEqual(fresh["success_rate"], 0.75)

    def test_router_and_model_dispatch_include_online_reason(self):
        from app.core import dispatch, router

        metrics = {"samples": 4, "successes": 4, "success_rate": 1.0,
                   "p95_duration_s": 2.0, "avg_cost_usd": 0.001}
        agent = {"id": "codex", "kind": "codex", "mode": "real",
                 "_dispatch_task_type": "code"}
        with patch("app.core.usage.routing_stats", return_value=metrics):
            score, reason = router.score(agent, "implement", "code", {})
            self.assertGreater(score, 90)
            self.assertIn("在线 4/4 验收成功", reason)

            entries = [{"provider_id": "p1", "model": "m1"}]
            ranked, decisions = dispatch.rank_model_entries(
                entries, {"p1": {"id": "p1", "tier": "standard"}}, {},
                "default", "code", "implement")
        self.assertEqual(ranked, entries)
        self.assertIn("在线 4/4 验收成功", decisions[0]["reason"])

    def test_dispatch_log_is_filtered_and_does_not_store_prompt(self):
        from app.core import dispatch_log

        dispatch_log.record_event(
            run_id="run-a", task_id="task-a", task_type="code", role="implement",
            selected="codex", candidates=[{"agent_id": "codex", "label": "Codex",
                                             "reason": "能力匹配"}],
            selection_reason="推荐", phase="selected")
        dispatch_log.record_event(run_id="run-b", task_id="task-b",
                                  task_type="article", phase="completed",
                                  result="passed")
        events = dispatch_log.replay(run_id="run-a", task_type="code", limit=10)
        self.assertEqual(len(events), 1)
        raw = json.dumps(events[0], ensure_ascii=False)
        self.assertNotIn("prompt", raw.lower())
        self.assertEqual(events[0]["selected"], "codex")
        self.assertEqual(dispatch_log.replay(task_type="article")[0]["run_id"], "run-b")

        dispatch_log.record_event(
            run_id="run-secret", task_type="code",
            selection_reason="C:\\Users\\alice\\work\\x.py sk-secretvalue123")
        secret = json.dumps(dispatch_log.replay(run_id="run-secret")[0],
                            ensure_ascii=False)
        self.assertNotIn("alice", secret)
        self.assertNotIn("secretvalue", secret)

    def test_pipeline_usage_records_provider_id(self):
        from app.core import pipeline, store, usage

        task = store.create_task({"type": "code", "title": "埋点", "goal": "g",
                                  "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        agent = {"id": "codex", "label": "Codex", "kind": "codex",
                 "provider": {"id": "provider-1", "name": "Provider One"}}
        result = {"ok": True, "model": "gpt-x", "provider_id": "provider-actual",
                  "provider": {"id": "provider-actual", "name": "Actual"},
                  "raw": {"duration": 1.5},
                  "cost_usd": 0.01, "usage": {"total": 5}}
        with patch.object(usage, "record") as record:
            pipeline._record_usage(run["id"], "implement", agent, result, step=1)
        self.assertEqual(record.call_args.kwargs["provider"], "provider-actual")

    def test_cache_invalidation_is_per_dataset_not_first_key(self):
        from app.core import usage

        usage.record(task_type="code", role="implement", agent="a", ok=True,
                     usage={"total": 1})
        usage.record(task_type="code", role="implement", agent="b", ok=True,
                     usage={"total": 1})
        usage.routing_stats(task_type="code", role="implement", agent="a",
                            days=0, min_samples=1)
        first_b = usage.routing_stats(task_type="code", role="implement", agent="b",
                                      days=0, min_samples=1)
        self.assertEqual(first_b["samples"], 1)
        usage.record(task_type="code", role="implement", agent="b", ok=False,
                     usage={"total": 1})
        usage.routing_stats(task_type="code", role="implement", agent="a",
                            days=0, min_samples=1)
        fresh_b = usage.routing_stats(task_type="code", role="implement", agent="b",
                                      days=0, min_samples=1)
        self.assertEqual(fresh_b["samples"], 2)
        self.assertEqual(fresh_b["successes"], 1)

    def test_quality_result_overrides_transport_success(self):
        from app.core import usage

        usage.record(run_id="quality-run", task_type="code", role="implement",
                     agent="codex", model="m1", provider="p1", ok=True,
                     usage={"total": 1})
        self.assertEqual(usage.record_quality_for_run("quality-run", False), 1)
        stats = usage.routing_stats(task_type="code", role="implement",
                                    agent="codex", model="m1", provider="p1",
                                    days=0, min_samples=1)
        self.assertEqual(stats["quality_samples"], 1)
        self.assertEqual(stats["successes"], 0)
        self.assertEqual(stats["success_rate"], 0.6)

    def test_quality_only_rewards_successful_final_implementer(self):
        from app.core import usage

        usage.record(run_id="fallback-run", task_type="code", role="implement",
                     agent="bad", model="m1", provider="p1", ok=True,
                     usage={"total": 1})
        usage.record(run_id="fallback-run", task_type="code", role="implement-fallback",
                     agent="good", model="m2", provider="p2", ok=True,
                     usage={"total": 1})
        self.assertEqual(
            usage.record_quality_for_run("fallback-run", True, agent="good"), 2)
        rows = usage._iter_quality_records(2)
        quality = {row["agent"]: row["quality_ok"] for row in rows}
        self.assertEqual(quality, {"bad": False, "good": True})
        bad = usage.routing_stats(task_type="code", role="implement",
                                  agent="bad", model="m1", provider="p1",
                                  days=2, min_samples=1)
        self.assertEqual(bad["quality_samples"], 1)
        self.assertEqual(bad["successes"], 0)

    def test_builtin_route_id_maps_to_builtin_usage_identity(self):
        from app.core import pipeline, store, usage

        task = store.create_task({"type": "direct", "title": "内置直连", "goal": "g",
                                  "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])

        def finish(_run, _task, *_args, **_kwargs):
            usage.record(run_id=run["id"], task_type="direct", role="direct",
                         agent="builtin", model="m", provider="p", ok=True,
                         usage={"total": 1})
            store.update_run(
                run["id"], status="done", verdict={"pass": True},
                route_plan={"implement": {"selected": "builtin:p:m"}})

        with patch.object(pipeline, "_agents", return_value=[]), \
             patch.object(pipeline, "_write_task_spec", return_value=""), \
             patch.object(pipeline, "_run_direct", side_effect=finish), \
             patch.object(pipeline.dispatch_log, "record_event"), \
             patch.object(pipeline.skills, "learn_async"), \
             patch.object(pipeline.knowledge, "learn_async"):
            pipeline.execute_run(run["id"])
        rows = usage._iter_quality_records(2)
        self.assertEqual([(row["agent"], row["quality_ok"]) for row in rows],
                         [("builtin", True)])

    def test_runner_reports_actual_fallback_provider(self):
        from app.core import runner

        agent = {"id": "generic", "kind": "generic", "mode": "real",
                 "command": "echo", "argv_template": ["{prompt}"],
                 "call_chain": [
                     {"model": "m1", "provider_id": "p1",
                      "provider": {"id": "p1"}, "env": {}},
                     {"model": "m2", "provider_id": "p2",
                      "provider": {"id": "p2"}, "env": {}},
                 ]}
        failed = {"ok": False, "exit_code": 1, "stdout": "",
                  "stderr": "429 rate limit", "timed_out": False,
                  "cancelled": False, "duration": 0.1}
        passed = {"ok": True, "exit_code": 0, "stdout": "done",
                  "stderr": "", "timed_out": False,
                  "cancelled": False, "duration": 0.2}
        with patch.object(runner, "run_process", side_effect=[failed, passed]):
            result = runner.run_agent(agent, "work", workdir=str(self.workdir))
        self.assertTrue(result["ok"])
        self.assertEqual(result["provider_id"], "p2")
        self.assertEqual(result["model"], "m2")

    def test_direct_engine_writes_completed_event(self):
        from app.core import pipeline, store, usage

        task = store.create_task({"type": "direct", "title": "直达", "goal": "g",
                                  "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])

        def finish(_run, _task, *_args, **_kwargs):
            store.update_run(run["id"], status="done",
                             verdict={"engine": "direct", "pass": True})

        events = []
        with patch.object(pipeline, "_agents", return_value=[]), \
             patch.object(pipeline, "_write_task_spec", return_value=""), \
             patch.object(pipeline, "_run_direct", side_effect=finish), \
             patch.object(pipeline.dispatch_log, "record_event",
                          side_effect=lambda **kw: events.append(kw)), \
             patch.object(usage, "record_quality_for_run", return_value=0), \
             patch.object(pipeline.skills, "learn_async"), \
             patch.object(pipeline.knowledge, "learn_async"):
            pipeline.execute_run(run["id"])
        completed = [event for event in events if event.get("phase") == "completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["result"], "passed")

    def test_cancelled_run_writes_event_without_quality_reward(self):
        from app.core import pipeline, store, usage

        task = store.create_task({"type": "direct", "title": "取消", "goal": "g",
                                  "workdir": str(self.workdir)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])

        def cancel(_run, _task, *_args, **_kwargs):
            store.update_run(run["id"], status="cancelled")

        events = []
        with patch.object(pipeline, "_agents", return_value=[]), \
             patch.object(pipeline, "_write_task_spec", return_value=""), \
             patch.object(pipeline, "_run_direct", side_effect=cancel), \
             patch.object(pipeline.dispatch_log, "record_event",
                          side_effect=lambda **kw: events.append(kw)), \
             patch.object(usage, "record_quality_for_run") as quality, \
             patch.object(pipeline.skills, "learn_async"), \
             patch.object(pipeline.knowledge, "learn_async"):
            pipeline.execute_run(run["id"])
        completed = [event for event in events if event.get("phase") == "completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["result"], "cancelled")
        quality.assert_not_called()


class TestCodeVerificationOrder(BaseTest):
    def test_verify_runs_before_review(self):
        from app.core import pipeline, store, task_compile

        task = store.create_task({"type": "code", "title": "顺序", "goal": "g",
                                  "workdir": str(self.workdir), "verify_command": "exit 0"})
        task = dict(task, difficulty="default", engine="code")
        task["_compiled_spec"] = task_compile.compile_task(task)
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        agent = {"id": "mock-a", "label": "Mock", "kind": "mock", "mode": "mock"}
        calls = []

        def verify(*_args, **_kwargs):
            calls.append("verify")
            return True, True

        def review(*_args, **_kwargs):
            calls.append("review")
            return {"pass": True, "scores": {"正确性": 9}, "issues": []}

        with patch.object(pipeline.planner, "make_code_plan",
                          return_value={"source": "test", "steps": [{"title": "实现", "detail": "实现"}]}) as make_plan, \
                patch.object(pipeline.modelhub, "bind_agent", side_effect=lambda a, *_: a), \
                patch.object(pipeline, "_run_step", return_value={"ok": True, "text": "", "raw": {}}), \
                patch.object(pipeline, "_run_verify", side_effect=verify), \
                patch.object(pipeline, "_run_review", side_effect=review):
            pipeline._run_code(run, task, [agent], threading.Event(), {}, "expert")
        make_plan.assert_called_once()
        self.assertEqual(calls[:2], ["verify", "review"])
