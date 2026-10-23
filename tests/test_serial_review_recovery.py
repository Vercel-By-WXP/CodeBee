"""Final serial review evidence excludes stale and non-consensus scores."""
import unittest

from app.core import pipeline


class SerialReviewRecoveryTests(unittest.TestCase):
    def test_single_global_reviewer_score_is_provisional(self):
        state = pipeline._finalize_serial_review_evidence(
            [{"chapter": 1, "means": {"质量": 8.0},
              "review_status": "evaluated", "reviewer_count": 2,
              "reviewer_consensus": True, "review_degraded": False}],
            {"means": {"质量": 9.0}, "reviewer_count": 1,
             "consensus": False},
        )

        self.assertTrue(state["review_degraded"])
        self.assertEqual(state["global_scores"], {})
        self.assertEqual(state["provisional_global_scores"], {"质量": 9.0})
        self.assertEqual(state["chapter_scores"][0]["means"], {"质量": 8.0})
        self.assertTrue(any("全局评审" in reason for reason in state["reasons"]))

    def test_successful_final_reassessment_does_not_keep_old_failure_reasons(self):
        state = pipeline._finalize_serial_review_evidence(
            [{"chapter": 1, "means": {"质量": 8.0},
              "review_status": "evaluated", "reviewer_count": 2,
              "reviewer_consensus": True, "review_degraded": False}],
            {"means": {"质量": 8.0}, "reviewer_count": 2,
             "consensus": True},
        )

        self.assertFalse(state["review_degraded"])
        self.assertEqual(state["reasons"], [])
        self.assertEqual(state["global_scores"], {"质量": 8.0})


if __name__ == "__main__":
    unittest.main()
