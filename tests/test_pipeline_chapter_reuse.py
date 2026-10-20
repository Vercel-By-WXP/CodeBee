import unittest
from unittest import mock

from app.core.pipeline import (
    _global_review_count,
    _checkpoint_after_reassessment,
    _invalidate_checkpoint_after_polish,
    _prefix_word_count,
    _reusable_chapter_review,
    _signing_checkpoint_due,
)


class ReusableChapterReviewTests(unittest.TestCase):
    def setUp(self):
        self.score = {
            "means": {"情节": 8.0, "人物": 8.0},
            "passed": True,
            "reviewer_count": 2,
            "reviewer_consensus": True,
        }

    def test_reuses_current_rubric_and_threshold_evidence(self):
        self.assertTrue(_reusable_chapter_review(
            self.score, ["情节", "人物"], 7.5))

    def test_rejects_different_rubric(self):
        self.assertFalse(_reusable_chapter_review(
            self.score, ["情节", "节奏"], 7.5))

    def test_rejects_score_below_current_threshold(self):
        self.assertFalse(_reusable_chapter_review(
            self.score, ["情节", "人物"], 8.5))

    def test_rejects_fractional_or_boolean_reviewer_counts(self):
        for count in (2.5, True):
            with self.subTest(count=count):
                score = dict(self.score, reviewer_count=count)
                self.assertFalse(_reusable_chapter_review(
                    score, ["情节", "人物"], 7.5))

    def test_rejects_non_numeric_or_out_of_range_means(self):
        for invalid in (True, "8", float("nan"), 10.1):
            with self.subTest(invalid=invalid):
                score = dict(self.score, means={"情节": invalid, "人物": 8.0})
                self.assertFalse(_reusable_chapter_review(
                    score, ["情节", "人物"], 7.5))


class SigningCheckpointAfterPolishTests(unittest.TestCase):
    def test_polished_chapter_inside_checkpoint_scope_invalidates_checkpoint(self):
        checkpoint = {"chapter": 5, "passed": True, "summary": "评估通过"}
        result = _invalidate_checkpoint_after_polish(checkpoint, 3)
        self.assertFalse(result["passed"])
        self.assertTrue(result["stale_after_polish"])
        self.assertTrue(checkpoint["passed"])

    def test_polished_chapter_after_checkpoint_keeps_checkpoint_valid(self):
        checkpoint = {"chapter": 3, "passed": True}
        self.assertEqual(_invalidate_checkpoint_after_polish(checkpoint, 5), checkpoint)

    def test_stale_checkpoint_is_due_using_cumulative_manuscript_words(self):
        stale = {"chapter": 6, "at_words": 24000, "stale_after_polish": True}
        self.assertTrue(_signing_checkpoint_due(25000, stale, 20000))

    def test_valid_checkpoint_is_not_repeated(self):
        valid = {"chapter": 6, "at_words": 24000, "passed": True}
        self.assertFalse(_signing_checkpoint_due(25000, valid, 20000))

    def test_unevaluated_checkpoint_is_retried(self):
        unevaluated = {"chapter": 6, "at_words": 24000, "passed": None}
        self.assertTrue(_signing_checkpoint_due(25000, unevaluated, 20000))

    def test_failed_reassessment_keeps_checkpoint_stale_and_due(self):
        stale = _checkpoint_after_reassessment(
            {"passed": False}, 26000, None, {}, "", 7.0)
        self.assertIsNone(stale["passed"])
        self.assertTrue(stale["stale_after_polish"])
        self.assertTrue(_signing_checkpoint_due(26000, stale, 20000))

    def test_successful_reassessment_refreshes_word_count_and_clears_stale(self):
        fresh = _checkpoint_after_reassessment(
            {"stale_after_polish": True}, 27000,
            {"剧情": 8.0}, {"reviewer_count": 2, "consensus": True},
            "已复核", 7.0)
        self.assertEqual(fresh["at_words"], 27000)
        self.assertTrue(fresh["passed"])
        self.assertNotIn("stale_after_polish", fresh)

    def test_checkpoint_does_not_run_before_word_threshold(self):
        stale = {"chapter": 6, "stale_after_polish": True}
        self.assertFalse(_signing_checkpoint_due(19999, stale, 20000))

    def test_prefix_word_count_includes_chapters_from_prior_runs(self):
        with mock.patch("app.core.pipeline._read_chapter",
                        side_effect=lambda _workdir, chapter: {
                            1: "前文 一",
                            2: "本批 二三",
                            3: "",
                        }.get(chapter, "")):
            self.assertEqual(_prefix_word_count("unused", 3), 7)

    def test_prefix_word_count_uses_inherited_length_for_archived_chapter(self):
        inherited = {2: {"chapter": 2, "words": 18000}}
        with mock.patch("app.core.pipeline._read_chapter",
                        side_effect=lambda _workdir, chapter: {
                            1: "前文 一",
                            2: "",
                        }.get(chapter, "")):
            self.assertEqual(_prefix_word_count("unused", 2, inherited), 18003)


class GlobalReviewFallbackTests(unittest.TestCase):
    def test_counts_only_unique_complete_reviews(self):
        reviews = [
            {"id": "a", "scores": {"情节": 8.0, "人物": 8.0}},
            {"id": "a", "scores": {"情节": 9.0, "人物": 9.0}},
            {"id": "b", "scores": {"情节": 8.0}},
        ]
        self.assertEqual(_global_review_count(reviews, ["情节", "人物"]), 1)


if __name__ == "__main__":
    unittest.main()
