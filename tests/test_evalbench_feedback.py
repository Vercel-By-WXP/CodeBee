import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock

from app.core import evalbench

# The server entry point imports ``core`` directly because it is launched with
# app/ as its working directory. Add that same import root for route tests.
APP_DIR = str(Path(__file__).resolve().parents[1] / "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
import main
from core import evalbench as route_evalbench

Handler = main.Handler


class EvalBenchFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.data_dir = Path(self.temp_dir.name)
        self.run = {
            "id": "run-failed-1", "task_id": "task-1", "status": "failed",
            "steps": [{"n": 1, "role": "implement", "agent_label": "worker",
                       "model": "model-x", "status": "failed",
                       "summary": "Failure with sk-123456789abcdef"}],
        }

    def test_preview_only_accepts_a_failed_step_and_redacts_sensitive_excerpt(self):
        with mock.patch.object(evalbench.paths, "DATA_DIR", self.data_dir):
            preview, error = evalbench.failure_sample_preview(self.run, 1)
            self.assertIsNone(error)
            self.assertEqual(preview["run_id"], "run-failed-1")
            self.assertIn("[key]", preview["failure_excerpt"])
            self.assertNotIn("sk-123456789abcdef", preview["failure_excerpt"])

            self.run["status"] = "succeeded"
            rejected, error = evalbench.failure_sample_preview(self.run, 1)

        self.assertIsNone(rejected)
        self.assertTrue(error)

    def test_conversion_requires_confirmation_and_never_persists_failure_output(self):
        sample = {"name": "Failure regression", "requirement": "Expected behavior",
                  "prompt": "Reproduction input", "dims": ["正确性", "完整性"]}
        with mock.patch.object(evalbench.paths, "DATA_DIR", self.data_dir):
            rejected, error = evalbench.sample_from_failed_run(
                self.run, 1, sample, confirmed=False)
            self.assertIsNone(rejected)
            self.assertTrue(error)
            self.assertEqual(evalbench.custom_samples(), [])

            created, error = evalbench.sample_from_failed_run(
                self.run, 1, sample, confirmed=True)
            saved = evalbench.custom_samples()

        self.assertIsNone(error)
        self.assertEqual(created["prompt"], "Reproduction input")
        self.assertEqual(len(saved), 1)
        self.assertNotIn("Failure with", saved[0]["prompt"])

    def test_conversion_redacts_sample_text_before_persisting(self):
        sample = {"name": "Redacted regression", "requirement": "Correct result",
                  "prompt": "Use api_key=abcdefghijklmno",
                  "dims": ["sk-123456789abcdef", "完整性"]}
        with mock.patch.object(evalbench.paths, "DATA_DIR", self.data_dir):
            created, error = evalbench.sample_from_failed_run(
                self.run, 1, sample, confirmed=True)
            saved = evalbench.custom_samples()

        self.assertIsNone(error)
        self.assertIn("api_key[key]", created["prompt"])
        self.assertNotIn("abcdefghijklmno", saved[0]["prompt"])
        self.assertNotIn("sk-123456789abcdef", saved[0]["dims"])

    def test_conversion_rejects_missing_or_non_failed_step(self):
        run = dict(self.run, steps=[dict(self.run["steps"][0], status="done")])
        sample = {"name": "Regression", "requirement": "Correct",
                  "prompt": "Original input", "dims": ["正确性", "完整性"]}
        with mock.patch.object(evalbench.paths, "DATA_DIR", self.data_dir):
            created, error = evalbench.sample_from_failed_run(
                run, 1, sample, confirmed=True)
        self.assertIsNone(created)
        self.assertTrue(error)

    def test_failed_runs_route_returns_only_redacted_failed_steps(self):
        handler = Handler.__new__(Handler)
        handler.path = "/api/evalbench/failed-runs"
        handler._authed = lambda: True
        response = {}
        handler._json = lambda status, body: response.update(status=status, body=body)
        runs = [self.run, dict(self.run, id="run-ok", status="done")]

        with mock.patch.object(main.store, "list_runs", return_value=runs):
            Handler._route_get(handler)

        self.assertEqual(response["status"], 200)
        self.assertEqual(len(response["body"]["failures"]), 1)
        excerpt = response["body"]["failures"][0]["failure_excerpt"]
        self.assertIn("[key]", excerpt)
        self.assertNotIn("sk-123456789abcdef", excerpt)

    def test_create_from_run_route_rejects_missing_run_and_unconfirmed_sample(self):
        handler = Handler.__new__(Handler)
        handler.path = "/api/evalbench/sample-from-run"
        # _route_post 现在先过 same-origin 闸（2026-10-20 起）；裸桩补空
        # headers——空 Origin=非浏览器客户端放行，与真请求语义一致。
        handler.headers = {}
        handler._authed = lambda: True
        handler._deny_control = lambda: None
        sample = {"name": "Regression", "requirement": "Correct",
                  "prompt": "Manual input", "dims": ["正确性", "完整性"]}
        body = {"run_id": self.run["id"], "step_n": 1,
                "sample": sample, "confirmed": False}
        handler._body = lambda: body
        responses = []
        handler._json = lambda status, value: responses.append((status, value))

        with mock.patch.object(main.store, "get_run", return_value=None):
            Handler._route_post(handler)
        self.assertEqual(responses[-1][0], 404)

        with mock.patch.object(main.store, "get_run", return_value=self.run), \
                mock.patch.object(route_evalbench, "sample_from_failed_run", return_value=(None, "confirmation required")) as create:
            Handler._route_post(handler)

        self.assertEqual(responses[-1][0], 400)
        create.assert_called_once_with(self.run, 1, sample, confirmed=False)


if __name__ == "__main__":
    unittest.main()
