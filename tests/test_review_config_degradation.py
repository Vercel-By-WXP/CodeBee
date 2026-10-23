"""Incomplete review evidence must stay provisional and explain its state."""
import unittest

from app.core import review_config


class ReviewConfigDegradationTests(unittest.TestCase):
    def setUp(self):
        self.policy = {
            "dimensions": [{"id": "quality", "name": "质量", "weight": 1}],
            "min_score": 7.0,
            "min_model_family": "",
            "min_confidence": 0.0,
        }

    def test_degraded_score_is_provisional_and_not_reported_as_below_threshold(self):
        verdict = review_config.assess({
            "overall": 8.0,
            "scores": {"quality": 8.0},
            "review_degraded": True,
            "review_degradation_reasons": ["评审人数不足"],
        }, self.policy)

        self.assertIsNone(verdict["overall"])
        self.assertEqual(verdict["scores"], {})
        self.assertEqual(verdict["provisional_overall"], 8.0)
        self.assertEqual(verdict["provisional_scores"], {"quality": 8.0})
        self.assertIn("评审人数不足", verdict["review_degradation_reasons"])
        self.assertNotIn("评分低于最低门槛", verdict["review_degradation_reasons"])

    def test_reassessment_merges_existing_provisional_scores(self):
        verdict = review_config.assess({
            "scores": {"quality": 8.0},
            "provisional_scores": {"safety": 6.0},
            "review_degraded": True,
            "review_degradation_reasons": ["未形成共识"],
        }, self.policy)

        self.assertEqual(verdict["provisional_scores"], {
            "quality": 8.0, "safety": 6.0,
        })
        self.assertEqual(verdict["scores"], {})


if __name__ == "__main__":
    unittest.main()
