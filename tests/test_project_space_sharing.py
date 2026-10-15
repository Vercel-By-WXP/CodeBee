# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class ProjectSpaceSharingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        from app.core import paths, store, knowledge, project_memory, project_permissions
        self.paths_patch = patch.object(paths, "DATA_DIR", self.root / "data")
        self.paths_patch.start()
        self.store_patch = patch.object(paths, "TASKS_DIR", self.root / "tasks")
        self.store_patch.start()
        self.runs_patch = patch.object(paths, "RUNS_DIR", self.root / "runs")
        self.runs_patch.start()
        self.knowledge_patch = patch.object(knowledge, "_FILE", self.root / "knowledge.json")
        self.knowledge_patch.start()
        self.permission_patch = patch.object(project_permissions, "_FILE", self.root / "permissions.json")
        self.permission_patch.start()
        self.addCleanup(self.permission_patch.stop)
        self.addCleanup(self.knowledge_patch.stop)
        self.addCleanup(self.runs_patch.stop)
        self.addCleanup(self.store_patch.stop)
        self.addCleanup(self.paths_patch.stop)
        self.addCleanup(self.tmp.cleanup)
        store._TASKS.clear()
        store._RUNS.clear()

    def test_project_snapshot_and_share_code_round_trip(self):
        from app.core import project_space, sharing, store
        task = store.create_task({"type": "research", "expert_id": "research",
                                  "goal": "分析项目市场", "workdir": str(self.root)})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        project = project_space.snapshot()[0]
        self.assertEqual(project["counts"]["tasks"], 1)
        self.assertEqual(project["counts"]["runs"], 1)
        exported = sharing.export_project(project["id"])
        decoded = sharing.decode(exported["code"])
        self.assertEqual(decoded["kind"], "project")
        self.assertEqual(decoded["payload"]["project"]["id"], project["id"])
        tampered = exported["code"][:-1] + ("A" if exported["code"][-1] != "A" else "B")
        with self.assertRaises(ValueError):
            sharing.decode(tampered)

    def test_shared_expert_import_and_knowledge_import_are_scoped(self):
        from app.core import experts, flows, knowledge, sharing
        expert = {"id": "shared_demo", "name": "共享示例", "version": "1.0",
                  "flow_id": flows.list_flows()[0]["id"], "prompt": "按证据交付",
                  "skills": [], "knowledge_scope": ["项目"], "deliverables": ["结果"]}
        code = sharing.encode(sharing._package("expert", {"expert": expert}))
        result = sharing.import_package(code, apply=True)
        self.assertEqual(result["expert"]["id"], "shared_demo")
        self.assertEqual(experts.get_expert("shared_demo")["name"], "共享示例")
        knowledge_code = sharing.export_knowledge()["code"]
        self.assertEqual(sharing.import_package(knowledge_code, apply=True)["kind"], "knowledge")

    def test_project_import_rehydrates_memory_without_file_writes(self):
        from app.core import sharing, knowledge
        package = sharing._package("project", {"project": {"id": "p"},
            "memory": [{"title": "共享事实", "content": "可复用结论"}]})
        result = sharing.import_package(sharing.encode(package), apply=True)
        self.assertEqual(result["imported_memory"], 1)
        self.assertEqual(knowledge.list_entries()[0]["title"], "共享事实")

    def test_permissions_and_cloud_gate(self):
        from app.core import cloud_readiness, project_permissions
        self.assertEqual(project_permissions.role("p", "local"), "owner")
        project_permissions.set_role("p", "alice", "editor")
        self.assertTrue(project_permissions.can("p", "alice", "viewer"))
        self.assertFalse(project_permissions.can("p", "alice", "owner"))
        with patch("app.core.phone_relay.view", return_value={"enabled": False, "connected": False}):
            view = cloud_readiness.snapshot()
        self.assertFalse(view["checks"]["cloud_executor"])
        self.assertIn("云端", view["policy"])


if __name__ == "__main__":
    unittest.main()
