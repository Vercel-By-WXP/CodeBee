# -*- coding: utf-8 -*-
"""Compact snapshot projections used by the UI polling and SSE paths."""
from __future__ import annotations

import sys
from pathlib import Path

from base import BaseTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))


class TestStatePayload(BaseTest):
    def _direct_result(self, run):
        from unittest import mock

        from app import main

        with mock.patch.object(main.store, "run_artifacts", return_value=("", [])):
            return main.Handler._direct_result(None, run, "direct")

    def test_direct_result_uses_precise_step_time_when_run_timestamps_share_a_second(self):
        run = {
            "id": "r-duration", "status": "failed",
            "started_at": "2026-09-23 12:00:00",
            "ended_at": "2026-09-23 12:00:00",
            "steps": [
                {"status": "done", "duration_s": 25.4},
                {"status": "timeout", "duration_s": 60.8},
            ],
        }

        result = self._direct_result(run)

        self.assertEqual(result["duration_s"], 86.2)

    def test_direct_result_prefers_precise_run_duration(self):
        run = {
            "id": "r-duration", "status": "failed",
            "started_at": "2026-09-23 12:00:00",
            "ended_at": "2026-09-23 12:00:00",
            "duration_s": 86.45,
            "steps": [{"status": "timeout", "duration_s": 60.8}],
        }

        result = self._direct_result(run)

        self.assertEqual(result["duration_s"], 86.45)

    def test_direct_result_repairs_zero_duration_from_recorded_steps(self):
        run = {
            "id": "r-duration", "status": "failed",
            "started_at": "2026-09-23 12:00:00",
            "ended_at": "2026-09-23 12:00:00",
            "duration_s": 0,
            "steps": [{"status": "timeout", "duration_s": 60.8}],
        }

        result = self._direct_result(run)

        self.assertEqual(result["duration_s"], 60.8)

    def test_direct_result_keeps_timestamp_fallback_without_step_durations(self):
        run = {
            "id": "r-duration", "status": "failed",
            "started_at": "2026-09-23 12:00:00",
            "ended_at": "2026-09-23 12:00:05",
        }

        result = self._direct_result(run)

        self.assertEqual(result["duration_s"], 5)

    def test_state_snapshot_omits_run_details_and_chat_messages(self):
        from unittest import mock

        from app import main

        run = {
            "id": "r-1", "task_id": "t-1", "title": "Task", "status": "running",
            "steps": [{"n": 1, "output": "x" * 10000}],
            "messages": [{"text": "y" * 10000, "consumed": False}],
        }
        with mock.patch.object(main.registry, "effective_agents", return_value=[]), \
             mock.patch.object(main.manager, "detect_all", return_value={}), \
             mock.patch.object(main.catalog, "load", return_value=[]), \
             mock.patch.object(main.store, "state_version", return_value=1), \
             mock.patch.object(main.store, "list_runs", return_value=[run]), \
             mock.patch.object(main.store, "latest_run_by_task", return_value={"t-1": run}), \
             mock.patch.object(main.store, "list_tasks", return_value=[]), \
             mock.patch.object(main.store, "task_run_stats", return_value={}), \
             mock.patch.object(main.remote, "control_view", return_value={}), \
             mock.patch.object(main.health, "snapshot", return_value={}), \
             mock.patch.object(main.jobs, "workers_info", return_value={}):
            payload = main._state_payload()

        self.assertNotIn("steps", payload["runs"][0])
        self.assertNotIn("messages", payload["runs"][0])
        self.assertNotIn("steps", payload["task_latest"]["t-1"])
        self.assertNotIn("messages", payload["task_latest"]["t-1"])
        self.assertEqual(payload["runs"][0]["message_count"], 1)
        self.assertEqual(payload["task_latest"]["t-1"]["pending_message_count"], 1)
        self.assertLess(len(str(payload)), 2000)

    def test_run_summary_omits_detail_payload_but_keeps_list_contract(self):
        from app import main

        run = {
            "id": "r-1", "task_id": "t-1", "title": "Task", "status": "done",
            "created_at": "2026-09-23 12:00:00", "summary": "Finished",
            "steps": [{"n": 1, "status": "done", "output": "x" * 10000}],
            "messages": [{"text": "keep live inbox", "consumed": False}],
            "plan": {"large": "x" * 10000},
            "verdict": {"pass": False, "publishable": False, "review": "x" * 10000},
        }

        summary = main._state_run_summary(run)

        self.assertEqual(summary["id"], "r-1")
        self.assertEqual(summary["step_count"], 1)
        self.assertEqual(summary["status"], "done")
        self.assertNotIn("messages", summary)
        self.assertEqual(summary["verdict"], {"pass": False, "publishable": False})
        self.assertNotIn("steps", summary)
        self.assertNotIn("plan", summary)
        self.assertLess(len(str(summary)), 1000)

    def test_archived_task_summary_drops_large_prompt_fields(self):
        from app import main

        task = {
            "id": "t-1", "title": "Archived", "status": "done", "archived": True,
            "workdir": "C:/work", "engine": "direct", "git_state": "isolated",
            "created_at": "2026-09-23 12:00:00", "serial": {"chapters": 3},
            "goal": "x" * 10000, "context": "y" * 10000,
        }

        summary = main._state_task_summary(task)

        self.assertEqual(summary["id"], "t-1")
        self.assertEqual(summary["engine"], "direct")
        self.assertEqual(summary["serial"], {"chapters": 3})
        self.assertTrue(summary["archived"])
        self.assertNotIn("goal", summary)
        self.assertNotIn("context", summary)
        self.assertLess(len(str(summary)), 1000)

    def test_state_projection_substantially_reduces_large_snapshot(self):
        import json

        from app import main

        runs = [{
            "id": "r-%02d" % i, "task_id": "t-%02d" % i, "title": "Task",
            "status": "done", "steps": [{"output": "x" * 12000} for _ in range(12)],
            "messages": [{"text": "y" * 4000, "consumed": True} for _ in range(4)],
            "plan": {"text": "z" * 6000},
        } for i in range(40)]
        tasks = [{
            "id": "t-%02d" % i, "title": "Archived", "archived": True,
            "goal": "g" * 12000, "context": "c" * 12000,
        } for i in range(30)]
        full = json.dumps({"runs": runs, "task_latest": {r["task_id"]: r for r in runs},
                           "archived_tasks": tasks}, ensure_ascii=False).encode("utf-8")
        compact = json.dumps({
            "runs": [main._state_run_summary(run) for run in runs],
            "task_latest": {r["task_id"]: main._state_run_summary(r) for r in runs},
            "archived_tasks": [main._state_task_summary(task) for task in tasks],
        }, ensure_ascii=False).encode("utf-8")

        self.assertLess(len(compact), len(full) * 0.01)

    def test_task_run_details_can_be_fetched_for_archived_tasks(self):
        import json
        import threading
        from http.server import ThreadingHTTPServer
        from unittest import mock

        from app import main

        run = {"id": "r-1", "task_id": "t-1", "steps": [{"output": "full detail"}]}
        server = ThreadingHTTPServer(("127.0.0.1", 0), main.Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        with mock.patch.object(main.remote, "request_authed", return_value=True), \
             mock.patch.object(main.store, "get_task", return_value={"id": "t-1", "archived": True}), \
             mock.patch.object(main.store, "task_runs", return_value=[run]):
            with __import__("urllib.request").request.urlopen(
                    "http://127.0.0.1:%d/api/tasks/t-1/runs" % server.server_port) as response:
                payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(payload["runs"], [run])

    def test_archived_task_detail_can_be_fetched_on_demand(self):
        import json
        import threading
        from http.server import ThreadingHTTPServer
        from unittest import mock

        from app import main

        task = {"id": "t-1", "archived": True, "goal": "full goal", "context": "full context"}
        server = ThreadingHTTPServer(("127.0.0.1", 0), main.Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        with mock.patch.object(main.remote, "request_authed", return_value=True), \
             mock.patch.object(main.store, "get_task", return_value=task):
            with __import__("urllib.request").request.urlopen(
                    "http://127.0.0.1:%d/api/tasks/t-1/detail" % server.server_port) as response:
                payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(payload["task"], task)

    def test_run_messages_can_be_fetched_on_demand(self):
        import json
        import threading
        from http.server import ThreadingHTTPServer
        from unittest import mock

        from app import main

        run = {"id": "r-1", "messages": [{"text": "full message", "consumed": False}]}
        server = ThreadingHTTPServer(("127.0.0.1", 0), main.Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        with mock.patch.object(main.remote, "request_authed", return_value=True), \
             mock.patch.object(main.store, "get_run", return_value=run):
            with __import__("urllib.request").request.urlopen(
                    "http://127.0.0.1:%d/api/runs/r-1/messages" % server.server_port) as response:
                payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(payload["messages"], run["messages"])
