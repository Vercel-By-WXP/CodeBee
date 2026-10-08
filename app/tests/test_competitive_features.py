import hashlib
import hmac
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from core import agent_context, checkpoints, eval_matrix, flow_graph
from core import knowledge_pipeline, policy, project_memory, retrieval, tracing, webhooks


class CompetitiveFeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_agent_context_discovers_nearest_files_and_scrubs_secrets(self):
        parent = self.root / "repo"
        workdir = parent / "packages" / "demo"
        workdir.mkdir(parents=True)
        (parent / "AGENTS.md").write_text(
            "# repo\nUse API key sk-1234567890abcdef and keep tests green.\n",
            encoding="utf-8")
        (parent / ".git").mkdir()
        (workdir / "AGENTS.md").write_text("# demo\nPrefer small changes.\n", encoding="utf-8")

        result = agent_context.discover(workdir, boundary=parent)

        self.assertEqual(result["files"][0]["path"], str(workdir / "AGENTS.md"))
        self.assertEqual(result["files"][1]["path"], str(parent / "AGENTS.md"))
        self.assertNotIn("sk-1234567890abcdef", result["prompt"])
        self.assertEqual(result["content_sha256"],
                         hashlib.sha256(result["prompt"].encode("utf-8")).hexdigest())
        self.assertIn("Prefer small changes", result["prompt"])

    def test_agent_context_does_not_trust_guidance_above_repository_boundary(self):
        outside = self.root / "outside"
        repo = outside / "repo"
        workdir = repo / "pkg"
        workdir.mkdir(parents=True)
        (outside / "AGENTS.md").write_text("outside instruction", encoding="utf-8")
        (repo / "AGENTS.md").write_text("repo instruction", encoding="utf-8")
        (repo / ".git").mkdir()
        result = agent_context.discover(workdir)
        self.assertEqual([x["scope"] for x in result["files"]], [str(repo)])

    def test_checkpoint_snapshots_restore_only_captured_file(self):
        with patch.object(checkpoints, "_DIR", self.root / "checkpoints"):
            workdir = self.root / "work"
            workdir.mkdir()
            target = workdir / "note.txt"
            target.write_text("before", encoding="utf-8")
            checkpoints.capture_file("run-snapshot", workdir, target)
            target.write_text("after", encoding="utf-8")
            checkpoints.mark_file_after("run-snapshot", workdir, target)
            preview = checkpoints.snapshot_preview("run-snapshot", workdir)
            self.assertEqual(preview["files"][0]["status"], "changed")
            restored = checkpoints.restore_files("run-snapshot", workdir)
            self.assertEqual(target.read_text(encoding="utf-8"), "before")
            self.assertEqual(restored["restored"], 1)

    def test_checkpoint_restore_refuses_to_overwrite_external_edits(self):
        with patch.object(checkpoints, "_DIR", self.root / "checkpoints"):
            workdir = self.root / "work"
            workdir.mkdir()
            target = workdir / "note.txt"
            target.write_text("before", encoding="utf-8")
            checkpoints.capture_file("run-conflict", workdir, target)
            target.write_text("agent edit", encoding="utf-8")
            checkpoints.mark_file_after("run-conflict", workdir, target)
            target.write_text("human edit", encoding="utf-8")
            restored = checkpoints.restore_files("run-conflict", workdir)
            self.assertEqual(restored["restored"], 0)
            self.assertEqual(restored["conflicts"], ["note.txt"])
            self.assertEqual(target.read_text(encoding="utf-8"), "human edit")

    def test_checkpoint_restore_rechecks_conflicts_after_preview(self):
        with patch.object(checkpoints, "_DIR", self.root / "checkpoints"):
            workdir = self.root / "work"
            workdir.mkdir()
            target = workdir / "note.txt"
            target.write_text("before", encoding="utf-8")
            checkpoints.capture_file("run-race", workdir, target)
            target.write_text("agent edit", encoding="utf-8")
            checkpoints.mark_file_after("run-race", workdir, target)
            original_preview = checkpoints.snapshot_preview

            def preview_then_user_edit(run_id, root):
                result = original_preview(run_id, root)
                target.write_text("concurrent user edit", encoding="utf-8")
                return result

            with patch.object(checkpoints, "snapshot_preview", side_effect=preview_then_user_edit):
                restored = checkpoints.restore_files("run-race", workdir)
            self.assertEqual(restored["restored"], 0)
            self.assertEqual(restored["conflicts"], ["note.txt"])
            self.assertEqual(target.read_text(encoding="utf-8"), "concurrent user edit")

    def test_checkpoint_restores_agent_created_file(self):
        with patch.object(checkpoints, "_DIR", self.root / "checkpoints"):
            workdir = self.root / "work"
            workdir.mkdir()
            target = workdir / "new.txt"
            checkpoints.capture_file("run-new", workdir, target)
            target.write_text("created", encoding="utf-8")
            checkpoints.mark_file_after("run-new", workdir, target)
            restored = checkpoints.restore_files("run-new", workdir)
            self.assertEqual(restored["restored"], 1)
            self.assertFalse(target.exists())

    def test_git_step_checkpoint_captures_cli_edits_creates_and_deletes(self):
        with patch.object(checkpoints, "_DIR", self.root / "checkpoints"):
            repo = self.root / "repo-checkpoint"
            repo.mkdir()
            def git(*args):
                return subprocess.run(["git", "-C", str(repo), *args], check=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            git("init", "-q")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Checkpoint Test")
            (repo / "edit.txt").write_text("before edit\n", encoding="utf-8")
            (repo / "delete.txt").write_text("before delete\n", encoding="utf-8")
            git("add", "edit.txt", "delete.txt")
            git("commit", "-qm", "baseline")
            started = checkpoints.begin_git_step("run-git-step", repo)
            self.assertTrue(started["ok"])
            (repo / "edit.txt").write_text("after edit\n", encoding="utf-8")
            (repo / "delete.txt").unlink()
            (repo / "new.txt").write_text("created\n", encoding="utf-8")
            finished = checkpoints.finish_git_step(
                "run-git-step", repo, started["head"], started["before_paths"])
            self.assertTrue(finished["ok"])
            preview = checkpoints.snapshot_preview("run-git-step", repo)
            self.assertEqual({row["path"] for row in preview["files"]},
                             {"edit.txt", "delete.txt", "new.txt"})
            restored = checkpoints.restore_files("run-git-step", repo)
            self.assertEqual(restored["restored"], 3)
            self.assertEqual((repo / "edit.txt").read_text(encoding="utf-8"), "before edit\n")
            self.assertEqual((repo / "delete.txt").read_text(encoding="utf-8"), "before delete\n")
            self.assertFalse((repo / "new.txt").exists())

    def test_git_step_checkpoint_warns_that_non_file_side_effects_are_not_reversible(self):
        with patch.object(checkpoints, "_DIR", self.root / "checkpoints"):
            repo = self.root / "repo-no-change"
            repo.mkdir()
            def git(*args):
                return subprocess.run(["git", "-C", str(repo), *args], check=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            git("init", "-q")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Checkpoint Test")
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            git("add", "base.txt")
            git("commit", "-qm", "baseline")
            started = checkpoints.begin_git_step("run-no-change", repo)
            finished = checkpoints.finish_git_step(
                "run-no-change", repo, started["head"], started["before_paths"])
            self.assertTrue(finished["ok"])
            self.assertIn("shell/MCP", finished["warning"])

    def test_git_step_checkpoint_restores_both_sides_of_a_rename(self):
        with patch.object(checkpoints, "_DIR", self.root / "checkpoints"):
            repo = self.root / "repo-rename"
            repo.mkdir()
            def git(*args):
                return subprocess.run(["git", "-C", str(repo), *args], check=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            git("init", "-q")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Checkpoint Test")
            (repo / "old.txt").write_text("rename me\n", encoding="utf-8")
            git("add", "old.txt")
            git("commit", "-qm", "baseline")
            started = checkpoints.begin_git_step("run-rename", repo)
            (repo / "old.txt").rename(repo / "new.txt")
            git("add", "-A")
            finished = checkpoints.finish_git_step(
                "run-rename", repo, started["head"], started["before_paths"])
            self.assertTrue(finished["ok"])
            preview = checkpoints.snapshot_preview("run-rename", repo)
            self.assertEqual({row["path"] for row in preview["files"]},
                             {"old.txt", "new.txt"})
            restored = checkpoints.restore_files("run-rename", repo)
            self.assertEqual(restored["restored"], 2)
            self.assertEqual((repo / "old.txt").read_text(encoding="utf-8"), "rename me\n")
            self.assertFalse((repo / "new.txt").exists())

    def test_git_step_checkpoint_reports_files_it_cannot_snapshot(self):
        with patch.object(checkpoints, "_DIR", self.root / "checkpoints"):
            repo = self.root / "repo-incomplete"
            repo.mkdir()
            def git(*args):
                return subprocess.run(["git", "-C", str(repo), *args], check=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            git("init", "-q")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Checkpoint Test")
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            git("add", "base.txt")
            git("commit", "-qm", "baseline")
            (repo / "base.txt").write_text("edited\n", encoding="utf-8")
            started = checkpoints.begin_git_step("run-incomplete", repo)
            (repo / "new.txt").write_text("created\n", encoding="utf-8")
            with patch.object(checkpoints, "capture_bytes", return_value={"ok": False,
                                                                            "reason": "snapshot_size_limit"}):
                finished = checkpoints.finish_git_step(
                    "run-incomplete", repo, started["head"], started["before_paths"])
            self.assertFalse(finished["ok"])
            self.assertIn("new.txt", finished["warning"])

    def test_checkpoint_restore_refuses_symlink_target_without_following_it(self):
        with patch.object(checkpoints, "_DIR", self.root / "checkpoints"):
            workdir = self.root / "work"
            workdir.mkdir()
            target = workdir / "note.txt"
            target.write_text("before", encoding="utf-8")
            checkpoints.capture_file("run-symlink", workdir, target)
            target.write_text("agent edit", encoding="utf-8")
            checkpoints.mark_file_after("run-symlink", workdir, target)
            outside = self.root / "outside.txt"
            outside.write_text("outside stays", encoding="utf-8")
            target.unlink()
            try:
                target.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is unavailable")
            restored = checkpoints.restore_files("run-symlink", workdir)
            self.assertEqual(outside.read_text(encoding="utf-8"), "outside stays")
            self.assertTrue(target.is_symlink())
            self.assertEqual(restored["restored"], 0)
            self.assertEqual(restored["conflicts"], ["note.txt"])

    def test_disabled_tools_are_rejected_at_execution_boundary(self):
        from core import builtin_agent
        with tempfile.TemporaryDirectory() as wd:
            result = builtin_agent._exec_tool(
                wd, "write_file", {"path": "x.txt", "content": "x"},
                disabled_tools={"write_file"})
            self.assertIn("禁用", result)
            self.assertFalse((Path(wd) / "x.txt").exists())

    def test_builtin_file_write_is_rewindable_end_to_end(self):
        from core import builtin_agent
        with patch.object(checkpoints, "_DIR", self.root / "checkpoints"):
            workdir = self.root / "work"
            workdir.mkdir()
            target = workdir / "note.txt"
            target.write_text("before", encoding="utf-8")
            result = builtin_agent._exec_tool(
                workdir, "write_file", {"path": "note.txt", "content": "after"},
                checkpoint_run_id="run-e2e")
            self.assertIn("已写入", result)
            self.assertEqual(checkpoints.snapshot_preview("run-e2e", workdir)["files"][0]["status"],
                             "changed")
            checkpoints.restore_files("run-e2e", workdir)
            self.assertEqual(target.read_text(encoding="utf-8"), "before")

    def test_sandbox_denies_file_tool_outside_allowed_roots(self):
        from core import builtin_agent
        outside = self.root / "outside.txt"
        with tempfile.TemporaryDirectory() as wd:
            sandbox = policy.normalize_sandbox({"allowed_roots": [wd]}, wd)
            result = builtin_agent._exec_tool(
                wd, "write_file", {"path": str(outside), "content": "x"},
                sandbox=sandbox)
            self.assertIn("沙箱", result)
            self.assertFalse(outside.exists())

    def test_sandbox_network_off_fails_closed_for_shell(self):
        from core import builtin_agent
        with tempfile.TemporaryDirectory() as wd:
            sandbox = policy.normalize_sandbox({"allowed_roots": [wd], "network": False}, wd)
            result = builtin_agent._exec_tool(
                wd, "run_command", {"command": "echo should-not-run"}, sandbox=sandbox)
            self.assertIn("网络隔离", result)

    def test_shell_does_not_start_a_process_when_platform_has_no_sandbox_backend(self):
        from core import builtin_agent, runner
        with tempfile.TemporaryDirectory() as wd:
            sandbox = policy.normalize_sandbox({}, wd)
            with patch.object(runner, "run_process") as run_process, \
                 patch.object(builtin_agent.shutil, "which", return_value=None):
                result = builtin_agent._exec_tool(
                    wd, "run_command", {"command": "echo must-not-run"}, sandbox=sandbox)
        self.assertIn("隔离后端", result)
        run_process.assert_not_called()

    def test_sandbox_network_off_fails_closed_for_mcp_tools(self):
        from core import builtin_agent, mcp_client
        with tempfile.TemporaryDirectory() as wd:
            sandbox = policy.normalize_sandbox({"allowed_roots": [wd], "network": False}, wd)
            with patch.object(mcp_client, "dispatch_full_name") as dispatch:
                result = builtin_agent._exec_tool(
                    wd, "mcp__demo__send", {"message": "hello"}, sandbox=sandbox)
        self.assertIn("网络隔离", result)
        dispatch.assert_not_called()

    def test_external_cli_sandbox_policy_fails_closed_when_backend_cannot_enforce(self):
        from core import pipeline
        sandbox = policy.normalize_sandbox({"network": False}, self.root)
        self.assertIn("网络隔离", pipeline._external_sandbox_block_reason(
            {"id": "codex-cli", "kind": "codex", "mode": "real"}, self.root, sandbox))
        self.assertEqual(pipeline._external_sandbox_block_reason(
            {"id": "codex-cli", "kind": "codex", "mode": "real"}, self.root,
            policy.normalize_sandbox({}, self.root)), "")

    def test_external_cli_without_verified_os_isolation_is_blocked(self):
        from core import pipeline
        sandbox = policy.normalize_sandbox({}, self.root)
        reason = pipeline._external_sandbox_block_reason(
            {"id": "claude", "kind": "claude", "mode": "real"}, self.root, sandbox)
        self.assertIn("OS", reason)

    def test_codex_full_access_override_is_ignored(self):
        from core import runner
        with patch.dict(os.environ, {"TUTTI_CODEX_SANDBOX": "danger-full-access"}):
            self.assertEqual(runner._codex_sandbox(readonly=False), "workspace-write")
        self.assertEqual(runner._codex_sandbox(readonly=True), "read-only")

    def test_disabled_builtin_tool_is_removed_from_provider_schema(self):
        from core import builtin_agent
        tools = builtin_agent._openai_tools(disabled_tools={"write_file"})
        self.assertNotIn("write_file", [x["function"]["name"] for x in tools])

    def test_task_disabled_mcp_tool_is_removed_from_provider_schema(self):
        from core import builtin_agent
        with patch.object(builtin_agent, "_mcp_specs", return_value=[{
                "full_name": "mcp__demo__danger", "server": "demo",
                "description": "", "input_schema": {}}]):
            tools = builtin_agent._openai_tools(disabled_tools={"mcp__demo__danger"})
        self.assertNotIn("mcp__demo__danger", [x["function"]["name"] for x in tools])

    def test_old_tool_outputs_are_bounded_but_recent_results_remain_full(self):
        from core import builtin_agent
        messages = [{"role": "tool_results", "tool_results": [("old", "x" * 4000)]}]
        messages.extend({"role": "tool_results", "tool_results": [(str(i), "recent")]} for i in range(4))
        builtin_agent._compact_old_tool_results(messages, keep_rounds=4, old_result_chars=1200)
        self.assertLess(len(messages[0]["tool_results"][0][1]), 1300)
        self.assertIn("重新读取", messages[0]["tool_results"][0][1])
        self.assertEqual(messages[-1]["tool_results"][0][1], "recent")

    def test_compacted_old_tool_output_can_be_retrieved_by_scoped_reference(self):
        from core import builtin_agent, tool_outputs
        source = "old-output-" * 400
        messages = [{"role": "tool_results", "tool_results": [("old-call", source)]}]
        messages.extend({"role": "tool_results", "tool_results": [(str(i), "recent")]}
                       for i in range(4))
        with patch.object(tool_outputs, "_DIR", self.root / "tool_outputs"):
            builtin_agent._compact_old_tool_results(messages, keep_rounds=4,
                                                    old_result_chars=1200, run_id="run-ref")
            marker = messages[0]["tool_results"][0][1]
            self.assertIn("read_tool_output", marker)
            ref = marker.split("ref=", 1)[1].split()[0]
            restored = builtin_agent._exec_tool(
                self.root, "read_tool_output", {"ref": ref}, tool_output_run_id="run-ref")
        self.assertEqual(restored, source)

    def test_mcp_tool_disable_applies_to_registry_and_dispatch(self):
        from core import mcp_client
        server = {"name": "demo", "command": "demo", "args": [], "env": {},
                  "disabled_tools": ["danger"]}
        with patch.object(mcp_client, "_settings_text", return_value=json.dumps([server])), \
             patch.object(mcp_client, "list_tools", return_value={"ok": True, "tools": [
                 {"name": "safe", "description": "", "input_schema": {"type": "object"}},
                 {"name": "danger", "description": "", "input_schema": {"type": "object"}},
             ]}), \
             patch.object(mcp_client, "call_tool") as call:
            mcp_client.invalidate_tools_cache()
            visible = mcp_client.tool_specs_cached(
                force=True, sandbox=policy.normalize_sandbox({}, self.root),
                workdir=str(self.root))
            denied = mcp_client.dispatch_full_name("mcp__demo__danger", {})
        self.assertEqual([x["name"] for x in visible], ["safe"])
        self.assertFalse(denied["ok"])
        self.assertIn("禁用", denied["error"])
        call.assert_not_called()

    def test_mcp_dispatch_enforces_task_disabled_tools_at_execution_boundary(self):
        from core import mcp_client
        config = json.dumps([{"name": "demo", "command": "demo"}])
        with patch.object(mcp_client, "_settings_text", return_value=config), \
             patch.object(mcp_client, "call_tool") as call:
            denied = mcp_client.dispatch_full_name(
                "mcp__demo__danger", {}, disabled_tools=["mcp__demo__danger"])
        self.assertFalse(denied["ok"])
        self.assertIn("禁用", denied["error"])
        call.assert_not_called()

    def test_mcp_dispatch_fails_closed_without_process_sandbox_backend(self):
        from core import mcp_client
        config = json.dumps([{"name": "demo", "command": "demo"}])
        sandbox = policy.normalize_sandbox({}, self.root)
        with patch.object(mcp_client, "_settings_text", return_value=config), \
             patch.object(mcp_client.shutil, "which", return_value=None), \
             patch.object(mcp_client.subprocess, "Popen") as spawn:
            denied = mcp_client.dispatch_full_name(
                "mcp__demo__safe", {}, sandbox=sandbox, workdir=str(self.root))
        self.assertFalse(denied["ok"])
        self.assertIn("隔离后端", denied["error"])
        spawn.assert_not_called()

    def test_mcp_configuration_change_invalidates_visible_tool_cache(self):
        from core import mcp_client
        open_config = json.dumps([{"name": "demo", "command": "demo"}])
        disabled_config = json.dumps([{"name": "demo", "command": "demo",
                                       "disabled_tools": ["danger"]}])
        with patch.object(mcp_client, "_settings_text", side_effect=[open_config, disabled_config]), \
             patch.object(mcp_client, "list_tools", return_value={"ok": True, "tools": [
                 {"name": "danger", "description": "", "input_schema": {"type": "object"}},
             ]}) as list_tools:
            mcp_client.invalidate_tools_cache()
            sandbox = policy.normalize_sandbox({}, self.root)
            self.assertEqual(len(mcp_client.tool_specs_cached(
                sandbox=sandbox, workdir=str(self.root))), 1)
            self.assertEqual(mcp_client.tool_specs_cached(
                sandbox=sandbox, workdir=str(self.root)), [])
        self.assertEqual(list_tools.call_count, 2)

    def test_mcp_tool_discovery_without_task_policy_does_not_spawn_server(self):
        from core import mcp_client
        with patch.object(mcp_client, "_settings_text", return_value=json.dumps([
                {"name": "demo", "command": "demo"}])), \
             patch.object(mcp_client, "list_tools") as list_tools:
            self.assertEqual(mcp_client.tool_specs_cached(force=True), [])
        list_tools.assert_not_called()

    def test_mcp_initialization_failure_kills_spawned_server(self):
        from core import mcp_client
        process = unittest.mock.Mock()
        session = mcp_client._Session({"name": "demo", "command": "demo"})
        with patch.object(mcp_client, "_sandbox_launch",
                          return_value=(["bwrap", "--", "demo"], {}, str(self.root))), \
             patch.object(mcp_client.subprocess, "Popen", return_value=process), \
             patch.object(mcp_client.threading, "Thread") as thread, \
             patch.object(session, "_request", side_effect=RuntimeError("init timeout")):
            thread.return_value.start.return_value = None
            with self.assertRaisesRegex(RuntimeError, "init timeout"):
                session.__enter__()
        process.kill.assert_called_once_with()

    def test_memory_and_checkpoint_routes_require_authentication(self):
        handler = object.__new__(main.Handler)
        handler.path = "/api/tasks/task-1/memory"
        handler._authed = lambda: False
        handler._json = lambda code, payload: (code, payload)
        self.assertEqual(handler._route_get()[0], 401)
        handler.path = "/api/runs/run-1/checkpoints/restore"
        self.assertEqual(handler._route_post()[0], 401)

    def test_project_memory_requires_approval_and_expires(self):
        with patch.object(project_memory, "DEFAULT_TTL_DAYS", 1):
            row = project_memory.propose(self.root, source_run="run-1", title="Facts",
                                         facts=["uses Python"], now=1000)
            self.assertEqual(row["status"], "pending")
            self.assertEqual(project_memory.active_text(self.root, now=1001), "")
            approved = project_memory.decide(self.root, row["id"], "approve",
                                             expected_version=1, now=1002)
            self.assertEqual(approved["status"], "approved")
            self.assertIn("uses Python", project_memory.active_text(self.root, now=1003))
            self.assertEqual(project_memory.active_text(self.root, now=1002 + 86400), "")

    def test_project_memory_uses_version_check_for_review(self):
        row = project_memory.propose(self.root, source_run="run-2", facts=["fact"], now=1000)
        project_memory.decide(self.root, row["id"], "approve", expected_version=1, now=1001)
        with self.assertRaises(ValueError):
            project_memory.decide(self.root, row["id"], "archive", expected_version=1, now=1002)

    def test_project_memory_rejects_workspace_symlink_store(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.root / ".codebee"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")
        with self.assertRaises(ValueError):
            project_memory.propose(self.root, facts=["do not write outside"])
        self.assertFalse((outside / "project-memory.jsonl").exists())

    def test_checkpoint_records_hashes_and_unknown_is_not_replayable(self):
        with patch.object(checkpoints, "_DIR", self.root / "checkpoints"):
            started = checkpoints.start("run-1", 2, "implement", "prompt with token sk-1234567890", attempt=2)
            self.assertEqual(started["attempt"], 2)
            self.assertEqual(len(started["prompt_sha256"]), 64)
            self.assertNotIn("sk-1234567890", json.dumps(started))
            checkpoints.finish("run-1", 2, "unknown", error="network timeout")
            view = checkpoints.replay_preview("run-1")
        self.assertFalse(view["replayable"])
        self.assertEqual(view["blocked_reason"], "requires_reconciliation")
        self.assertEqual(view["steps"][0]["status"], "unknown")

    def test_checkpoint_orphan_and_missing_run_are_not_replayable(self):
        with patch.object(checkpoints, "_DIR", self.root / "checkpoints"):
            checkpoints.start("orphan", 1, "implement", "prompt")
            orphan = checkpoints.replay_preview("orphan")
            missing = checkpoints.replay_preview("missing")
        self.assertFalse(orphan["replayable"])
        self.assertFalse(missing["replayable"])
        self.assertEqual(missing["blocked_reason"], "checkpoint_not_found")

    def test_webhook_signature_and_delivery_deduplication(self):
        raw = b'{"goal":"run"}'
        secret = "local-secret"
        timestamp = "1700000000"
        signature = webhooks.sign(secret, raw, timestamp)
        self.assertTrue(webhooks.verify(secret, raw, signature, timestamp, now=1700000001))
        self.assertFalse(webhooks.verify(secret, raw, signature, timestamp, now=1700001000))
        github = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        self.assertTrue(webhooks.verify_raw(secret, raw, github))
        with patch.object(webhooks, "_FILE", self.root / "deliveries.json"):
            self.assertTrue(webhooks.claim_delivery("delivery-1", now=1700000000)[0])
            self.assertFalse(webhooks.claim_delivery("delivery-1", now=1700000001)[0])
            webhooks.release_delivery("delivery-1")
            self.assertTrue(webhooks.claim_delivery("delivery-1", now=1700000002)[0])

    def test_eval_matrix_reports_baseline_regressions(self):
        manifest = eval_matrix.normalize_manifest({
            "id": "release-1",
            "candidates": ["model-a", "model-b"],
            "cases": [{"id": "case-1", "prompt": "hello", "expected": "hi"}],
            "regression_threshold": 0.5,
        })
        report = eval_matrix.evaluate(manifest, {
            "model-a": {"case-1": {"score": 7.0}},
            "model-b": {"case-1": {"score": 8.0}},
        }, baseline={"model-a": {"case-1": {"score": 8.0}}})
        self.assertEqual(report["matrix"][0]["candidate"], "model-a")
        self.assertEqual(report["regressions"][0]["delta"], -1.0)
        self.assertEqual(report["best_candidate"], "model-b")
        self.assertIsNone(eval_matrix.evaluate(manifest, {"model-a": {"case-1": {"score": float("nan")}}})["matrix"][0]["score"])

    def test_knowledge_pipeline_chunks_with_source_hash_and_quality(self):
        with patch.object(retrieval, "_FILE", self.root / "index.json"), \
             patch.object(knowledge_pipeline, "_REPORT_FILE", self.root / "reports.json"):
            result = knowledge_pipeline.ingest(
                "notes.md", "alpha beta " * 80,
                metadata={"scope": "code", "owner": "team"}, chunk_size=80, overlap=10)
            report = knowledge_pipeline.quality_report(result["ingest_id"])
        self.assertGreater(result["chunks"], 1)
        self.assertEqual(len(result["source_sha256"]), 64)
        self.assertEqual(report["source_sha256"], result["source_sha256"])
        self.assertEqual(report["indexed_chunks"], result["chunks"])
        self.assertGreater(report["quality"]["non_empty_ratio"], 0)
        with self.assertRaises(ValueError):
            knowledge_pipeline.ingest("bad", "text", metadata=["not", "an object"])

    def test_policy_sandbox_and_flow_graph_are_deterministic(self):
        sandbox = policy.normalize_sandbox({
            "allowed_roots": [str(self.root)], "env_allowlist": ["PATH", "OPENAI_API_KEY"],
            "network": False, "timeout_s": 0, "max_output_bytes": 0}, self.root)
        self.assertFalse(sandbox["network"])
        self.assertTrue(policy.path_allowed(self.root / "x.txt", sandbox))
        self.assertFalse(policy.path_allowed(self.root.parent / "x.txt", sandbox))
        self.assertEqual(policy.filter_env({"PATH": "x", "SECRET": "y"}, sandbox), {"PATH": "x"})
        self.assertFalse(policy.normalize_sandbox({"network": "false"}, self.root)["network"])
        widened = policy.normalize_sandbox({"allowed_roots": [str(self.root.parent)]}, self.root)
        self.assertEqual(widened["allowed_roots"], [str(self.root)])
        graph = flow_graph.build("code")
        self.assertTrue(graph["nodes"])
        self.assertTrue(graph["edges"])
        self.assertEqual(graph["version_id"], flow_graph.build("code")["version_id"])

    def test_otel_export_has_standard_span_shape_and_route_endpoint(self):
        run = {"id": "run-otel", "status": "done", "started_at": "2026-01-01 00:00:00",
               "ended_at": "2026-01-01 00:00:01", "steps": [{
                   "n": 1, "role": "implement", "status": "done", "agent": "mock",
                   "started_at_epoch": 1, "ended_at_epoch": 2, "tokens": 3,
               }]}
        exported = tracing.to_otel(tracing.build_trace(run))
        span = exported["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        self.assertEqual(span["name"], "agent.implement")
        self.assertIn("traceId", span)
        h = object.__new__(main.Handler)
        h.path = "/api/flows/code/graph"
        h._authed = lambda: True
        h._json = lambda code, payload: (code, payload)
        status, payload = h._route_get()
        self.assertEqual(status, 200)
        self.assertEqual(payload["graph"]["flow_id"], "code")


if __name__ == "__main__":
    unittest.main()
