import unittest
import json
from unittest.mock import patch

from base import BaseTest

from app.core.acceptance import evaluate_run


def _cases(result):
    return {item["case"]: item for item in result["cases"]}


class AcceptancePerformanceGateTests(unittest.TestCase):
    def test_measured_performance_without_threshold_is_not_evaluated(self):
        result = evaluate_run(
            {"status": "done", "duration_s": 12, "cost_usd": 0.3},
            {},
        )

        performance = _cases(result)["performance"]
        self.assertEqual(performance["status"], "not_evaluated")
        self.assertEqual(performance["evidence"][0]["measured"]["duration_s"], 12.0)
        self.assertIn("未声明性能阈值", performance["reason"])

    def test_performance_thresholds_pass_and_fail_with_explicit_evidence(self):
        passed = evaluate_run(
            {"status": "done", "duration_s": 12, "cost_usd": 0.3,
             "tokens_per_sec": 20},
            {"acceptance_thresholds": {"max_duration_s": 15,
                                        "max_cost_usd": 0.5,
                                        "min_tokens_per_sec": 10}},
        )
        pcase = _cases(passed)["performance"]
        self.assertEqual(pcase["status"], "passed")
        self.assertTrue(all(item["passed"] for item in pcase["evidence"][0]["checks"]))

        failed = evaluate_run(
            {"status": "done", "duration_s": 18, "cost_usd": 0.3,
             "tokens_per_sec": 20},
            {"acceptance_thresholds": {"max_duration_s": 15}},
        )
        fcase = _cases(failed)["performance"]
        self.assertEqual(fcase["status"], "failed")
        self.assertFalse(fcase["evidence"][0]["checks"][0]["passed"])

    def test_missing_measurement_for_declared_threshold_stays_unverified(self):
        result = evaluate_run(
            {"status": "done", "duration_s": 12},
            {"acceptance_thresholds": {"min_tokens_per_sec": 10}},
        )

        performance = _cases(result)["performance"]
        self.assertEqual(performance["status"], "not_evaluated")
        self.assertIn("缺少实测值", performance["reason"])

    def test_failure_wins_over_missing_measurement_and_keeps_all_checks(self):
        case = _cases(evaluate_run(
            {"duration_s": 20}, {"acceptance_thresholds": {
                "max_duration_s": 10, "max_first_token_ms": 100}}))["performance"]
        self.assertEqual(case["status"], "failed")
        self.assertEqual(len(case["evidence"][0]["checks"]), 2)

    def test_placeholder_cost_and_invalid_measurements_cannot_pass(self):
        for value in (0, None, True, float("nan"), float("inf"), -1):
            with self.subTest(value=value):
                case = _cases(evaluate_run({"cost_usd": value}, {
                    "acceptance_thresholds": {"max_cost_usd": 1}}))["performance"]
                self.assertEqual(case["status"], "not_evaluated")

    def test_invalid_legacy_thresholds_cannot_partially_pass(self):
        for invalid in (True, "10", float("nan"), float("inf"), -1):
            with self.subTest(invalid=invalid):
                case = _cases(evaluate_run({"duration_s": 5, "cost_usd": 0.1}, {
                    "acceptance_thresholds": {"max_duration_s": 10,
                                               "max_cost_usd": invalid}}))["performance"]
                self.assertEqual(case["status"], "not_evaluated")

    def test_zero_and_equal_measured_boundaries_are_valid(self):
        case = _cases(evaluate_run({"duration_s": 0, "first_token_ms": 100,
                                    "tokens_per_sec": 20}, {"acceptance_thresholds": {
            "max_duration_s": 0, "max_first_token_ms": 100,
            "min_tokens_per_sec": 20}}))["performance"]
        self.assertEqual(case["status"], "passed")

    def test_invalid_timestamps_and_unknown_thresholds_stay_unverified(self):
        for start, end in (("bad", "bad"), ("2026-10-15 00:00:10", "2026-10-15 00:00:00"),
                           ("23:59:00", "00:01:00")):
            with self.subTest(start=start):
                case = _cases(evaluate_run({"started_at": start, "ended_at": end}, {
                    "acceptance_thresholds": {"max_duration_s": 20}}))["performance"]
                self.assertEqual(case["status"], "not_evaluated")
        case = _cases(evaluate_run({"duration_s": 1}, {
            "acceptance_thresholds": {"max_duration_s": 10, "unknown": 10}}))["performance"]
        self.assertEqual(case["status"], "not_evaluated")


class PerformanceGatePersistenceTests(BaseTest):
    def setUp(self):
        super().setUp()
        from app.core import contracts, store, hooks
        self.store, self.contracts = store, contracts
        for obj, name, value in ((contracts, "_DIR", self.data_dir / "contracts"),
                                 (hooks, "run_event", lambda *a, **k: None)):
            p = patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    def create(self, **extra):
        return self.store.create_task({"type": "code", "goal": "Build report",
                                       "workdir": str(self.workdir), **extra})

    def test_create_rejects_invalid_thresholds_before_persistence(self):
        for thresholds in (None, [], {"unknown": 1}, {"max_duration_s": True},
                           {"max_duration_s": "10"}, {"max_duration_s": -1},
                           {"max_duration_s": float("nan")},
                           {"max_duration_s": float("inf")}):
            with self.subTest(thresholds=thresholds):
                with self.assertRaisesRegex(ValueError, "acceptance_thresholds"):
                    self.create(acceptance_thresholds=thresholds)
                self.assertEqual(self.store.list_tasks(), [])
                self.assertEqual(list(self._paths.TASKS_DIR.glob("*.json")), [])

    def test_threshold_update_is_validated_before_other_mutations_and_cas(self):
        task = self.create(acceptance_thresholds={"max_duration_s": 10})
        before = json.loads(json.dumps(task))
        ok, error = self.store.update_task_params(task["id"], {
            "mode": "manual", "acceptance_thresholds": {"max_cost_usd": True}})
        self.assertFalse(ok)
        self.assertIn("acceptance_thresholds", error)
        self.assertEqual(self.store.get_task(task["id"]), before)
        ok, _ = self.store.update_task_params(task["id"], {
            "expected_rev": task["rev"], "acceptance_thresholds": {"max_duration_s": 20}})
        self.assertTrue(ok)
        self.assertEqual(task["rev"], before["rev"] + 1)
        ok, _ = self.store.update_task_params(task["id"], {
            "expected_rev": before["rev"], "acceptance_thresholds": {}})
        self.assertFalse(ok)
        self.assertEqual(task["acceptance_thresholds"], {"max_duration_s": 20})
        ok, _ = self.store.update_task_params(task["id"], {"acceptance_thresholds": {}})
        self.assertTrue(ok)
        self.assertEqual(task["acceptance_thresholds"], {})

    def test_run_snapshot_survives_task_edit_and_new_run_uses_new_threshold(self):
        task = self.create(acceptance_thresholds={"max_duration_s": 10})
        run = self.store.create_run("orchestration", "Build", task_id=task["id"])
        self.store.update_run(run["id"], status="done", duration_s=15)
        self.store.update_task_params(task["id"], {"acceptance_thresholds": {"max_duration_s": 20}})
        self.assertEqual(_cases(evaluate_run(run, task))["performance"]["status"], "failed")
        ok, error, newer = self.store.retry_task(task["id"])
        self.assertTrue(ok, error)
        newer["duration_s"] = 15
        self.assertEqual(_cases(evaluate_run(newer, task))["performance"]["status"], "passed")
        self.assertEqual(run["acceptance_thresholds"], {"max_duration_s": 10})
        persisted = json.loads((self._paths.RUNS_DIR / run["id"] / "run.json").read_text(encoding="utf-8"))
        persisted["duration_s"] = 15
        self.assertEqual(_cases(evaluate_run(persisted, task))["performance"]["status"], "failed")

    def test_empty_snapshot_cannot_inherit_later_task_threshold(self):
        task = self.create()
        run = self.store.create_run("orchestration", "Build", task_id=task["id"])
        self.store.update_task_params(task["id"], {"acceptance_thresholds": {"max_duration_s": 20}})
        run["duration_s"] = 15
        self.assertEqual(_cases(evaluate_run(run, task))["performance"]["status"], "not_evaluated")

    def test_running_task_rejects_gate_updates(self):
        task = self.create(acceptance_thresholds={"max_duration_s": 10})
        self.store.update_task_status(task["id"], "running")
        ok, _ = self.store.update_task_params(task["id"], {"acceptance_thresholds": {}})
        self.assertFalse(ok)
        self.assertEqual(task["acceptance_thresholds"], {"max_duration_s": 10})

    def test_terminal_run_persists_wall_clock_result_and_contract_evidence(self):
        task = self.create(acceptance_thresholds={"max_duration_s": 10},
                           acceptance_criteria=["acceptance:performance"])
        run = self.store.create_run("orchestration", "Build", task_id=task["id"])
        self.store.update_run(run["id"], status="done", started_at="2026-10-14 23:59:55",
                              ended_at="2026-10-15 00:00:10")
        persisted = json.loads((self._paths.RUNS_DIR / run["id"] / "run.json").read_text(encoding="utf-8"))
        case = _cases(persisted["acceptance"])["performance"]
        self.assertEqual(case["status"], "failed")
        self.assertEqual(case["evidence"][0]["measured"]["duration_s"], 15)
        contract = self.contracts.get(task["id"])
        self.assertTrue(any(e["criterion"] == "acceptance:performance" and
                            e["status"] == "failed" for e in contract["evidence"]))
        self.assertEqual(contract["completion_receipt"]["run_id"], run["id"])

    def handler(self, path, body):
        import main
        from core import prefs
        p = patch.object(main, "store", self.store)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(prefs, "record", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)
        h = main.Handler.__new__(main.Handler)
        h.path, h.headers = path, {}
        h._authed = lambda: True
        h._deny_control = lambda: None
        h._body = lambda: body
        h._json = lambda code, obj: (code, obj)
        h._client_name = lambda: "test"
        h._enqueue_run = lambda *a, **k: (True, "")
        return h

    def test_http_create_and_params_preserve_gate_and_reject_invalid_values(self):
        h = self.handler("/api/tasks", {"type": "code", "goal": "Build report",
            "workdir": str(self.workdir), "acceptance_thresholds": {"max_duration_s": 30}})
        status, result = h._api_create_task()
        self.assertEqual(status, 200)
        run = self.store.get_run(result["run_id"])
        self.assertEqual(run["acceptance_thresholds"], {"max_duration_s": 30})
        self.store.update_run(run["id"], status="done")
        h = self.handler("/api/tasks/%s/params" % result["task_id"], {
            "acceptance_thresholds": {"max_cost_usd": 0.5}})
        self.assertEqual(h._route_post()[0], 200)
        self.assertEqual(self.store.get_task(result["task_id"])["acceptance_thresholds"],
                         {"max_cost_usd": 0.5})
        h._body = lambda: {"acceptance_thresholds": {"max_cost_usd": False}}
        self.assertEqual(h._route_post()[0], 400)
        h = self.handler("/api/tasks", {"type": "code", "goal": "Build report",
            "workdir": str(self.workdir), "acceptance_thresholds": {"max_duration_s": "30"}})
        self.assertEqual(h._api_create_task()[0], 400)
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_openapi_declares_strict_numeric_thresholds(self):
        from app.core import openapi
        schemas = openapi.document()["components"]["schemas"]
        self.assertEqual(schemas["TaskCreate"]["properties"]["acceptance_thresholds"],
                         {"$ref": "#/components/schemas/PerformanceThresholds"})
        gate = schemas["PerformanceThresholds"]
        self.assertFalse(gate["additionalProperties"])
        self.assertEqual(gate["properties"]["max_duration_s"], {"type": "number", "minimum": 0})


if __name__ == "__main__":
    unittest.main()
