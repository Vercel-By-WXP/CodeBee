# -*- coding: utf-8 -*-
"""竞品借鉴落地：检查点、能力清单、记忆预览和外部评审契约。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import checkpoints, project_memory, registry, review_requests


class AgentOperatingContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_checkpoint_carries_tool_call_and_side_effect_boundary(self):
        with patch.object(checkpoints, "_DIR", self.root / "checkpoints"):
            started = checkpoints.start(
                "run-contract", 1, "implement", "prompt",
                tool_call={"name": "write_file", "args": {"path": "a.txt"}},
                conversation_ref="session-1",
                side_effects=["workspace_files"],
            )
            checkpoints.finish(
                "run-contract", 1, "done",
                metadata={"repropose_required": True,
                          "side_effects": ["workspace_files", "external_cli"]},
            )
            view = checkpoints.replay_preview("run-contract")
        self.assertEqual(started["conversation_ref"], "session-1")
        self.assertEqual(len(started["tool_call_digest"]), 64)
        self.assertNotIn("write_file", started["tool_call_digest"])
        self.assertTrue(view["steps"][0]["repropose_required"])
        self.assertIn("external_cli", view["steps"][0]["side_effects"])

    def test_capability_manifest_is_explicit_and_safe(self):
        manifest = registry.capability_manifest({
            "id": "gemini", "name": "Gemini CLI",
            "orch": {"kind": "generic", "command": "gemini",
                      "argv_template": ["-p", "{prompt}"]},
        })
        self.assertEqual(manifest["transport"], "cli")
        self.assertFalse(manifest["resume"])
        self.assertEqual(manifest["sandbox"], "task-policy")
        self.assertEqual(manifest["approval"], "task-contract")
        self.assertIn("windows", manifest["platforms"])

    def test_memory_injection_preview_exposes_sources_without_extra_content(self):
        row = project_memory.propose(
            self.root, source_run="run-memory", title="Build rule",
            facts=["Keep changes small"], now=1000)
        project_memory.decide(self.root, row["id"], "approve", now=1001)
        preview = project_memory.injection_preview(self.root, query="changes", cap=2000, now=1002)
        self.assertIn("Keep changes small", preview["text"])
        self.assertEqual(preview["entries"][0]["source_run"], "run-memory")
        self.assertEqual(preview["entries"][0]["version"], 2)
        self.assertEqual(preview["budget"]["cap"], 2000)
        bounded = project_memory.injection_preview(
            self.root, query="changes", cap=10 ** 9, now=1002)
        self.assertEqual(bounded["budget"]["cap"], 20000)

    def test_review_request_routes_and_transitions_idempotently(self):
        with patch.object(review_requests, "_FILE", self.root / "reviews.jsonl"):
            channel = review_requests.route_channel(
                ["ui/app.js"],
                [{"paths": ["ui/*"], "channel": "#frontend"}],
                "#engineering",
            )
            row = review_requests.create(
                "run-1", summary="Review diff", paths=["ui/app.js"], channel=channel)
            assigned = review_requests.transition(row["id"], "assign", reviewer="alice")
            approved = review_requests.transition(row["id"], "approve", reviewer="alice")
            again = review_requests.transition(row["id"], "approve", reviewer="alice")
            current = review_requests.get(row["id"])
        self.assertEqual(channel, "#frontend")
        self.assertEqual(assigned["status"], "assigned")
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(again["version"], approved["version"])
        self.assertEqual(current["reviewer"], "alice")

    def test_review_request_validates_run_id_and_normalizes_string_paths(self):
        with patch.object(review_requests, "_FILE", self.root / "reviews.jsonl"):
            with self.assertRaises(ValueError):
                review_requests.create("", paths="ui/app.js")
            row = review_requests.create("run-2", paths="ui/app.js")
            self.assertEqual(row["paths"], ["ui/app.js"])
            self.assertEqual(review_requests.list_requests()[0]["id"], row["id"])

    def test_openapi_declares_new_operating_contract_routes(self):
        from core import openapi
        paths = openapi.document()["paths"]
        self.assertIn("/api/reviews", paths)
        self.assertIn("/api/reviews/{review_id}/transition", paths)
        self.assertIn("/api/capabilities", paths)
        self.assertIn("/api/tasks/{task_id}/memory/preview", paths)


if __name__ == "__main__":
    unittest.main()
