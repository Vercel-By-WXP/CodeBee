# -*- coding: utf-8 -*-
"""Regression checks for release score normalization and malformed verdicts."""
import unittest
import tempfile
from pathlib import Path

from app.core import quality_gate


class QualityGateTests(unittest.TestCase):
    def test_release_is_blocked_when_reviewed_manuscript_fingerprint_is_stale(self):
        verdict = self._passing_signing_verdict()
        verdict["manuscript_fingerprint"] = "reviewed-version"
        result = quality_gate.evaluate_release(
            verdict, action="signing", target_chapter=2,
            platform_version={"remote_published": 2},
            current_manuscript_fingerprint="edited-version")
        self.assertFalse(result["allowed"], result)
        self.assertIn("评审后正文已变化，请重新评审当前稿件", result["blockers"])

        result = quality_gate.evaluate_release(
            verdict, action="signing", target_chapter=2,
            platform_version={"remote_published": 2},
            current_manuscript_fingerprint="")
        self.assertFalse(result["allowed"], result)
        self.assertIn("当前正文版本无法核验，请重新评审当前稿件", result["blockers"])

    def test_fingerprint_scope_ignores_unreviewed_chapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "chapter-01.md").write_text("reviewed", encoding="utf-8")
            (root / "chapter-02.md").write_text("unreviewed", encoding="utf-8")
            first = quality_gate.local_version(
                root, start_chapter=1, end_chapter=1)["fingerprint"]
            (root / "chapter-02.md").write_text("changed unreviewed", encoding="utf-8")
            second = quality_gate.local_version(
                root, start_chapter=1, end_chapter=1)["fingerprint"]
            self.assertEqual(first, second)

    def test_manuscript_fingerprint_uses_pipeline_filename_sanitization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = root / "foo_bar.md"
            expected.write_text("reviewed manuscript", encoding="utf-8")
            self.assertEqual(
                quality_gate.local_version(root, "foo/bar.md")["fingerprint"],
                quality_gate.local_version(root, "foo_bar.md")["fingerprint"],
            )

    def test_archive_fingerprint_ignores_duplicate_extension_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "已成稿"
            archive.mkdir()
            (archive / "prefix.md").write_text("reviewed copy", encoding="utf-8")
            duplicate = archive / "prefix.txt"
            duplicate.write_text("ignored duplicate", encoding="utf-8")
            first = quality_gate.local_version(
                tmp, include_archive=True)["fingerprint"]
            duplicate.write_text("edited ignored copy", encoding="utf-8")
            second = quality_gate.local_version(
                tmp, include_archive=True)["fingerprint"]
            self.assertEqual(first, second)

    def test_archive_fingerprint_ignores_case_variant_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "已成稿"
            archive.mkdir()
            (archive / "Prefix.md").write_text("reviewed copy", encoding="utf-8")
            duplicate = archive / "PREFIX.txt"
            duplicate.write_text("ignored duplicate", encoding="utf-8")
            first = quality_gate.local_version(tmp, include_archive=True)["fingerprint"]
            duplicate.write_text("edited ignored copy", encoding="utf-8")
            second = quality_gate.local_version(tmp, include_archive=True)["fingerprint"]
            self.assertEqual(first, second)

    def test_local_version_rejects_malformed_scope_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "chapter-01.md").write_text("reviewed", encoding="utf-8")
            for kwargs in ({"start_chapter": True},
                           {"start_chapter": 1.5},
                           {"end_chapter": False},
                           {"start_chapter": 2, "end_chapter": 1}):
                with self.subTest(kwargs=kwargs):
                    snapshot = quality_gate.local_version(root, **kwargs)
                    self.assertEqual(snapshot["fingerprint"], "")
            self.assertEqual(
                quality_gate.local_version(root, start_chapter="1")["fingerprint"],
                quality_gate.local_version(root, start_chapter=1)["fingerprint"])
            self.assertEqual(
                quality_gate.local_version(root, end_chapter="2")["fingerprint"],
                quality_gate.local_version(root, end_chapter=2)["fingerprint"])

    def test_local_version_deduplicates_manuscript_and_chapter_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chapter = root / "chapter-01.md"
            chapter.write_text("reviewed", encoding="utf-8")
            snapshot = quality_gate.local_version(
                root, manuscript="chapter-01.md", start_chapter=1, end_chapter=1)
            self.assertEqual(snapshot["local_total"], 1)
            self.assertEqual(snapshot["local_words"], len("reviewed"))

    def test_continuation_uses_archive_fingerprint_only_for_missing_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "chapter-01.md").write_text("local chapter", encoding="utf-8")
            (root / "chapter-02.md").write_text("reviewed chapter", encoding="utf-8")
            archive = root / "已成稿"
            archive.mkdir()
            archived = archive / "old.md"
            archived.write_text("unread archive", encoding="utf-8")
            self.assertTrue(quality_gate.has_complete_local_prefix(root, 3))
            first = quality_gate.local_version(
                root, start_chapter=3, end_chapter=3,
                include_archive=not quality_gate.has_complete_local_prefix(root, 3))
            archived.write_text("edited unread archive", encoding="utf-8")
            second = quality_gate.local_version(
                root, start_chapter=3, end_chapter=3,
                include_archive=not quality_gate.has_complete_local_prefix(root, 3))
            self.assertEqual(first["fingerprint"], second["fingerprint"])

            (root / "chapter-01.md").unlink()
            self.assertFalse(quality_gate.has_complete_local_prefix(root, 3))

    def test_prefix_scope_rejects_fractional_or_boolean_chapter_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "chapter-01.md").write_text("prefix", encoding="utf-8")
            self.assertFalse(quality_gate.has_complete_local_prefix(root, 2.5))
            self.assertFalse(quality_gate.has_complete_local_prefix(root, True))

    def test_local_version_fingerprint_tracks_manuscript_and_archived_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manuscript = root / "my-book.md"
            chapter = root / "chapter-01.md"
            archive = root / "已成稿"
            archive.mkdir()
            archived = archive / "prefix.md"
            manuscript.write_text("opening text", encoding="utf-8")
            chapter.write_text("chapter text", encoding="utf-8")
            archived.write_text("archived text", encoding="utf-8")

            first = quality_gate.local_version(root, "my-book.md", include_archive=True)
            self.assertEqual(first, quality_gate.local_version(
                root, "my-book.md", include_archive=True))
            manuscript.write_text("opening edit", encoding="utf-8")
            second = quality_gate.local_version(root, "my-book.md", include_archive=True)
            self.assertNotEqual(first["fingerprint"], second["fingerprint"])
            archived.write_text("archived edit", encoding="utf-8")
            third = quality_gate.local_version(root, "my-book.md", include_archive=True)
            self.assertNotEqual(second["fingerprint"], third["fingerprint"])

    def test_boolean_scores_are_rejected_instead_of_coerced_to_numbers(self):
        for value in (True, False):
            with self.subTest(value=value):
                scores, errors = quality_gate.normalize_scores(
                    {"plot": value}, ["plot"])
                self.assertEqual(scores, {})
                self.assertEqual(errors, {"plot": "非数字"})

                result = quality_gate.aggregate_reviews(
                    [{"id": "reviewer", "scores": {"plot": value}}],
                    ["plot"], min_reviewers=1)
                self.assertFalse(result["eligible"])
                self.assertEqual(result["reviewer_count"], 0)

    def test_score_normalization_accepts_one_shot_dimension_iterables(self):
        scores, errors = quality_gate.normalize_scores(
            {"plot": 8.0, "character": 9.0},
            (dimension for dimension in ("plot", "character")))
        self.assertEqual(scores, {"plot": 8.0, "character": 9.0})
        self.assertEqual(errors, {})

    def test_unrepresentably_large_scores_are_rejected_without_raising(self):
        score = 10 ** 10000
        scores, errors = quality_gate.normalize_scores({"plot": score}, ["plot"])
        self.assertEqual(scores, {})
        self.assertEqual(errors, {"plot": "非数字"})

    def test_unrepresentably_large_persisted_scores_fail_closed(self):
        verdict = self._passing_signing_verdict()
        verdict["global_scores"] = {"plot": 10 ** 400}
        result = quality_gate.evaluate_release(
            verdict, action="signing", target_chapter=2,
            platform_version={"remote_published": 2})
        self.assertFalse(result["allowed"], result)
        self.assertIn("全局评分证据缺失或无效", result["blockers"])

    def _passing_signing_verdict(self):
        return {
            "publishable": True,
            "serial": True,
            "threshold": 7.0,
            "global_pass": True,
            "global_reviewer_count": 2,
            "global_reviewer_consensus": True,
            "global_scores": {"plot": 8.0},
            "chapter_scores": [{
                "chapter": 1, "passed": True, "reviewer_count": 2,
                "reviewer_consensus": True, "means": {"plot": 8.0},
            }, {
                "chapter": 2, "passed": True, "reviewer_count": 2,
                "reviewer_consensus": True, "means": {"plot": 8.0},
            }],
            "major_issues": [],
            "start_chapter": 1,
            "end_chapter": 2,
            "signing_checkpoint": {
                "passed": True, "at_words": 20000, "chapter": 2,
                "means": {"plot": 8.0}, "reviewer_count": 2,
                "reviewer_consensus": True,
            },
        }

    def test_valid_signing_evidence_remains_allowed(self):
        result = quality_gate.evaluate_release(
            self._passing_signing_verdict(), action="signing", target_chapter=2,
            platform_version={"remote_published": 2})
        self.assertTrue(result["allowed"], result)

    def test_malformed_persisted_signing_evidence_fails_closed(self):
        cases = (
            ("global_reviewer_count", "many"),
            ("global_pass", None),
            ("global_scores", {}),
            ("global_scores", {"plot": True}),
            ("global_reviewer_consensus", "true"),
            ("chapter_scores", ["not-a-chapter-result"]),
            ("chapter_scores", []),
            ("major_issues", None),
            ("major_issues", ["malformed-issue"]),
            ("major_issues", [{"severity": "minor"}]),
            ("major_issues", [{"note": "missing severity"}]),
            ("major_issues", [{"severity": "major", "anchored": False}]),
            ("signing_checkpoint", "passed"),
            ("signing_checkpoint", {"passed": True, "at_words": 12000,
                                     "chapter": 2, "reviewer_count": 2,
                                     "reviewer_consensus": True}),
            ("signing_checkpoint", {"passed": True, "at_words": 20000,
                                     "chapter": 3, "reviewer_count": 2,
                                     "means": {"plot": 8.0},
                                     "reviewer_consensus": True}),
        )
        for field, value in cases:
            with self.subTest(field=field):
                verdict = self._passing_signing_verdict()
                verdict[field] = value
                result = quality_gate.evaluate_release(
                    verdict, action="signing", target_chapter=2,
                    platform_version={"remote_published": "unknown"})
                self.assertFalse(result["allowed"], result)
                self.assertTrue(result["blockers"], result)

    def test_missing_serial_major_issue_evidence_fails_closed(self):
        verdict = self._passing_signing_verdict()
        del verdict["major_issues"]
        result = quality_gate.evaluate_release(
            verdict, action="signing", target_chapter=2,
            platform_version={"remote_published": 2})
        self.assertFalse(result["allowed"], result)
        self.assertIn("全书 major 问题评审数据缺失或格式无效", result["blockers"])

    def test_serial_chapter_evidence_must_cover_declared_range_exactly(self):
        cases = (
            [{"chapter": 1, "passed": True, "reviewer_count": 2,
              "reviewer_consensus": True}],
            [
                {"chapter": 1, "passed": True, "reviewer_count": 2,
                 "reviewer_consensus": True},
                {"chapter": 1, "passed": True, "reviewer_count": 2,
                 "reviewer_consensus": True},
            ],
            [
                {"chapter": 1, "passed": True, "reviewer_count": 2,
                 "reviewer_consensus": True},
                {"chapter": 3, "passed": True, "reviewer_count": 2,
                 "reviewer_consensus": True},
            ],
            [
                {"passed": True, "reviewer_count": 2,
                 "reviewer_consensus": True},
                {"chapter": 2, "passed": True, "reviewer_count": 2,
                 "reviewer_consensus": True},
            ],
        )
        for chapter_scores in cases:
            with self.subTest(chapter_scores=chapter_scores):
                verdict = self._passing_signing_verdict()
                verdict["chapter_scores"] = chapter_scores
                result = quality_gate.evaluate_release(
                    verdict, action="signing", target_chapter=2,
                    platform_version={"remote_published": 2})
                self.assertFalse(result["allowed"], result)
                self.assertTrue(any(
                    "逐章评审范围或章节编号无效" in item or
                    "逐章评审未完整覆盖声明范围" in item
                    for item in result["blockers"]), result)

    def test_extreme_chapter_range_is_rejected_without_expanding_range(self):
        verdict = self._passing_signing_verdict()
        verdict["end_chapter"] = 10 ** 400
        result = quality_gate.evaluate_release(
            verdict, action="signing", target_chapter=2,
            platform_version={"remote_published": 2})
        self.assertFalse(result["allowed"], result)
        self.assertIn("逐章评审范围或章节编号无效", result["blockers"])

    def test_signing_checkpoint_requires_word_count_and_reviewed_chapter(self):
        verdict = self._passing_signing_verdict()
        verdict["signing_checkpoint"].pop("at_words")
        result = quality_gate.evaluate_release(
            verdict, action="signing", target_chapter=2,
            platform_version={"remote_published": 2})
        self.assertFalse(result["allowed"], result)
        self.assertIn("签约检查点字数证据不足 20000 字", result["blockers"])

        verdict = self._passing_signing_verdict()
        verdict["signing_checkpoint"].pop("chapter")
        result = quality_gate.evaluate_release(
            verdict, action="signing", target_chapter=2,
            platform_version={"remote_published": 2})
        self.assertFalse(result["allowed"], result)
        self.assertIn("签约检查点章节不在已评审范围内", result["blockers"])

    def test_requested_publish_chapter_must_have_its_own_review_evidence(self):
        verdict = self._passing_signing_verdict()
        verdict["start_chapter"] = 2
        verdict["end_chapter"] = 3
        verdict["chapter_scores"] = [
            {"chapter": chapter, "passed": True, "reviewer_count": 2,
             "reviewer_consensus": True}
            for chapter in (2, 3)
        ]
        verdict.pop("signing_checkpoint")
        result = quality_gate.evaluate_release(
            verdict, action="publish", target_chapter=1,
            platform_version={"remote_published": 3})
        self.assertFalse(result["allowed"], result)
        self.assertIn("目标章节没有对应的逐章评审证据", result["blockers"])

    def test_serial_score_evidence_must_be_present_finite_and_in_range(self):
        cases = (
            ("global_scores", None),
            ("global_scores", {"plot": float("nan")}),
            ("global_scores", {"plot": 11}),
            ("global_scores", {"plot": "8"}),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                verdict = self._passing_signing_verdict()
                verdict[field] = value
                result = quality_gate.evaluate_release(
                    verdict, action="signing", target_chapter=2,
                    platform_version={"remote_published": 2})
                self.assertFalse(result["allowed"], result)
                self.assertIn("全局评分证据缺失或无效", result["blockers"])

        for location, field in (("chapter", "means"),
                                ("checkpoint", "means")):
            verdict = self._passing_signing_verdict()
            target = (verdict["chapter_scores"][0] if location == "chapter"
                      else verdict["signing_checkpoint"])
            target[field] = {"plot": "not-a-score"}
            result = quality_gate.evaluate_release(
                verdict, action="signing", target_chapter=2,
                platform_version={"remote_published": 2})
            self.assertFalse(result["allowed"], result)
            message = ("逐章评分证据缺失或无效" if location == "chapter"
                       else "签约检查点评分证据缺失或无效")
            self.assertIn(message, result["blockers"])

    def test_serial_chapter_and_global_scores_must_use_the_same_dimensions(self):
        verdict = self._passing_signing_verdict()
        verdict["global_scores"] = {"plot": 8.0, "character": 8.0}
        verdict["chapter_scores"][0]["means"] = {"plot": 8.0}
        result = quality_gate.evaluate_release(
            verdict, action="signing", target_chapter=2,
            platform_version={"remote_published": 2})
        self.assertFalse(result["allowed"], result)
        self.assertIn("逐章与全局评分维度不一致", result["blockers"])

    def test_persisted_pass_flags_cannot_override_scores_below_threshold(self):
        cases = (
            ("global", "global_scores", {"plot": 6.9},
             "全局评分未达到有效发布门槛"),
            ("chapter", "means", {"plot": 6.9},
             "存在评分低于逐章发布门槛的章节"),
            ("checkpoint", "means", {"plot": 6.9},
             "签约检查点评分未达到有效发布门槛"),
        )
        for location, field, scores, blocker in cases:
            with self.subTest(location=location):
                verdict = self._passing_signing_verdict()
                if location == "global":
                    verdict[field] = scores
                elif location == "chapter":
                    verdict["chapter_scores"][0][field] = scores
                else:
                    verdict["signing_checkpoint"][field] = scores
                result = quality_gate.evaluate_release(
                    verdict, action="signing", target_chapter=2,
                    platform_version={"remote_published": 2})
                self.assertFalse(result["allowed"], result)
                self.assertIn(blocker, result["blockers"])

    def test_valid_relaxed_chapter_threshold_matches_serial_pipeline(self):
        verdict = self._passing_signing_verdict()
        verdict["threshold"] = 8.0
        for chapter in verdict["chapter_scores"]:
            chapter["means"] = {"plot": 7.5}
        result = quality_gate.evaluate_release(
            verdict, action="signing", target_chapter=2,
            platform_version={"remote_published": 2})
        self.assertTrue(result["allowed"], result)

    def test_serial_release_requires_a_valid_explicit_score_threshold(self):
        for value in (None, True, 0, 10.1, float("nan"), "7", 10 ** 400):
            with self.subTest(value=value):
                verdict = self._passing_signing_verdict()
                verdict["threshold"] = value
                result = quality_gate.evaluate_release(
                    verdict, action="signing", target_chapter=2,
                    platform_version={"remote_published": 2})
                self.assertFalse(result["allowed"], result)
                self.assertIn("全局评分未达到有效发布门槛", result["blockers"])

    def test_missing_serial_review_fields_fail_closed(self):
        for field in ("global_pass", "global_reviewer_consensus", "chapter_scores"):
            with self.subTest(field=field):
                verdict = self._passing_signing_verdict()
                del verdict[field]
                result = quality_gate.evaluate_release(
                    verdict, action="signing", target_chapter=2,
                    platform_version={"remote_published": 2})
                self.assertFalse(result["allowed"], result)

    def test_missing_or_malformed_chapter_review_metadata_fails_closed(self):
        cases = (
            ("reviewer_count", None),
            ("reviewer_count", "two"),
            ("reviewer_count", True),
            ("reviewer_consensus", None),
            ("reviewer_consensus", False),
            ("reviewer_consensus", "true"),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                verdict = self._passing_signing_verdict()
                if value is None:
                    verdict["chapter_scores"][0].pop(field, None)
                else:
                    verdict["chapter_scores"][0][field] = value
                result = quality_gate.evaluate_release(
                    verdict, action="signing", target_chapter=2,
                    platform_version={"remote_published": 2})
                self.assertFalse(result["allowed"], result)
                self.assertIn("存在评审人数不足或无共识章节", result["blockers"])

    def test_missing_or_malformed_signing_checkpoint_review_metadata_fails_closed(self):
        cases = (
            ("reviewer_count", None),
            ("reviewer_count", "two"),
            ("reviewer_count", True),
            ("reviewer_consensus", None),
            ("reviewer_consensus", False),
            ("reviewer_consensus", "true"),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                verdict = self._passing_signing_verdict()
                if value is None:
                    verdict["signing_checkpoint"].pop(field, None)
                else:
                    verdict["signing_checkpoint"][field] = value
                result = quality_gate.evaluate_release(
                    verdict, action="signing", target_chapter=2,
                    platform_version={"remote_published": 2})
                self.assertFalse(result["allowed"], result)
                if field == "reviewer_count":
                    self.assertIn("签约检查点有效评审不足 2 名", result["blockers"])
                else:
                    self.assertIn("签约检查点评审未形成共识", result["blockers"])

    def test_boolean_or_fractional_target_chapter_fails_closed(self):
        for target in (True, 2.5):
            with self.subTest(target=target):
                result = quality_gate.evaluate_release(
                    self._passing_signing_verdict(), action="signing",
                    target_chapter=target,
                    platform_version={"remote_published": 2})
                self.assertFalse(result["allowed"], result)
                self.assertIn("目标章节号无效", result["blockers"])

    def test_string_force_flags_never_authorize_or_confirm_bypass(self):
        blocked_verdict = self._passing_signing_verdict()
        blocked_verdict["publishable"] = False
        result = quality_gate.evaluate_release(
            blocked_verdict, action="signing", target_chapter=2,
            platform_version={"remote_published": 2},
            force="true", force_confirmed="true", force_reason="人工已复核")
        self.assertFalse(result["allowed"], result)
        self.assertFalse(result["forced"], result)

        confirmed = quality_gate.evaluate_release(
            blocked_verdict, action="signing", target_chapter=2,
            platform_version={"remote_published": 2},
            force=True, force_confirmed="true", force_reason="人工已复核")
        self.assertFalse(confirmed["allowed"], confirmed)
        self.assertIn("强制放行必须二次确认", confirmed["blockers"])


if __name__ == "__main__":
    unittest.main()
