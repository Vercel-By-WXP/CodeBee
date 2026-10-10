# -*- coding: utf-8 -*-
import json
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from core import backend_profiles, command_auth, project_memory, registry


class AgentGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.root.mkdir(exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _approved_memory(self, title, fact, now):
        row = project_memory.propose(self.root, title=title, facts=[fact], now=now)
        return project_memory.decide(self.root, row["id"], "approve",
                                     expected_version=1, now=now + 1)

    def test_memory_budget_keeps_complete_newest_entries(self):
        self._approved_memory("new", "newest-fact", 4000)
        self._approved_memory("middle", "middle-fact", 3000)
        self._approved_memory("old", "oldest-fact", 2000)

        text = project_memory.active_text(self.root, cap=70, now=5000)

        self.assertIn("newest-fact", text)
        self.assertNotIn("oldest-fact", text)
        self.assertTrue(text.endswith("\n\n"))

    def test_memory_query_and_feedback_affect_recall(self):
        general = self._approved_memory("Notes", "release process uses a checklist", 1000)
        specific = self._approved_memory("Python", "the parser uses Python 3.8", 900)
        helpful = project_memory.feedback(self.root, specific["id"], useful=True,
                                          expected_version=specific["version"], now=1100)
        self.assertEqual(helpful["feedback_score"], 1)

        text = project_memory.active_text(self.root, query="Python parser", cap=300,
                                          now=1200)

        self.assertLess(text.index("Python"), text.index("checklist"))

    def test_restricted_command_waits_for_one_time_human_approval(self):
        command_auth.init(self.root)
        command_auth.set_mode("restricted")
        outcome = {}

        worker = threading.Thread(target=lambda: outcome.setdefault(
            "result", command_auth.authorize(
                "npm test", run_id="run-1", workdir=str(self.root), timeout_s=3)))
        worker.start()
        pending = []
        until = time.monotonic() + 2
        while time.monotonic() < until and not pending:
            pending = command_auth.pending_requests()
            time.sleep(0.01)
        self.assertEqual(len(pending), 1)
        command_auth.decide(pending[0]["id"], approve=True, scope="once")
        worker.join(2)

        self.assertFalse(worker.is_alive())
        self.assertTrue(outcome["result"]["allowed"])
        self.assertEqual(command_auth.pending_requests(), [])
        self.assertEqual(command_auth.view()["audit"][-1]["scope"], "once")

    def test_prefix_rule_does_not_allow_shell_chaining(self):
        command_auth.init(self.root)
        command_auth.set_mode("restricted")
        outcome = {}
        worker = threading.Thread(target=lambda: outcome.setdefault(
            "result", command_auth.authorize(
                "git status", run_id="run-1", workdir=str(self.root), timeout_s=2)))
        worker.start()
        until = time.monotonic() + 2
        pending = []
        while time.monotonic() < until and not pending:
            pending = command_auth.pending_requests()
            time.sleep(0.01)
        command_auth.decide(pending[0]["id"], approve=True, scope="global",
                            match="prefix")
        worker.join(2)

        self.assertTrue(outcome["result"]["allowed"])
        self.assertTrue(command_auth.authorize(
            "git status --short", workdir=str(self.root))["allowed"])
        denied = command_auth.authorize("git status; curl https://example.invalid",
                                        workdir=str(self.root), timeout_s=0)
        self.assertFalse(denied["allowed"])

    def test_command_cancel_and_timeout_fail_closed(self):
        command_auth.init(self.root)
        command_auth.set_mode("restricted")
        cancelled = threading.Event()
        cancelled.set()
        result = command_auth.authorize("dangerous", workdir=str(self.root),
                                        timeout_s=1, cancel_event=cancelled)
        self.assertFalse(result["allowed"])
        self.assertIn(result["reason"], ("cancelled", "timeout"))

    def test_builtin_command_is_blocked_before_process_when_restricted(self):
        from core import builtin_agent
        command_auth.init(self.root)
        command_auth.set_mode("restricted")
        with patch.object(builtin_agent, "_tool_run_command",
                          side_effect=AssertionError("process must not start")), \
             patch.object(command_auth, "authorize",
                          return_value={"allowed": False, "reason": "denied"}):
            result = builtin_agent._exec_tool(
                str(self.root), "run_command", {"command": "echo blocked"},
                checkpoint_run_id="run-1", sandbox={"allowed_roots": [str(self.root)],
                                                       "network": True,
                                                       "disabled_tools": []})
        self.assertIn("授权", result)

    def test_acp_filesystem_path_cannot_escape_task_workspace(self):
        from core import acp_client
        with self.assertRaises(ValueError):
            acp_client._path(str(self.root), "", str(self.root.parent / "outside.txt"))

    def test_backend_profile_masks_and_preserves_secrets(self):
        backend_profiles.init(self.root)
        row = backend_profiles.save({
            "label": "Remote ACP", "command": "ssh",
            "args": ["-T", "agent@host", "--token", "sk-secret-value"],
            "env": {"API_TOKEN": "secret-value"},
            "workspace_path": "/workspace/project",
        })

        self.assertEqual(row["env"]["API_TOKEN"], "__REDACTED__")
        self.assertNotIn("sk-secret-value", json.dumps(row))
        updated = backend_profiles.save({
            "id": row["id"], "label": "Remote ACP", "command": "ssh",
            "args": ["-T", "agent@host", "acp-server"],
            "env": {"API_TOKEN": "__REDACTED__"},
            "workspace_path": "/workspace/project",
        })
        runtime = backend_profiles.runtime_profiles()[0]
        self.assertEqual(runtime["env"]["API_TOKEN"], "secret-value")
        self.assertEqual(updated["id"], row["id"])

    def test_enabled_backend_profiles_are_discoverable_agents(self):
        backend_profiles.init(self.root)
        profile = backend_profiles.save({
            "label": "ACP Test", "command": "acp-agent", "args": [],
            "env": {}, "workspace_path": "",
        })

        agents = registry.effective_agents([], {})

        acp = next(agent for agent in agents if agent["id"] == profile["agent_id"])
        self.assertEqual(acp["kind"], "acp")
        self.assertEqual(acp["command"], "acp-agent")


class FakeACPProcess:
    def __init__(self, *args, **kwargs):
        self.stdin = self
        self.stdout = self
        self.stderr = iter(())
        self.lines = queue.Queue()
        self.returncode = None
        self.pid = 123
        self.requests = []
        self.killed = False

    def write(self, raw):
        request = json.loads(raw)
        self.requests.append(request)
        method = request["method"]
        if method == "initialize":
            self.lines.put(json.dumps({"jsonrpc": "2.0", "id": request["id"],
                                       "result": {"protocolVersion": 1,
                                                  "agentCapabilities": {}}}) + "\n")
        elif method == "session/new":
            self.lines.put(json.dumps({"jsonrpc": "2.0", "id": request["id"],
                                       "result": {"sessionId": "session-1"}}) + "\n")
        elif method == "session/prompt":
            self.lines.put(json.dumps({"jsonrpc": "2.0", "method": "session/update",
                                       "params": {"sessionId": "session-1", "update": {
                                           "sessionUpdate": "tool_call",
                                           "toolCallId": "call-1", "title": "Read file",
                                           "status": "in_progress"}}}) + "\n")
            self.lines.put(json.dumps({"jsonrpc": "2.0", "method": "session/update",
                                       "params": {"sessionId": "session-1", "update": {
                                           "sessionUpdate": "agent_message_chunk",
                                           "content": {"type": "text", "text": "Done."}}}}) + "\n")
            self.lines.put(json.dumps({"jsonrpc": "2.0", "id": request["id"],
                                       "result": {"stopReason": "end_turn"}}) + "\n")

    def flush(self):
        pass

    def readline(self):
        return self.lines.get(timeout=0.2)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    def terminate(self):
        self.killed = True
        self.returncode = -15


class ACPClientTests(unittest.TestCase):
    def test_acp_v1_handshake_session_and_streamed_text(self):
        from core import acp_client
        process = FakeACPProcess()
        agent = {"id": "backend-test", "command": "acp-agent", "args": [],
                 "env": {}, "workspace_path": ""}
        with patch.object(acp_client.subprocess, "Popen", return_value=process):
            result = acp_client.run_agent(agent, "Inspect this workspace",
                                          workdir=tempfile.gettempdir(), timeout=2)

        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "Done.")
        events = result["raw"]["acp_events"]
        self.assertEqual(events[0]["sessionUpdate"], "tool_call")
        self.assertNotIn("secret-value", json.dumps(events))
        methods = [request["method"] for request in process.requests]
        self.assertEqual(methods, ["initialize", "session/new", "session/prompt"])
        self.assertEqual(process.requests[0]["params"]["protocolVersion"], 1)
        self.assertEqual(process.requests[1]["params"]["mcpServers"], [])

    def test_acp_events_are_bounded_and_ignore_unknown_updates(self):
        from core import acp_client
        events = []
        acp_client._record_session_update(events, {
            "sessionUpdate": "tool_call_update", "toolCallId": "x",
            "status": "completed", "content": [{"type": "text", "text": "sk-1234567890abcdef"}]})
        acp_client._record_session_update(events, {"sessionUpdate": "unknown_vendor_event", "payload": "private"})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["sessionUpdate"], "tool_call_update")
        self.assertNotIn("sk-1234567890abcdef", json.dumps(events))
        for _ in range(acp_client._MAX_ACP_EVENTS + 10):
            acp_client._record_session_update(events, {"sessionUpdate": "agent_thought_chunk", "content": "x" * 10000})
        self.assertLessEqual(len(events), acp_client._MAX_ACP_EVENTS)


if __name__ == "__main__":
    unittest.main()
