"""发布进度、逐章审稿和实际放行状态的回归；数据全部隔离。"""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import paths, store
from app.core.publish import auto, ledger, manager


class PublishReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="publish-readiness-")
        self.root = Path(self.tmp.name)
        self.wd = self.root / "work"
        self.wd.mkdir()
        for n in [104, 105, 106, 108]:
            (self.wd / ("chapter-%d.md" % n)).write_text("正文", encoding="utf8")
        self.task = {"id": "child", "type": "serial_novel", "workdir": str(self.wd),
                     "serial": {"continues": "root"}}
        self.patches = [patch.object(paths, "PUBLISH_DIR", self.root / "publish"),
                        patch.object(ledger, "_BOOKS_FILE", self.root / "publish/books.json"),
                        patch.object(store, "_TASKS", {"child": self.task,
                            "root": {"id": "root", "type": "serial_novel", "serial": {}}}),
                        patch.object(store, "_RUNS", {}),
                        patch.object(auto, "guards", return_value=(True, "")),
                        patch.object(auto, "calibrated", return_value=True)]
        for p in self.patches:
            p.start()
        ledger.save_book("root", "fanqie", {"book_id": "b1", "title": "测试书"})
        ledger.update_book("root", "fanqie", confirmed_published_through=104,
                           remote_total=48, remote_published_nos=list(range(1, 49)))

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def review(self, n=105, **overrides):
        score = {"chapter": n, "passed": True, "reviewer_count": 2, "reviewer_consensus": True}
        score.update(overrides)
        return score

    def seed_review(self, scores, run_id="r-2", **overrides):
        verdict = {"publishable": True, "global_pass": True,
                   "global_reviewer_count": 2, "global_reviewer_consensus": True,
                   "chapter_scores": scores, "end_chapter": 106, "major_issues": []}
        verdict.update(overrides)
        store._RUNS[run_id] = {"id": run_id, "task_id": "child", "status": "done",
            "verdict": verdict, "ended_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    def view(self):
        return auto.status("child")["books"][0]

    def test_confirmed_104_survives_partial_platform_sync_and_is_shared(self):
        self.assertEqual(auto.pending("child", "fanqie")[0][0]["chapter_no"], 105)
        self.assertIn(104, ledger.published_chapters("root", "fanqie"))
        self.assertNotIn(105, ledger.published_chapters("child", "fanqie"))
        self.assertNotIn(104, ledger.published_chapters("child", "qimao"))
        self.assertEqual(self.view()["published_latest"], 104)

    def test_platform_count_alone_is_not_a_chapter_boundary(self):
        ledger.update_book("root", "fanqie", confirmed_published_through=None,
                           remote_published_nos=None, remote_total=104)
        self.assertEqual(auto.pending("child", "fanqie")[0][0]["chapter_no"], 104)

    def test_missing_review_is_not_ready(self):
        view = self.view()
        self.assertEqual(view["ready_count"], 0)
        self.assertEqual(view["items"][0]["review_status"], "pending")

    def test_valid_review_and_actual_gate_make_chapter_ready(self):
        self.seed_review([self.review()])
        view = self.view()
        self.assertEqual(view["items"][0]["review_status"], "ready")
        self.assertEqual(view["ready_count"], 1)
        self.assertFalse(view["items"][1]["publish_ready"])

    def test_passed_chapter_with_major_issue_is_blocked(self):
        self.seed_review([self.review()], major_issues=[{"severity": "major"}])
        view = self.view()
        self.assertEqual(view["items"][0]["review_status"], "passed_blocked")
        self.assertEqual(view["review_passed_count"], 1)
        self.assertEqual(view["ready_count"], 0)

    def test_uncalibrated_flow_is_passed_but_not_direct_ready(self):
        self.seed_review([self.review()])
        with patch.object(auto, "calibrated", return_value=False):
            view = self.view()
        self.assertTrue(view["guard_ok"])
        self.assertEqual(view["items"][0]["review_status"], "passed_blocked")
        self.assertIn("尚未校准", view["items"][0]["review_reason"])
        self.assertEqual(view["review_passed_count"], 1)
        self.assertEqual(view["ready_count"], 0)

    def test_invalid_review_boundary_never_marks_ready(self):
        items = [{"chapter_no": 105, "file": "chapter-105.md"}]
        self.seed_review([self.review()])
        auto.chapter_review_states(self.task, items,
            {"allowed": True, "reviewed_through": "unknown"}, True)
        self.assertFalse(items[0]["publish_ready"])

    def test_latest_failed_review_replaces_earlier_pass(self):
        self.seed_review([self.review()], "r-1")
        self.seed_review([self.review(passed=False)], "r-2", publishable=False)
        self.assertEqual(self.view()["items"][0]["review_status"], "failed")

    def test_single_reviewer_is_not_passed(self):
        self.seed_review([self.review(reviewer_count=1)])
        item = self.view()["items"][0]
        self.assertEqual(item["review_status"], "pending")
        self.assertFalse(item["review_passed"])

    def test_changed_manuscript_requires_new_review(self):
        self.seed_review([self.review()])
        os.utime(self.wd / "chapter-105.md", (time.time() + 10, time.time() + 10))
        self.assertEqual(self.view()["items"][0]["review_status"], "stale")

    def test_drafts_are_listed_but_never_reuploaded(self):
        ledger.record("fanqie", "upload_chapter_draft", task_id="child", chapter_no=106)
        view = self.view()
        self.assertEqual(view["drafted_nos"], [106])
        self.assertNotIn(106, [p["chapter_no"] for p in view["items"]])

    def test_binding_another_book_clears_old_progress(self):
        ok, err = manager.register_book("child", "fanqie", "另一书", "b2")
        self.assertTrue(ok, err)
        self.assertNotIn("confirmed_published_through", ledger.book_for("child", "fanqie"))
        self.assertNotIn("remote_published_nos", ledger.book_for("child", "fanqie"))


if __name__ == "__main__":
    unittest.main()
