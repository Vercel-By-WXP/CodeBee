import unittest

from core.quality_gate import aggregate_reviews, evaluate_release, local_version, normalize_scores


class ScoreValidationTests(unittest.TestCase):
    def test_scores_are_limited_to_one_through_ten(self):
        scores, errors = normalize_scores({"情节": 8, "人物": 39.2}, ["情节", "人物"])
        self.assertEqual(scores, {})
        self.assertIn("人物", errors)

    def test_missing_or_non_numeric_scores_are_invalid(self):
        scores, errors = normalize_scores({"情节": "abc"}, ["情节", "人物"])
        self.assertEqual(scores, {})
        self.assertIn("情节", errors)
        self.assertIn("人物", errors)


class ReviewerConsensusTests(unittest.TestCase):
    def test_two_valid_reviewers_are_required(self):
        result = aggregate_reviews(
            [{"id": "a", "scores": {"情节": 8, "人物": 8}}],
            ["情节", "人物"], threshold=7,
        )
        self.assertFalse(result["eligible"])
        self.assertTrue(any("2" in reason and "1" in reason
                            for reason in result["reasons"]))

    def test_large_reviewer_disagreement_blocks_consensus(self):
        result = aggregate_reviews(
            [
                {"id": "a", "scores": {"情节": 9, "人物": 9}},
                {"id": "b", "scores": {"情节": 5, "人物": 5}},
            ],
            ["情节", "人物"], threshold=7, max_spread=2.0,
        )
        self.assertFalse(result["consensus"])
        self.assertFalse(result["passed"])


class ReleaseGateTests(unittest.TestCase):
    def _verdict(self, **overrides):
        value = {
            "publishable": True, "global_pass": True,
            "global_scores": {"情节": 8, "人物": 8},
            "global_reviewer_count": 2, "global_reviewer_consensus": True,
            "chapter_scores": [{"chapter": 1, "passed": True,
                                "reviewer_count": 2,
                                "reviewer_consensus": True}],
            "end_chapter": 1, "major_issues": [],
            "signing_checkpoint": {"passed": True},
        }
        value.update(overrides)
        return value

    def test_major_issue_blocks_publish(self):
        result = evaluate_release(self._verdict(
            major_issues=[{"severity": "major", "note": "因果断裂"}],
        ), action="publish", target_chapter=1)
        self.assertFalse(result["allowed"])
        self.assertTrue(any("major" in blocker for blocker in result["blockers"]))

    def test_missing_release_evidence_blocks_publish(self):
        result = evaluate_release({"publishable": True}, action="publish")
        self.assertFalse(result["allowed"])
        self.assertTrue(result["blockers"])

    def test_target_chapter_must_be_in_reviewed_version(self):
        result = evaluate_release(
            self._verdict(end_chapter=1), action="publish", target_chapter=2,
        )
        self.assertFalse(result["allowed"])
        self.assertTrue(any("评审版本" in blocker for blocker in result["blockers"]))

    def test_signing_requires_platform_version_alignment(self):
        result = evaluate_release(
            self._verdict(), action="signing", target_chapter=1,
            platform_version={"remote_published": 0, "remote_total": 0},
        )
        self.assertFalse(result["allowed"])
        self.assertIn("平台版本", "；".join(result["blockers"]))

    def test_force_release_requires_explicit_confirmation_and_reason(self):
        blocked = evaluate_release(
            self._verdict(publishable=False), action="publish", target_chapter=1,
            force=True, force_confirmed=False, force_reason="",
        )
        self.assertFalse(blocked["allowed"])
        self.assertIn("二次确认", "；".join(blocked["blockers"]))

        allowed = evaluate_release(
            self._verdict(publishable=False), action="publish", target_chapter=1,
            force=True, force_confirmed=True, force_reason="人工复核后放行",
        )
        self.assertTrue(allowed["allowed"])
        self.assertTrue(allowed["forced"])

    def test_continue_is_separate_from_publish(self):
        result = evaluate_release(self._verdict(publishable=False), action="continue")
        self.assertTrue(result["allowed"])
        self.assertEqual(result["status"], "continue_only")


class VersionSnapshotTests(unittest.TestCase):
    def test_local_version_counts_chapters_and_words(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "chapter-01.md").write_text("甲" * 10, encoding="utf-8")
            (root / "chapter-03.md").write_text("乙" * 20, encoding="utf-8")
            value = local_version(root)
        self.assertEqual(value["local_total"], 2)
        self.assertEqual(value["local_latest"], 3)
        self.assertEqual(value["local_words"], 30)


if __name__ == "__main__":
    unittest.main()
