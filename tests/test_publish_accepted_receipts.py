"""Platform acceptance lets the next chapter run without a duplicate upload."""
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock


APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class ImmediateThread:
    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


class PublishAcceptedReceiptTests(unittest.TestCase):
    def run_batch(self, receipt, stale_failure=False, scheduled=False):
        from core import contracts, store
        from core.publish import auto, ledger, manager

        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            accepted = {kind: set() for kind in ("published", "scheduled", "review", "drafted")}
            uploads = []
            items = [{"chapter_no": no, "file": "chapter-%d.txt" % no}
                     for no in (1, 2)]

            def upload(task_id, platform, chapter_path, **kwargs):
                no = int(Path(chapter_path).stem.split("-")[-1])
                uploads.append(no)
                if receipt:
                    accepted[receipt].add(no)
                return True, ""

            patches = [
                mock.patch.object(auto, "_BATCH_JOB_FILE", Path(temp) / "batches.json"),
                mock.patch.object(auto, "_BATCH_JOBS", None),
                mock.patch.object(auto, "_running", {}),
                mock.patch.object(auto, "_cancel_events", {}),
                mock.patch.object(auto, "_BATCH_SLOT_LOCKS", {}),
                mock.patch.object(auto, "calibrated", return_value=True),
                mock.patch.object(auto, "guards", return_value=(True, "")),
                mock.patch.object(auto, "pending", return_value=(items, "")),
                mock.patch.object(auto, "_batch_sleep", return_value=True),
                mock.patch.object(auto, "_wait_idle", return_value=True),
                mock.patch.object(auto.threading, "Thread", ImmediateThread),
                mock.patch.object(store, "get_task", return_value={"id": "receipt-task", "workdir": temp}),
                mock.patch.object(contracts, "publish_allowed", return_value=True),
                mock.patch.object(ledger, "book_for", return_value={"book_id": "123", "title": "测试"}),
                mock.patch.object(ledger, "recent", return_value=([
                    {"chapter_no": 1, "ok": False, "error": "CDP 连接断开"}
                ] if stale_failure else [])),
                mock.patch.object(manager, "_quality_release_guard", return_value={"allowed": True}),
                mock.patch.object(manager, "sync_published", return_value=(True, "")),
                mock.patch.object(manager, "upload_chapter_async", side_effect=upload),
            ]
            for patch in patches:
                stack.enter_context(patch)
            for kind in accepted:
                stack.enter_context(mock.patch.object(
                    ledger, kind + "_chapters",
                    side_effect=lambda *args, kind=kind: accepted[kind]))
            reconcile = stack.enter_context(mock.patch.object(
                auto, "_reconcile_chapter_before_retry",
                return_value=(False, False, "unexpected reconciliation")))

            self.assertEqual(auto.publish_pending_async(
                "receipt-task", "fanqie", scheduled=scheduled), (True, ""))
            state = dict(auto._running["receipt-task"])
            self.assertFalse(auto._batch_slot_lock("fanqie").locked())
            return state, uploads, reconcile.call_count

    def test_accepted_receipts_continue_the_batch(self):
        for receipt, scheduled in (("published", False), ("scheduled", False),
                                   ("scheduled", True), ("review", False)):
            with self.subTest(receipt=receipt, scheduled=scheduled):
                state, uploads, _ = self.run_batch(receipt, scheduled=scheduled)
                self.assertEqual(state["status"], "done", state["error"])
                self.assertEqual(state["done"], 2)
                self.assertEqual(uploads, [1, 2])

    def test_accepted_receipt_ignores_an_older_transient_failure(self):
        for receipt in ("scheduled", "review"):
            with self.subTest(receipt=receipt):
                state, uploads, reconciles = self.run_batch(receipt, stale_failure=True)
                self.assertEqual(state["status"], "done", state["error"])
                self.assertEqual(uploads, [1, 2])
                self.assertEqual(reconciles, 0)

    def test_draft_or_absent_receipt_stops_before_next_chapter(self):
        for receipt in ("drafted", None):
            with self.subTest(receipt=receipt):
                state, uploads, _ = self.run_batch(receipt)
                self.assertEqual(state["status"], "error")
                self.assertEqual(state["done"], 0)
                self.assertEqual(uploads, [1])

    def test_ambiguous_failure_still_requires_reconciliation(self):
        state, uploads, reconciles = self.run_batch(None, stale_failure=True)
        self.assertEqual(state["status"], "error")
        self.assertEqual(uploads, [1])
        self.assertEqual(reconciles, 1)


if __name__ == "__main__":
    unittest.main()
