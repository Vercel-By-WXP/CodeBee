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

    def test_admitted_turn_heartbeats_and_finish_releases_lease(self):
        row = self.runtime.create(workspace=self.root / "workspace", owner="ui-1")
        self.runtime.admit_turn(row["id"], "long task")
        lease = self.runtime._leases[row["id"]]
        self.assertTrue(lease.heartbeat_running())
        self.runtime.finish_turn(row["id"], "done")
        self.assertFalse(lease.valid())
        self.assertFalse(lease.heartbeat_running())

    def test_heartbeat_renews_before_short_ttl_expires(self):
        lease = session_runtime.WorkspaceLease.acquire(
            self.root / "heartbeat-workspace", "owner", ttl=0.2, root=self.root)
        lease.start_heartbeat(ttl=0.2, interval=0.1)
        time.sleep(0.35)
        self.assertTrue(lease.valid())
        lease.release()

    def test_drain_releases_lease_and_next_turn_reacquires(self):
        row = self.runtime.create(workspace=self.root / "workspace", owner="ui-1")
        self.runtime.admit_turn(row["id"], "aborted")
        old = self.runtime._leases[row["id"]]
        self.runtime.drain_turns(row["id"], "aborted")
        self.assertFalse(old.valid())
        self.runtime.admit_turn(row["id"], "retry")
        self.assertTrue(self.runtime._leases[row["id"]].valid())

    def test_stale_finish_and_drain_cannot_touch_resumed_owner(self):
        row = self.runtime.create(workspace=self.root / "workspace", owner="ui-1")
        old = self.runtime.admit_turn(row["id"], "old")
        resumed = self.runtime.resume(row["id"], owner="ui-2")
        current = self.runtime.admit_turn(row["id"], "new")
        self.runtime.finish_turn(row["id"], "late old", admission=old)
        after_finish = self.runtime.get(row["id"])
        self.assertEqual(after_finish["inflight_count"], 1)
        self.assertIn(current["token"], after_finish["active_turns"])
        self.runtime.drain_turns(row["id"], "late drain",
                                 admissions=[old], owner="ui-1",
                                 owner_epoch=old["owner_epoch"])
        after_drain = self.runtime.get(row["id"])
        self.assertEqual(after_drain["owner"], "ui-2")
        self.assertEqual(after_drain["inflight_count"], 1)
        self.assertEqual(after_drain["owner_epoch"], current["owner_epoch"])

    def test_scoped_drain_preserves_turn_admitted_after_snapshot(self):
        row = self.runtime.create(workspace=self.root / "workspace", owner="ui-1")
        first = self.runtime.admit_turn(row["id"], "first", allow_parallel=True)
        second = self.runtime.admit_turn(row["id"], "second", allow_parallel=True)
        drained = self.runtime.drain_turns(
            row["id"], "first aborted", admissions=[first],
            owner="ui-1", owner_epoch=first["owner_epoch"])
        self.assertTrue(drained["inflight"])
        self.assertEqual(drained["inflight_count"], 1)
        self.assertEqual(drained["active_turns"], [second["token"]])

    def test_heartbeat_renew_error_stops_thread_cleanly(self):
        lease = session_runtime.WorkspaceLease.acquire(
            self.root / "heartbeat-error", "owner", ttl=1, root=self.root)
        original = lease.renew
        lease.renew = lambda ttl=90.0: (_ for _ in ()).throw(OSError("read failed"))
        try:
            lease.start_heartbeat(ttl=1, interval=0.01)
            deadline = time.time() + 1
            while lease.heartbeat_running() and time.time() < deadline:
                time.sleep(0.01)
            self.assertFalse(lease.heartbeat_running())
        finally:
            lease.renew = original
            lease.release()

    def test_close_rejects_late_finish_and_drain(self):
        row = self.runtime.create(workspace=self.root / "workspace", owner="ui-1")
        admission = self.runtime.admit_turn(row["id"], "close me")
        self.runtime.close(row["id"])
        closed = self.runtime.finish_turn(row["id"], "late", admission=admission)
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(closed["inflight_count"], 0)
        drained = self.runtime.drain_turns(
            row["id"], "late drain", admissions=[admission],
            owner="ui-1", owner_epoch=admission["owner_epoch"])
        self.assertEqual(drained["status"], "closed")

    def test_startup_removes_expired_lease_files(self):
        workspace = self.root / "old-workspace"
        old = session_runtime.WorkspaceLease.acquire(
            workspace, "old", ttl=0.01, root=self.root / "leases")
        time.sleep(0.03)
        self.assertTrue(old.path.exists())
        session_runtime.SessionRuntime(self.root)
        self.assertFalse(old.path.exists())


if __name__ == "__main__":
    unittest.main()
