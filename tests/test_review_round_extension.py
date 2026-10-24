# -*- coding: utf-8 -*-
"""评审自动追加轮（2026-10-24 拍板「打回直到全过审」）决策回归。

只测模块级决策函数 should_extend_review / review_round_improved：
进步保险丝 = 均分更高或 major 更少才追加；降级轮 / 无进步 / 硬上限 /
mock 不追加。循环本体的接线由存量评审测试覆盖。
"""
import unittest

from app.core.pipeline import (
    REVIEW_EXTRA_ROUNDS_MAX, review_round_improved, should_extend_review,
)


def _round(n, means=None, majors=0):
    return {"round": n, "means": means or {},
            "issues": [{"severity": "major"}] * majors}


class ReviewRoundImprovedTests(unittest.TestCase):
    def test_mean_up_is_improved(self):
        self.assertTrue(review_round_improved([
            _round(1, {"a": 6.0, "b": 6.0}), _round(2, {"a": 7.0, "b": 6.0})]))

    def test_fewer_majors_is_improved(self):
        self.assertTrue(review_round_improved([
            _round(1, {"a": 6.0}, majors=3), _round(2, {"a": 6.0}, majors=1)]))

    def test_frozen_scores_are_not_improved(self):
        self.assertFalse(review_round_improved([
            _round(1, {"a": 6.0}, majors=2), _round(2, {"a": 6.0}, majors=2)]))

    def test_declining_is_not_improved(self):
        self.assertFalse(review_round_improved([
            _round(1, {"a": 7.0}), _round(2, {"a": 6.0})]))

    def test_single_round_has_no_baseline(self):
        self.assertFalse(review_round_improved([_round(1, {"a": 9.0})]))


class ShouldExtendReviewTests(unittest.TestCase):
    def _history(self):
        return [_round(1, {"a": 6.0}), _round(2, {"a": 7.0})]

    def test_improving_healthy_round_extends(self):
        self.assertTrue(should_extend_review(
            0, False, False, True, self._history()))

    def test_cap_reached_stops(self):
        self.assertEqual(REVIEW_EXTRA_ROUNDS_MAX, 2)
        self.assertFalse(should_extend_review(
            REVIEW_EXTRA_ROUNDS_MAX, False, False, True, self._history()))

    def test_degraded_round_stops(self):
        # 服务降级（通道故障）：换将/兜底该管的事，修订救不了
        self.assertFalse(should_extend_review(
            0, True, False, True, self._history()))

    def test_degraded_draft_stops(self):
        # 起草降级把发布门禁硬锁死：分数再高也过不了，追加轮纯烧钱
        self.assertFalse(should_extend_review(
            0, False, True, True, self._history()))

    def test_no_progress_stops(self):
        frozen = [_round(1, {"a": 6.0}, majors=2),
                  _round(2, {"a": 6.0}, majors=2)]
        self.assertFalse(should_extend_review(0, False, False, True, frozen))

    def test_mock_never_extends(self):
        self.assertFalse(should_extend_review(
            0, False, False, True, self._history(), mock_mode=True))

    def test_no_scores_stops(self):
        self.assertFalse(should_extend_review(
            0, False, False, False, self._history()))


class ReviseFallbackAgentTests(unittest.TestCase):
    def _agents(self):
        return [
            {"id": "codex-cli", "mode": "real"},
            {"id": "claude-code", "mode": "real"},
            {"id": "mock-helper", "mode": "mock"},
        ]

    def test_sick_impl_is_replaced_by_other_real_cli(self):
        from app.core.pipeline import _revise_fallback_agent
        alt = _revise_fallback_agent(
            self._agents(), {"id": "codex-cli", "mode": "real"}, "w")
        self.assertEqual(alt.get("id"), "claude-code")

    def test_mock_agents_never_take_over_revision(self):
        from app.core.pipeline import _revise_fallback_agent
        alt = _revise_fallback_agent(
            [{"id": "codex-cli", "mode": "real"},
             {"id": "mock-helper", "mode": "mock"}],
            {"id": "codex-cli", "mode": "real"}, "w")
        self.assertIsNone(alt)

    def test_benched_agent_is_skipped(self):
        from app.core.pipeline import _revise_fallback_agent
        alt = _revise_fallback_agent(
            self._agents(), {"id": "codex-cli", "mode": "real"}, "w",
            benched=["claude-code"])
        self.assertIsNone(alt)


if __name__ == "__main__":
    unittest.main()
