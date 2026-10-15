import tempfile
import unittest
from pathlib import Path

from app.core import provenance, session_snapshot


class ProvenanceSnapshotTests(unittest.TestCase):
    def test_provenance_records_inputs_commands_and_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "result.txt"
            out.write_text("hello", encoding="utf-8")
            row = provenance.build(run_id="r1", workdir=root, command=["pytest", "-q"],
                                   inputs=[root / "input.txt"], outputs=[out],
                                   verification=["pytest -q"])
            self.assertEqual(row["run_id"], "r1")
            self.assertEqual(row["outputs"][0]["sha256"], provenance.file_digest(out))
            self.assertEqual(row["verification"][0], "pytest -q")

    def test_session_snapshot_roundtrip_and_workspace_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("a", encoding="utf-8")
            snapshot = session_snapshot.capture(root, {"messages": [{"role": "user", "content": "hi"}]})
            (root / "a.txt").write_text("changed", encoding="utf-8")
            (root / "b.txt").write_text("b", encoding="utf-8")
            diff = session_snapshot.compare_workspace(snapshot, root)
            self.assertIn("a.txt", diff["modified"])
            self.assertIn("b.txt", diff["added"])
            restored = session_snapshot.restore(snapshot)
            self.assertEqual(restored["messages"][0]["content"], "hi")

    def test_from_run_projects_command_verification_and_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "result.txt"
            out.write_text("done", encoding="utf-8")
            row = provenance.from_run({
                "id": "r2", "source_revision": "abc",
                "steps": [{"command": ["tool", "--api-key", "<redacted>"],
                            "verify_command": "python -m unittest"}],
                "assets": [{"path": "result.txt"}],
            }, {}, workdir=root)
            self.assertEqual(row["command"][0], "tool")
            self.assertEqual(row["verification"], ["python -m unittest"])
            self.assertEqual(row["outputs"][0]["sha256"], provenance.file_digest(out))

    def test_snapshot_replay_reconstructs_messages_and_honors_cutoff(self):
        snapshot = {
            "schema": 1, "workspace": "/tmp/work", "replace_generation": 1,
            "session": {"messages": []},
            "events": [
                {"seq": 1, "type": "user_message", "data": {"content": "one"}},
                {"seq": 2, "type": "assistant_message", "data": {"content": "two"}},
                {"seq": 3, "type": "user_message", "data": {"content": "three"},
                 "surface_op": {"op": "replace", "start_seq": 1, "end_seq": 2}},
            ],
        }
        replayed = session_snapshot.replay(snapshot)
        self.assertEqual([m["content"] for m in replayed["messages"]], ["three"])
        self.assertEqual(session_snapshot.replay(snapshot, upto_seq=2)["event_count"], 2)


if __name__ == "__main__":
    unittest.main()
