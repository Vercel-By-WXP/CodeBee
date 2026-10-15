"""Signing reviews must read the manuscript, not merely return reassuring scores."""
import copy
import json
from unittest.mock import patch

from base import BaseTest
import test_novel_signing as signing
from test_novel_signing import _agents, _critique_json, _res, _serial_task


TEXT = "闻川把退款单推回去，决定先去找许棠。她肯不肯收这笔钱，他要当面问。"


def review(scores):
    return {"scores": scores, "evidence": [
        {"dim": dim, "chapter": 1, "quote": TEXT[:20],
         "analysis": "选择改变了行动方向；仍需结合后文验证退款的结果。"}
        for dim in scores], "issues": [], "summary": "内部文本诊断"}


class EvidenceValidationTests(BaseTest):
    def test_all_dimensions_need_real_chapter_quotes_and_analysis(self):
        from app.core import novel_quality as nq
        scores = dict.fromkeys(nq.SIGNING_RUBRIC, 8)
        valid = review(scores)
        self.assertEqual(nq.review_evidence_errors(valid, {1: TEXT}), [])
        mutations = [
            lambda r: r.pop("evidence"),
            lambda r: r["evidence"].pop(),
            lambda r: r["evidence"][0].update(quote="他当场烧掉收据，所有人都吓坏了。"),
            lambda r: r["evidence"][0].update(chapter=2),
            lambda r: r["evidence"][0].update(quote="闻川"),
            lambda r: r["evidence"][0].update(analysis=""),
            lambda r: r.update(evidence=[None, "bad", {}]),
        ]
        for mutate in mutations:
            broken = copy.deepcopy(valid)
            mutate(broken)
            self.assertTrue(nq.review_evidence_errors(broken, {1: TEXT}))

    def test_header_is_not_manuscript_evidence(self):
        from app.core import novel_quality as nq
        value = review(dict.fromkeys(nq.SIGNING_RUBRIC, 8))
        value["evidence"][0]["quote"] = "### 第 1 章 待评审稿件"
        self.assertTrue(nq.review_evidence_errors(value, {1: TEXT}))
        value["evidence"][0]["quote"] = "第一章：闻川拒收退款的那个雨夜"
        self.assertTrue(nq.review_evidence_errors(
            value, {1: "# 第一章：闻川拒收退款的那个雨夜\n\n" + TEXT}))


class OpeningEvidenceGateTests(BaseTest):
    def _exercise(self, signing_result, body=None, goal=None):
        from app.core import pipeline, store
        task = _serial_task(str(self.workdir))
        if goal:
            task["goal"] = goal
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_run(run["id"], status="running")
        agents, critics, impl = _agents()
        runner = signing.TestOpeningGate(methodName="runTest")
        calls = []
        self.signing_prompts = []

        def fake(run_id, role, agent, prompt, workdir, readonly, ev, *args, **kwargs):
            calls.append(role)
            if role == "draft-c1":
                pipeline._write_chapter(workdir, 1, body or TEXT + "山" * 300)
            if role == "signing-eval":
                self.signing_prompts.append(prompt)
                return signing_result() if callable(signing_result) else signing_result
            if role.startswith("critique-c") or role == "global-critique":
                return _critique_json(dict.fromkeys(task["rubric"], 8))
            return _res()

        with patch.object(pipeline.planner, "redesign_opening", return_value=None) as rebuild:
            runner._run(task, run, agents, critics, impl, fake)
        return store.get_run(run["id"]), calls, rebuild.call_count

    def test_score_only_or_failed_reviews_stop_before_next_chapter(self):
        from app.core import novel_quality as nq
        for result in (_res(json.dumps({"scores": dict.fromkeys(nq.SIGNING_RUBRIC, 9)})),
                       _res("model unavailable", ok=False),
                       _res(json.dumps(review(dict.fromkeys(nq.SIGNING_RUBRIC, 9))), ok=False)):
            saved, calls, rebuilt = self._exercise(result)
            self.assertEqual(saved["status"], "failed")
            self.assertIn("未完成有效文本评估", saved.get("error", ""))
            self.assertNotIn("global-critique", calls)
            self.assertEqual(rebuilt, 0)  # model failure is not a literary defect

    def test_high_opening_score_cannot_hide_weak_causality(self):
        from app.core import novel_quality as nq
        scores = dict.fromkeys(nq.SIGNING_RUBRIC, 8)
        scores["情节推进"] = 4
        saved, calls, rebuilt = self._exercise(_res(json.dumps(review(scores), ensure_ascii=False)))
        self.assertEqual(rebuilt, 1)
        self.assertEqual(saved["status"], "failed")
        self.assertIn("情节推进", saved.get("error", ""))

    def test_failed_final_review_cannot_restore_pass(self):
        from app.core import novel_quality as nq
        scores = dict.fromkeys(nq.SIGNING_RUBRIC, 8)
        scores["情节推进"] = 4
        results = iter([_res(json.dumps(review(scores))), _res("unavailable", ok=False)])
        saved, _, _ = self._exercise(lambda: next(results))
        self.assertEqual(saved["status"], "failed")
        self.assertIn("未完成有效文本评估", saved.get("error", ""))

    def test_high_scores_do_not_erase_major_with_real_quote(self):
        from app.core import novel_quality as nq
        value = review(dict.fromkeys(nq.SIGNING_RUBRIC, 8))
        value["issues"] = [{"dim": "情节推进", "severity": "major", "quote": TEXT[:20],
                            "note": "第1章人物选择缺少动机"}]
        saved, _, rebuilt = self._exercise(_res(json.dumps(value)))
        self.assertEqual(rebuilt, 1)
        self.assertEqual(saved["status"], "failed")
        self.assertIn("major", saved.get("error", ""))

    def test_review_scope_marks_clipped_chapter(self):
        from app.core import novel_quality as nq
        value = review(dict.fromkeys(nq.SIGNING_RUBRIC, 8))
        saved, _, _ = self._exercise(_res(json.dumps(value)), TEXT + "山" * 27000)
        scope = saved["signing_evals"]["opening_gate"]["read_scope"]
        self.assertTrue(scope[0]["excerpt"])
        self.assertLess(scope[0]["included_chars"], scope[0]["total_chars"])
        self.assertIn("实际送审范围", self.signing_prompts[0])
        self.assertNotIn("__CONTEXT__", self.signing_prompts[0])

    def test_signing_review_receives_feedback_as_case_not_manuscript(self):
        from app.core import novel_quality as nq
        value = review(dict.fromkeys(nq.SIGNING_RUBRIC, 8))
        self._exercise(_res(json.dumps(value)), goal="七猫鉴宝，优化情节推进")
        prompt = self.signing_prompts[0]
        self.assertIn("nf-qimao-shentong-20261015", prompt)
        self.assertIn("待完成，未验证改后效果", prompt)
        self.assertIn("非平台规则", prompt)


if __name__ == "__main__":
    import unittest
    unittest.main()
