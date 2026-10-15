import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from base import BaseTest
from app.core import hooks


class HookDiagnosticsTests(BaseTest):
    def setUp(self):
        super().setUp()
        p = patch.object(hooks, "_FILE", self.data_dir / "hooks.json")
        p.start()
        self.addCleanup(p.stop)

    def hook(self, code, **extra):
        return {"id": "example", "name": "Example", "event": "task_start",
                "cmd": '"%s" -c "%s"' % (sys.executable, code), **extra}

    def test_nonzero_stdout_is_not_injected_and_error_is_queryable(self):
        hooks.save_list([self.hook("import sys;print('bad inject');sys.stderr.write('broken');sys.exit(3)")])
        self.assertEqual(hooks.run_event("task_start", run_id="r1"), "")
        result = hooks.diagnostics()
        row = result["recent"][0]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["exit_code"], 3)
        self.assertGreaterEqual(row["duration_ms"], 0)
        self.assertEqual(row["run_id"], "r1")
        self.assertIn("broken", row["error"])
        self.assertNotIn("bad inject", json.dumps(result))

    def test_success_records_without_stdout_or_user_payload(self):
        hooks.save_list([self.hook("print('success')")])
        self.assertEqual(hooks.run_event("task_start", text="private content"), "success")
        result = hooks.diagnostics()
        self.assertEqual(result["recent"][0]["status"], "succeeded")
        self.assertNotIn("private content", json.dumps(result))
        self.assertNotIn("success\"", json.dumps(result))

    def test_test_one_returns_real_failure_and_preserves_latest_error_after_success(self):
        ok, message = hooks.test_one(self.hook("import sys;sys.stderr.write('failure');sys.exit(4)")["cmd"])
        self.assertFalse(ok)
        self.assertIn("4", message)
        ok, _ = hooks.test_one(self.hook("print('fixed')")["cmd"])
        self.assertTrue(ok)
        self.assertEqual(hooks.diagnostics()["recent"][0]["status"], "succeeded")
        self.assertEqual(hooks.diagnostics()["last_errors"][0]["exit_code"], 4)

    def test_timeout_spawn_and_parse_failures_are_distinct(self):
        for exception, status in ((subprocess.TimeoutExpired("hook", 1), "timeout"),
                                  (OSError("missing executable"), "spawn_failed")):
            with self.subTest(status=status), patch.object(hooks.subprocess, "run", side_effect=exception):
                ok, _ = hooks.test_one("hook")
                self.assertFalse(ok)
                self.assertEqual(hooks.diagnostics()["recent"][0]["status"], status)
        self.assertFalse(hooks.test_one('"unterminated')[0])
        self.assertEqual(hooks.diagnostics()["recent"][0]["status"], "invalid_command")

    def test_errors_redact_credentials_and_environment_values(self):
        secret = "opaque-test-value-123"
        with patch.dict("os.environ", {"CUSTOM_API_KEY": secret}), patch.object(
                hooks.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], 7, b"", ("password=short Bearer abcdef sk-long-secret-123 " + secret).encode())):
            self.assertFalse(hooks.test_one("hook --api-key=command-secret")[0])
        text = json.dumps(hooks.diagnostics())
        for value in (secret, "command-secret", "short", "abcdef", "sk-long-secret-123"):
            self.assertNotIn(value, text)

    def test_diagnostic_storage_failure_does_not_change_hook_result(self):
        from app.core import hook_diagnostics
        with patch.object(hook_diagnostics, "record", side_effect=OSError("disk full")):
            self.assertEqual(hooks._run_one(self.hook("print('ok')"), {}), "ok")

    def test_bounded_history_survives_concurrent_writes_and_reload(self):
        from app.core import hook_diagnostics
        def write(i):
            hook_diagnostics.record(self.data_dir, {"id": "hook-%d" % i, "event": "run_end"}, {},
                                    status="failed", exit_code=1, duration_ms=2, error="broken")
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write, range(130)))
        result = hooks.diagnostics()
        self.assertEqual(len(result["recent"]), 100)
        self.assertLessEqual(len(result["last_errors"]), 50)
        self.assertEqual(len({r["hook_id"] for r in result["recent"]}), 100)
