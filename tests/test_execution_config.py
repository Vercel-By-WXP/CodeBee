import json
import threading
import time
from unittest.mock import patch

from base import BaseTest


class ExecutionConfigTests(BaseTest):
    def create(self, **extra):
        from app.core import store
        return store.create_task({"type": "novel", "goal": "Write a story",
                                  "workdir": str(self.workdir), **extra})

    def test_request_flow_and_defaults_have_distinct_sources(self):
        from app.core import execution_config
        task = self.create(rounds=4, thinking="high")
        view = execution_config.task_view(task)
        self.assertEqual(view["rounds"]["source"], "task.request")
        self.assertEqual(view["rounds"]["value"], 4)
        self.assertEqual(view["threshold"]["source"], "flow")
        self.assertEqual(view["thinking"]["source"], "task.request")
        self.assertEqual(view["mode"]["source"], "default")

    def test_task_edits_are_tracked_but_old_run_configuration_is_frozen(self):
        from app.core import store, execution_config
        task = self.create(thinking="high")
        run = store.create_run("orchestration", "Story", task_id=task["id"])
        ok, error = store.update_task_params(task["id"], {"thinking": "low"})
        self.assertTrue(ok, error)
        self.assertEqual(execution_config.task_view(task)["thinking"]["source"], "task.params")
        frozen = execution_config.run_view(run)
        self.assertEqual(frozen["parameters"]["thinking"]["value"], "high")
        self.assertEqual(frozen["parameters"]["thinking"]["source"], "task.request")

    def test_editing_thinking_preserves_unmentioned_model_and_cli(self):
        from app.core import store, execution_config
        task = self.create(type="direct", direct_agent="codex-cli",
                           direct_provider_id="p1", direct_model="m1")
        ok, error = store.update_task_params(task["id"], {"thinking": "high"})
        self.assertTrue(ok, error)
        self.assertEqual(task["direct_provider_id"], "p1")
        self.assertEqual(task["direct_model"], "m1")
        self.assertEqual(task["direct_agent"], "codex-cli")
        self.assertEqual(execution_config.task_view(task)["thinking"]["source"], "task.params")

    def test_invalid_direct_model_patch_cannot_partially_change_mode(self):
        from app.core import store
        task = self.create(type="direct")
        before = json.loads(json.dumps(task))
        ok, _ = store.update_task_params(task["id"], {"mode": "manual", "direct_model": "m1"})
        self.assertFalse(ok)
        self.assertEqual(task, before)

    def test_cli_override_explains_bypassed_model_and_resume_has_priority(self):
        from app.core import execution_config
        task = self.create(type="direct", direct_agent="codex-cli",
                           direct_provider_id="provider-a", direct_model="model-a")
        run = {"execution_snapshot": {"configuration": execution_config.capture(task)}}
        selection = execution_config.run_view(run)["selection"]
        self.assertEqual(selection["source"], "task.direct_agent")
        self.assertIn("direct_provider_id", selection["overrides"])
        task["resume"] = {"agent": "claude-code", "session": "session-private"}
        selection = execution_config.capture(task)["selection"]
        self.assertEqual(selection["source"], "task.resume")
        self.assertNotIn("session-private", json.dumps(selection))

    def test_step_evidence_is_redacted_and_exposes_actual_fallback_result(self):
        from app.core import execution_config, store
        task = self.create()
        run = store.create_run("orchestration", "Story", task_id=task["id"])
        agent = {"id": "codex-cli", "kind": "codex", "binding_mode": "explicit",
            "reasoning_effort": "high", "model": "primary", "env": {"API_KEY": "secret"},
            "call_chain": [{"model": "primary", "provider_id": "p1", "env": {"SECRET": "secret"},
                            "provider": {"id": "p1", "api_key": "sk-private-123456"}},
                           {"model": "backup", "provider_id": "p2"}]}
        step, _ = store.add_step(run["id"], "draft", "codex-cli", "Codex",
                                configuration=execution_config.agent_view(agent))
        store.finish_step(run["id"], step["n"], "done", binding_result=execution_config.result_view({
            "ok": True, "model": "backup", "provider_id": "p2", "attempts": [
                {"ok": False, "model": "primary", "provider_id": "p1", "error": "private error"},
                {"ok": True, "model": "backup", "provider_id": "p2"}]}))
        result = execution_config.run_view(run)
        self.assertEqual(result["steps"][0]["configuration"]["binding_source"], "binding.explicit")
        actual = result["steps"][0]["configuration"]["result"]
        self.assertEqual(actual["model"], "backup")
        self.assertEqual(actual["provider_id"], "p2")
        self.assertEqual(len(actual["attempts"]), 2)
        for private in ("API_KEY", "secret", "sk-private", "private error"):
            self.assertNotIn(private, json.dumps(result))

    def test_builtin_resolution_records_real_source(self):
        from app.core import builtin_agent, modelhub
        provider = {"id": "p1", "name": "Provider", "api_key": "test", "model": "model-a",
                    "models": [{"name": "model-a", "enabled": True}]}
        with patch.object(modelhub, "providers", return_value=[provider]), \
             patch.object(modelhub, "resolve_orchestrator", return_value=(provider, "model-a")):
            self.assertEqual(builtin_agent.resolve("p1", "model-a")["selection_source"], "task.direct_provider")
            self.assertEqual(builtin_agent.resolve()["selection_source"], "orchestrator")
        with patch.object(modelhub, "providers", return_value=[provider]), \
             patch.object(modelhub, "resolve_orchestrator", return_value=None):
            self.assertEqual(builtin_agent.resolve()["selection_source"], "provider.default")

    def test_real_builtin_step_records_configuration_without_running_model(self):
        from app.core import pipeline, store, builtin_agent, session_runtime
        task = self.create(type="direct")
        run = store.create_run("orchestration", "Talk", task_id=task["id"])
        bi = {"provider_id": "p1", "provider_name": "Provider", "model": "m1",
              "prov": {"api_key": "sk-private"}, "selection_source": "orchestrator"}
        result = {"ok": True, "text": "done", "model": "m1", "tokens": 1, "cost_usd": 0,
                  "raw": {"exit_code": 0}}
        with patch.object(builtin_agent, "run", return_value=result), \
             patch.object(pipeline, "_record_usage"), \
             patch.object(session_runtime, "default_runtime"):
            pipeline._run_builtin_step(run["id"], "direct", bi, "Hello", str(self.workdir),
                                       threading.Event(), sandbox=task["sandbox"])
        step = store.get_run(run["id"])["steps"][0]
        self.assertEqual(step["configuration"]["binding_source"], "orchestrator")
        self.assertEqual(step["configuration"]["result"]["model"], "m1")

    def test_real_cli_step_records_bound_input_and_actual_fallback(self):
        from app.core import pipeline, store, session_runtime
        task = self.create(type="code")
        run = store.create_run("orchestration", "Build", task_id=task["id"])
        agent = {"id": "codex-cli", "kind": "codex", "mode": "real", "model": "m1",
                 "binding_mode": "explicit", "env": {"API_KEY": "private"},
                 "call_chain": [{"provider_id": "p1", "model": "m1"}]}
        result = {"ok": True, "text": "done", "model": "m2", "provider_id": "p2",
                  "tokens": 1, "cost_usd": 0, "raw": {"exit_code": 0},
                  "attempts": [{"ok": False, "model": "m1", "provider_id": "p1"},
                               {"ok": True, "model": "m2", "provider_id": "p2"}]}
        with patch.object(pipeline, "_spawn_step", return_value=result), \
             patch.object(pipeline, "_record_usage"), \
             patch.object(session_runtime, "default_runtime"):
            pipeline._run_step(run["id"], "implement", agent, "Build", str(self.workdir),
                               True, threading.Event())
        step = store.get_run(run["id"])["steps"][0]
        self.assertEqual(step["configuration"]["model"], "m1")
        self.assertEqual(step["configuration"]["result"]["model"], "m2")
        self.assertEqual(step["configuration"]["result"]["provider_id"], "p2")
        persisted = json.loads((self.data_dir / "runs" / run["id"] / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["steps"][0]["configuration"], step["configuration"])

    def test_legacy_run_does_not_invent_sources(self):
        from app.core import execution_config
        result = execution_config.run_view({"id": "legacy", "steps": [{"n": 1, "model": "m"}]})
        self.assertEqual(result["parameters"], {})
        self.assertEqual(result["selection"]["source"], "unknown")

    def test_step_verification_command_uses_snapshot_credential_redaction(self):
        from app.core import store
        command = "check --api-key=private-key --token private-token --password=private-password"
        task = self.create(type="code", verify_command=command)
        run = store.create_run("orchestration", "Verify", task_id=task["id"])
        step, _ = store.add_step(run["id"], "verify", "shell", "Shell")
        store.finish_step(run["id"], step["n"], "done", verify_command=command)
        persisted = (self.data_dir / "runs" / run["id"] / "run.json").read_text(encoding="utf-8")
        for private in ("private-key", "private-token", "private-password"):
            self.assertNotIn(private, persisted)
        self.assertEqual(store.get_run(run["id"])["steps"][0]["verify_command"].count("<redacted>"), 3)
