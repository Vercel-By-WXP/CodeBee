# -*- coding: utf-8 -*-
"""Automatic routing must not launch file-writing CLIs without recovery."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from base import BaseTest


class TestNonGitExternalRouting(unittest.TestCase):
    def test_non_git_workspace_excludes_kimi_and_other_real_writers(self):
        from app.core import router

        with tempfile.TemporaryDirectory() as td:
            agents = [
                {"id": "kimi-code", "label": "Kimi Code",
                 "kind": "generic", "mode": "real"},
                {"id": "claude-code", "label": "Claude Code",
                 "kind": "claude", "mode": "real"},
                {"id": "mock-a", "label": "Mock A",
                 "kind": "mock", "mode": "mock"},
            ]
            usable, excluded, capability = router.writable_agents(agents, td)

        self.assertFalse(capability["supported"])
        self.assertEqual([a["id"] for a in excluded],
                         ["kimi-code", "claude-code"])
        self.assertEqual([a["id"] for a in usable], ["mock-a"])
        picked, _ = router.pick(usable, "implement", "code", {})
        self.assertEqual(picked["id"], "mock-a")
        plan = router.route_plan(usable, "implement", "code", {})
        self.assertEqual(plan["selected"], "mock-a")
        self.assertEqual([row["agent_id"] for row in plan["candidates"]],
                         ["mock-a"])

    def test_git_repository_keeps_real_writers_eligible(self):
        from app.core import router

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(["git", "-C", td, "init", "-q"], check=True)
            subprocess.run(["git", "-C", td, "config", "user.email",
                            "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", td, "config", "user.name",
                            "Routing Test"], check=True)
            (root / "README.md").write_text("baseline\n", encoding="utf-8")
            subprocess.run(["git", "-C", td, "add", "README.md"], check=True)
            subprocess.run(["git", "-C", td, "commit", "-qm", "baseline"],
                           check=True)
            kimi = {"id": "kimi-code", "kind": "generic", "mode": "real"}
            usable, excluded, capability = router.writable_agents([kimi], td)

        self.assertTrue(capability["supported"])
        self.assertEqual(excluded, [])
        self.assertEqual(usable, [kimi])


class TestExecuteRunNonGitRouting(BaseTest):
    def test_all_automatic_modes_exclude_external_writers(self):
        from unittest.mock import patch
        from app.core import pipeline, store

        agents = [
            {"id": "kimi-code", "label": "Kimi Code",
             "kind": "generic", "mode": "real"},
            {"id": "mock-a", "label": "Mock A",
             "kind": "mock", "mode": "mock"},
        ]
        observed = {}

        def finish_without_dispatch(run, task, _agents, _ev, _stats, mode):
            observed[mode] = [agent["id"] for agent in _agents]
            store.update_run(run["id"], expected_status="running", status="done")

        for mode in ("auto", "fast", "expert"):
            task = store.create_task({
                "type": "code", "title": "non-Git %s" % mode,
                "goal": "route safely", "workdir": str(self.workdir),
                "mode": mode,
            })
            run = store.create_run("orchestration", task["title"],
                                   task_id=task["id"])
            with patch.object(pipeline, "_agents", return_value=agents), \
                    patch.object(pipeline, "_run_code",
                                 side_effect=finish_without_dispatch):
                pipeline.execute_run(run["id"])

            saved = store.get_run(run["id"])
            self.assertEqual(saved["status"], "done", saved.get("error"))
            self.assertEqual(saved["route_plan"]["implement"]["selected"], "mock-a")
            self.assertEqual([row["agent_id"] for row in
                              saved["route_plan"]["implement"]["candidates"]],
                             ["mock-a"])
            self.assertTrue(any("Kimi Code" in warning
                                for warning in saved.get("warnings", [])))

        self.assertEqual(observed, {
            "auto": ["mock-a"], "fast": ["mock-a"], "expert": ["mock-a"]})

    def test_manual_selection_keeps_explicit_external_agent(self):
        from unittest.mock import patch
        from app.core import pipeline, store

        kimi = {"id": "kimi-code", "label": "Kimi Code",
                "kind": "generic", "mode": "real"}

        def finish_without_dispatch(run, _task, _agents, _ev, _stats, _mode):
            store.update_run(run["id"], expected_status="running", status="done")

        task = store.create_task({
            "type": "code", "title": "manual non-Git", "goal": "route explicitly",
            "workdir": str(self.workdir), "mode": "manual",
            "implementer": "kimi-code",
        })
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        with patch.object(pipeline, "_agents", return_value=[kimi]), \
                patch.object(pipeline, "_run_code", side_effect=finish_without_dispatch):
            pipeline.execute_run(run["id"])

        saved = store.get_run(run["id"])
        self.assertEqual(saved["status"], "done", saved.get("error"))
        self.assertEqual(saved["route_plan"]["implement"]["selected"], "kimi-code")
        self.assertEqual(saved.get("warnings", []), [])


if __name__ == "__main__":
    unittest.main()
