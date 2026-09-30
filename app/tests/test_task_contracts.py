import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import contracts


class TaskContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name)
        self.patch = patch.object(contracts, "_DIR", self.data_dir)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_create_normalizes_contract_and_records_criteria(self):
        item = contracts.create("task-1", {
            "acceptance_criteria": [" tests pass ", "", "tests pass", "output exists"],
            "approval_required": True,
        })
        self.assertEqual(item["acceptance_criteria"], ["tests pass", "output exists"])
        self.assertTrue(item["approval_required"])
        self.assertEqual(item["status"], "open")

    def test_evidence_and_approval_are_auditable(self):
        contracts.create("task-1", {"acceptance_criteria": ["tests pass"],
                                     "approval_required": True})
        contracts.add_evidence("task-1", {"criterion": "tests pass", "status": "passed",
                                            "summary": "pytest: 12 passed"}, actor="local")
        self.assertFalse(contracts.release_allowed("task-1"))
        approved, error = contracts.approve("task-1", actor="local", note="checked")
        self.assertIsNone(error)
        self.assertEqual(approved["approval"]["actor"], "local")
        self.assertTrue(contracts.release_allowed("task-1"))

    def test_partial_update_preserves_acceptance_criteria(self):
        contracts.create("task-partial", {"acceptance_criteria": ["build"],
                                          "approval_required": True})
        item = contracts.create("task-partial", {"actor": "ui"})
        self.assertEqual(item["acceptance_criteria"], ["build"])
        self.assertTrue(item["approval_required"])

    def test_reject_and_blocked_need_reason(self):
        contracts.create("task-2", {})
        result, error = contracts.set_blocked("task-2", "", actor="local")
        self.assertIsNone(result)
        self.assertIn("原因", error)
        result, error = contracts.set_blocked("task-2", "依赖不可用", actor="local")
        self.assertIsNone(error)
        self.assertEqual(result["blocked_reason"], "依赖不可用")
        rejected, error = contracts.reject("task-2", actor="local", note="证据不足")
        self.assertIsNone(error)
        self.assertEqual(rejected["approval"]["status"], "rejected")

    def test_completion_receipt_requires_terminal_run_and_lists_evidence(self):
        contracts.create("task-3", {"acceptance_criteria": ["build"]})
        contracts.add_evidence("task-3", {"criterion": "build", "status": "passed"})
        receipt, error = contracts.complete("task-3", {"id": "run-1", "status": "running"})
        self.assertIsNone(receipt)
        self.assertIn("终态", error)
        receipt, error = contracts.complete("task-3", {"id": "run-1", "status": "done"})
        self.assertIsNone(error)
        self.assertEqual(receipt["run_id"], "run-1")
        self.assertEqual(receipt["criteria_passed"], 1)

    def test_completion_receipt_is_idempotent_for_same_run(self):
        contracts.create("task-idempotent", {"acceptance_criteria": ["build"]})
        first, error = contracts.complete("task-idempotent", {
            "id": "run-1", "status": "done", "tokens": 10, "cost_usd": 0.1})
        second, error = contracts.complete("task-idempotent", {
            "id": "run-1", "status": "done", "tokens": 99, "cost_usd": 9.9})
        self.assertIsNone(error)
        self.assertEqual(first, second)
        self.assertEqual(second["tokens"], 10)

    def test_evidence_ids_are_unique_when_many_are_recorded(self):
        contracts.create("task-many")
        ids = [contracts.add_evidence("task-many", {"criterion": str(i), "status": "passed"})["evidence"][-1]["id"]
                for i in range(305)]
        self.assertEqual(len(ids), len(set(ids)))

    def test_task_approval_bit_cannot_be_downgraded_by_sidecar(self):
        contracts.create("task-approval", {"approval_required": False})
        with patch("core.store.get_task", return_value={"approval_required": True}):
            self.assertFalse(contracts.release_allowed("task-approval"))

    def test_contract_update_rejects_string_approval_flag(self):
        with self.assertRaises(ValueError):
            contracts.create("task-bad-approval", {"approval_required": "false"})

    def test_acceptance_matrix_links_matching_user_criteria_only(self):
        contracts.create("task-matrix", {"acceptance_criteria": ["测试全部通过", "安全检查完成", "产品文案符合品牌语气"]})
        contracts.record_acceptance_cases("task-matrix", [
            {"case": "correctness", "status": "passed", "reason": "验证通过"},
            {"case": "security", "status": "failed", "reason": "缺少鉴权证据"},
        ])
        item = contracts.get("task-matrix")
        latest = {}
        for row in item["evidence"]:
            latest[row["criterion"]] = row
        self.assertEqual(latest["测试全部通过"]["status"], "passed")
        self.assertEqual(latest["安全检查完成"]["status"], "failed")
        self.assertNotIn("产品文案符合品牌语气", latest)


if __name__ == "__main__":
    unittest.main()
