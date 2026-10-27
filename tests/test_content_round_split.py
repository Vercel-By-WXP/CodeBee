# -*- coding: utf-8 -*-
"""内容评审 P2-1 第四/五刀的回归：单轮评审、共识门禁、修订三段各就各位。

背景：`_content_rounds` 原为 456 行（评审-修订循环），2026-10-27 再切成
`_content_rounds`（53 行：推进 / 早退 / 追加轮）+ `_content_review_round`
（单轮评审 + 减员补位）+ `_content_round_consensus`（共识与发布门禁，纯计算）
+ `_content_revise_round`（按意见修订）。

锁死三件事：
1. 循环体只剩推进与判定，跳转语句（`break`）不许再漏回单轮函数里；
2. 门禁分支不许退化 —— 每一条都对应一次真实误判（席位不足 / 纯机测 /
   本地模板稿 / 派单缺维）；
3. 修订段拿不到本轮结论时会退化成「无意见修订」，所以要断言它真的吃了
   本轮 means / issues。
"""
import inspect
import unittest
from types import SimpleNamespace
from unittest import mock

from app.core import local_fallback, pipeline

DIMS = ["情节", "文笔"]


def seat(scores, **extra):
    review = {"scores": dict(scores), "issues": [], "summary": "ok"}
    review.update(extra)
    return review


class RoundShapeTests(unittest.TestCase):
    """形状：循环薄、三段各自有 docstring、状态一律合并式推进。"""

    HELPERS = ("_content_review_round", "_content_round_consensus",
               "_content_revise_round")

    def test_loop_is_thin(self):
        src = inspect.getsource(pipeline._content_rounds)
        self.assertLess(len(src.splitlines()), 70,
                        "_content_rounds 又变厚了：单轮逻辑请落进段函数")
        for name in ("_content_review_round", "_content_revise_round"):
            self.assertIn(name, src, "循环里没调用 %s" % name)

    def test_no_loop_control_left_in_helpers(self):
        """`break` 只能在编排里：搬进段函数会变成语法错误或提前返回。"""
        for name in self.HELPERS:
            src = inspect.getsource(getattr(pipeline, name))
            self.assertNotIn("        break", src, "%s 里出现了 break" % name)
            self.assertNotIn("        continue", src, "%s 里出现了 continue" % name)

    def test_helpers_have_docstring_and_budget(self):
        budgets = {"_content_review_round": 300, "_content_round_consensus": 130,
                   "_content_revise_round": 180}
        for name, budget in budgets.items():
            fn = getattr(pipeline, name, None)
            self.assertTrue(callable(fn), "缺少 %s" % name)
            self.assertTrue((fn.__doc__ or "").strip(), "%s 缺 docstring" % name)
            n = len(inspect.getsource(fn).splitlines())
            self.assertLess(n, budget, "%s 涨到 %d 行（预算 %d）" % (name, n, budget))

    def test_state_is_merged_not_truncated(self):
        """两段都要走 `_content_state`，否则上游字段会被 locals() 截断。"""
        for name in ("_content_review_round", "_content_revise_round"):
            src = inspect.getsource(getattr(pipeline, name))
            self.assertIn("_content_state(st,", src,
                          "%s 没走合并式状态推进" % name)


class ConsensusGateTests(unittest.TestCase):
    """门禁纯计算：不跑流水线也能锁住每一条发布判定。"""

    def gate(self, per_agent, required=2, draft_degraded=False,
             draft_source="", threshold=7.0, is_selection=False,
             issues=None, selection=None):
        return pipeline._content_round_consensus(
            per_agent, DIMS, threshold, required, draft_degraded, draft_source,
            is_selection, "正文", list(issues or []),
            selection or SimpleNamespace(parse_report=lambda _t: ("", [])))

    def test_two_qualified_seats_pass(self):
        out = self.gate({"a": seat({"情节": 8.0, "文笔": 8.5}),
                         "b": seat({"情节": 8.2, "文笔": 8.0})})
        self.assertEqual({"情节": 8.1, "文笔": 8.2}, out.means)
        self.assertFalse(out.degraded)
        self.assertTrue(out.publishable, "两席达标却不放行")
        self.assertEqual({}, out.provisional_means, "达标轮不该带临时分")

    def test_seat_shortage_degrades_and_blocks(self):
        out = self.gate({"a": seat({"情节": 9.0, "文笔": 9.0})}, required=2)
        self.assertTrue(out.degraded, "单席却当成完整共识")
        self.assertFalse(out.publishable)
        self.assertEqual(out.means, out.provisional_means,
                         "降级轮的分数要留作临时证据")

    def test_heuristic_only_is_not_degraded_but_never_publishes(self):
        """纯机测不是「评审失败」，但也不能放行——成功不能靠规则分冒充。"""
        out = self.gate({"local": seat({"情节": 7.0, "文笔": 7.0},
                                      review_fallback=local_fallback.HEURISTIC)})
        self.assertFalse(out.degraded, "纯机测被误判成评审降级")
        self.assertFalse(out.publishable, "本地启发式评审放行了发布门禁")

    def test_local_template_draft_never_publishes(self):
        out = self.gate({"a": seat({"情节": 9.0, "文笔": 9.0}),
                         "b": seat({"情节": 9.0, "文笔": 9.0})},
                        draft_source=local_fallback.TEMPLATE)
        self.assertFalse(out.publishable, "本地模板稿被当成成品放行")

    def test_draft_degraded_blocks_even_with_high_scores(self):
        out = self.gate({"a": seat({"情节": 9.0, "文笔": 9.0}),
                         "b": seat({"情节": 9.0, "文笔": 9.0})},
                        draft_degraded=True)
        self.assertFalse(out.publishable)

    def test_model_scores_are_not_dragged_by_heuristic_seat(self):
        out = self.gate({"a": seat({"情节": 9.0, "文笔": 9.0}),
                         "h": seat({"情节": 4.0, "文笔": 4.0},
                                   review_fallback=local_fallback.HEURISTIC)})
        self.assertEqual({"情节": 9.0, "文笔": 9.0}, out.means,
                         "机测分被算进模型共识均值")

    def test_no_scores_at_all_is_critic_failed(self):
        out = self.gate({"a": {"scores": {}, "issues": []}})
        self.assertEqual({}, out.means)
        self.assertTrue(out.critic_failed)
        self.assertFalse(out.publishable)

    def test_selection_requires_every_dimension(self):
        """派单：缺维度不能靠均值掩盖，必须拦住。"""
        out = self.gate({"a": seat({"情节": 9.0})}, required=1, is_selection=True)
        self.assertFalse(out.publishable, "派单缺维度却放行")
        out_ok = self.gate({"a": seat({"情节": 9.0, "文笔": 9.0})},
                           required=1, is_selection=True)
        self.assertTrue(out_ok.publishable)

    def test_selection_blocking_issue_blocks(self):
        bad = [{"dim": "交接完整", "severity": "major", "note": "缺文件清单"}]
        out = self.gate({"a": seat({"情节": 9.0, "文笔": 9.0})}, required=1,
                        is_selection=True, issues=bad)
        self.assertFalse(out.publishable, "派单的阻塞问题没有拦住发布")

    def test_selection_package_errors_are_appended(self):
        issues = []
        sel = SimpleNamespace(parse_report=lambda _t: ("", ["缺少交付清单"]))
        out = self.gate({"a": seat({"情节": 9.0, "文笔": 9.0})}, required=1,
                        is_selection=True, issues=issues, selection=sel)
        self.assertEqual(["缺少交付清单"],
                         [i["note"] for i in out.issues_all],
                         "包完整性问题没进 issues")
        self.assertFalse(out.publishable)

    def test_gate_does_not_mutate_reviews(self):
        reviews = {"a": seat({"情节": 8.0, "文笔": 8.0})}
        snapshot = {"a": dict(reviews["a"], scores=dict(reviews["a"]["scores"]))}
        self.gate(reviews)
        self.assertEqual(snapshot, reviews, "门禁改写了各席评审结果")


class ReviseRoundTests(unittest.TestCase):
    """修订段：吃到本轮意见才叫修订，否则只是重写一遍。"""

    def _run(self, mode="mock", means=None):
        st = SimpleNamespace(
            agents=[], bestof_improvements="", difficulty="default",
            dims=DIMS, ev=None, impl={"id": "a", "mode": mode, "label": "A"},
            ms_name="manuscript.md", resume_ctx=None, run_id="r-1",
            task={"type": "novel", "goal": "写故事", "workdir": "w"},
            threshold=7.0, workdir="w", write_ms=mock.Mock(),
            ability=None)
        with mock.patch.object(pipeline.mocks, "draft_manuscript",
                               return_value="REVISED") as dm, \
                mock.patch.object(pipeline.time, "sleep", lambda *a, **k: None), \
                mock.patch.object(pipeline.store, "add_step",
                                  return_value=({"n": 1}, mock.Mock())), \
                mock.patch.object(pipeline.store, "finish_step"):
            out = pipeline._content_revise_round(
                st, 1, "原稿", means if means is not None else {"情节": 5.0},
                [{"dim": "情节", "severity": "major", "note": "开篇太慢",
                  "chapter": "全稿评审"}])
        return st, out, dm

    def test_mock_revise_writes_new_manuscript(self):
        st, out, dm = self._run()
        self.assertEqual("REVISED", st.write_ms.call_args[0][0])
        dm.assert_called_once()
        self.assertEqual("manuscript.md", out.ms_name, "上游字段被截断")

    def test_revise_sees_round_means(self):
        st, out, _ = self._run(means={"情节": 3.0})
        self.assertEqual({"情节": 3.0}, out.means)


if __name__ == "__main__":
    unittest.main()
