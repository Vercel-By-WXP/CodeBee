import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app.core import audit


class AuditChainTests(unittest.TestCase):
    def test_records_are_hash_chained_and_tamper_detected(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(audit.paths, "DATA_DIR", Path(root)):
                audit.record("unit", {"run_id": "r1", "action": "read"})
                audit.record("unit", {"run_id": "r1", "action": "write"})
                self.assertEqual(audit.verify("unit")["ok"], True)
                path = Path(root) / "audit" / ("unit-" + time.strftime("%Y-%m") + ".jsonl")
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                rows[0]["action"] = "tampered"
                path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
                result = audit.verify("unit")
                self.assertFalse(result["ok"])
                self.assertEqual(result["broken_at"], 1)

    def test_missing_tail_hash_is_detected(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(audit.paths, "DATA_DIR", Path(root)):
                audit.record("unit", {"action": "write"})
                path = Path(root) / "audit" / ("unit-" + time.strftime("%Y-%m") + ".jsonl")
                row = json.loads(path.read_text(encoding="utf-8"))
                row.pop("hash")
                path.write_text(json.dumps(row) + "\n", encoding="utf-8")
                result = audit.verify("unit")
                self.assertFalse(result["ok"])
                self.assertEqual(result["broken_at"], 1)

    def test_legacy_rows_can_be_followed_by_a_new_chain(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(audit.paths, "DATA_DIR", Path(root)):
                path = Path(root) / "audit" / ("unit-" + time.strftime("%Y-%m") + ".jsonl")
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({"event": "unit", "action": "old"}) + "\n",
                                encoding="utf-8")
                audit.record("unit", {"action": "new"})
                self.assertTrue(audit.verify("unit")["ok"])


if __name__ == "__main__":
    unittest.main()
