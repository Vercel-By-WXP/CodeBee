import json
import tempfile
import time
import unittest
from pathlib import Path

from app.core import session_runtime


class SessionRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime = session_runtime.SessionRuntime(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_admit_resume_and_close(self):
        row = self.runtime.create(workspace=self.root / "workspace", owner="ui-1")
        self.assertEqual(row["status"], "idle")
        admitted = self.runtime.admit_turn(row["id"], "hello", route={"model": "m1"})
        self.assertEqual(admitted["turn"], 1)
        self.assertEqual(admitted["route"]["model"], "m1")
        self.runtime.finish_turn(row["id"], "ok")
        self.runtime.lease(row["id"]).release()
        resumed = self.runtime.resume(row["id"], owner="ui-2")
        self.assertEqual(resumed["id"], row["id"])
        self.assertEqual(resumed["status"], "idle")
        self.runtime.close(row["id"])
        self.assertEqual(self.runtime.get(row["id"])["status"], "closed")

    def test_single_inflight_turn_and_workspace_lease_fencing(self):
        row = self.runtime.create(workspace=self.root / "workspace", owner="ui-1")
        self.runtime.admit_turn(row["id"], "one")
        with self.assertRaises(session_runtime.SessionBusyError):
            self.runtime.admit_turn(row["id"], "two")
        self.runtime.finish_turn(row["id"], "done")
        lease = self.runtime.lease(row["id"])
        self.assertTrue(lease.valid())
        with self.assertRaises(RuntimeError):
            self.runtime.lease(row["id"], owner="other")
        self.assertTrue(lease.release())
        self.assertFalse(lease.valid())

    def test_expired_lease_can_be_reclaimed_with_new_epoch(self):
        workspace = self.root / "workspace"
        first = session_runtime.WorkspaceLease.acquire(workspace, "a", ttl=0.01, root=self.root)
        epoch = first.epoch
        time.sleep(0.03)
        second = session_runtime.WorkspaceLease.acquire(workspace, "b", ttl=30, root=self.root)
        self.assertGreater(second.epoch, epoch)
        self.assertFalse(first.valid())
        self.assertTrue(second.valid())

    def test_drain_turns_recovers_aborted_worker(self):
        row = self.runtime.create(workspace=self.root / "workspace", owner="ui-1")
        self.runtime.admit_turn(row["id"], "one")
        recovered = self.runtime.drain_turns(row["id"], "worker aborted")
        self.assertFalse(recovered["inflight"])
        self.assertEqual(recovered["inflight_count"], 0)
        self.assertEqual(recovered["status"], "idle")
        self.runtime.admit_turn(row["id"], "retry")


if __name__ == "__main__":
    unittest.main()
