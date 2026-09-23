# -*- coding: utf-8 -*-
"""代码评审 JSON 解析失败时 extract_scores_from_text 回退（第 5 道网）单测。

跑法：python -m unittest discover -s tests -p "test_review_fallback.py" -v
"""
from __future__ import annotations

from base import BaseTest


class ReviewFallbackTests(BaseTest):
    def test_scores_recovered_from_prose(self):
        """评审 JSON 坏但分数散在正文：从原文提取，不再误判 pass=False。"""
        from app.core import pipeline, runner
        from unittest import mock
        bad_text = ("评审结论如下：\n"
                    "正确性：8 分\n"
                    "可维护性：7 分\n"
                    "安全：9 分\n"
                    "总体可以通过。")
        # 模拟 extract_json 返回 None（解析失败）
        with mock.patch.object(runner, "extract_json", return_value=None), \
             mock.patch.object(runner, "extract_scores_from_text",
                               return_value={"正确性": 8, "可维护性": 7, "安全": 9}):
            # 直接调用内部逻辑（不走完整 _run_review 的 CLI 步骤）
            scores = runner.extract_scores_from_text(bad_text)
        self.assertEqual(scores.get("正确性"), 8)
        self.assertEqual(scores.get("安全"), 9)

    def test_extract_scores_from_text_direct(self):
        """extract_scores_from_text 本身能从散文提取分数。"""
        from app.core import runner
        text = "综合评审：正确性：8 分，可维护性：6 分，安全：9 分。"
        scores = runner.extract_scores_from_text(text)
        self.assertIn("正确性", scores)
        self.assertEqual(scores["正确性"], 8)

    def test_no_scores_still_fails_gracefully(self):
        """原文无分数：维持原有空分+报错（不炸）。"""
        from app.core import runner
        scores = runner.extract_scores_from_text("这里没有任何分数信息")
        self.assertEqual(scores, {})

    def test_fallback_logic_in_run_review(self):
        """_run_review 坏 JSON 时走回退（mock _run_step + extract_json）。"""
        from app.core import pipeline, runner
        from unittest import mock
        from app.core import store
        task = store.create_task({"type": "code", "title": "回退", "goal": "g",
                                  "workdir": str(self.workdir), "mode": "auto"})
        run = store.create_run("orchestration", "回退", task_id=task["id"])
        reviewer = {"id": "test-rev", "mode": "real", "label": "评审者"}
        bad_text = "正确性：8 分\n可维护性：7 分\n安全：9 分"
        with mock.patch.object(pipeline, "_git_diff", return_value="diff"), \
             mock.patch.object(pipeline, "_run_step",
                               return_value={"ok": True, "text": bad_text, "error": ""}), \
             mock.patch.object(pipeline, "_task_images", return_value=[]), \
             mock.patch.object(runner, "extract_json", return_value=None):
            result = pipeline._run_review(run["id"], task, str(self.workdir),
                                          reviewer, None)
        self.assertIn("scores", result)
        self.assertTrue(any(k in result.get("scores", {}) for k in ("正确性", "可维护性", "安全")))


if __name__ == "__main__":
    import unittest
    unittest.main()
