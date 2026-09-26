# -*- coding: utf-8 -*-
"""候选打分口径回归：标准「候选打分权重表」的每个数值必须与代码常量同源。

背景（2026-09-24）：`docs/execution-standard.md` 原先只成文了在线信号的软加减分
上限，而 CLI 能力基线、绑定链加减分、历史胜率公式、亲和度矩阵、档位表、
priority 折算和配额惩罚这套静态权重只活在 `router.py` / `dispatch.py` 里。
文档不收编就有两处无人核对的断言：第 5 条"P95 惩罚 -18 与 CLI 静态能力差同量级"
里的"静态能力差"本身没有出处；调一次权重没有任何门禁会要求同步口径。
手法沿用 `test_cost_budget_standard` 与 i18n 重复键扫描（文档—实现契约守卫）；
选路运行行为本身另由既有路由测试覆盖，这里只锁对应关系。
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from base import BaseTest

SECTION = "## 候选打分权重表"


def _doc():
    root = Path(__file__).resolve().parents[1]
    return (root / "docs" / "execution-standard.md").read_text(encoding="utf-8")


def _section():
    return _doc().split(SECTION, 1)[1].split("\n## ", 1)[0]


def _row(prefix):
    """取权重表里以 prefix 开头的那一行，供数值断言用。"""
    for line in _section().splitlines():
        if line.startswith(prefix):
            return line
    raise AssertionError("文档缺少以 %r 开头的表格行" % prefix)


class TestSectionExists(BaseTest):
    def test_section_and_impl_row_exist(self):
        text = _doc()
        self.assertIn(SECTION, text)
        self.assertIn("| 候选打分权重 |", text)

    def test_intro_declares_score_governs_ranking_only(self):
        section = _section()
        self.assertIn("**分值表治理的是排序，不是结论**", section)
        self.assertIn("「评测层级表」", section)

    def test_cost_section_cross_references_weights(self):
        cost = _doc().split("## 成本与时延预算", 1)[1].split("\n## ", 1)[0]
        self.assertIn("静态分项", cost)
        self.assertIn("候选打分权重表", cost)


class TestCapabilityTableMatchesCode(BaseTest):
    def test_every_kind_and_baseline_is_written_down(self):
        from app.core import router

        row = _row("| 能力基线 |")
        for kind, value in router.CAPABILITY.items():
            with self.subTest(kind=kind):
                self.assertRegex(row, r"%s[^|]*%d" % (re.escape(kind), value))

    def test_unknown_kind_default_matches_source(self):
        from app.core import router

        src = inspect.getsource(router.score)
        self.assertIn('CAPABILITY.get(agent.get("kind"), 60)', src)
        self.assertIn("未知 kind 60", _row("| 能力基线 |"))

    def test_binding_bonus_values_match_source(self):
        from app.core import router

        src = inspect.getsource(router._binding_bonus)
        self.assertIn("return 8.0,", src)
        self.assertIn("-25.0,", src)
        row = _row("| 绑定链 |")
        self.assertIn("≥2 条 +8", row)
        self.assertIn("空链 -25", row)
        # 自动分发模式下空绑定不扣分：这一支必须在代码里存在
        self.assertIn("if dispatch_mode else", src)
        self.assertIn("空链记 0 不扣 25", row)

    def test_history_formula_constants_match_doc(self):
        from app.core import router

        src = inspect.getsource(router._history_bonus)
        self.assertIn("24.0 * (1.0 - win_rate) * min(1.0, runs / 3.0)", src)
        self.assertIn("18.0 * win_rate + min(6.0, runs)", src)
        row = _row("| 历史战绩 |")
        self.assertIn("18×胜率", row)
        self.assertIn("min(6, 次数)", row)
        self.assertIn("24×(1−胜率)", row)
        self.assertIn("min(1, 次数/3)", row)

    def test_quota_penalty_cap_match_doc(self):
        from app.core import router

        self.assertIn("-45.0 * ratio", inspect.getsource(router.score))
        row = _row("| 配额惩罚 |")
        self.assertIn("-45 ×", row)
        self.assertIn("不得记成模型质量失败", row)


class TestAffinityAndOnlineWeightsMatchDoc(BaseTest):
    def test_affinity_range_and_dimension_list(self):
        from app.core import dispatch

        values = [v for row in dispatch._KIND_AFFINITY.values() for v in row.values()]
        self.assertEqual(0, min(values))
        self.assertEqual(10, max(values))
        row = _row("| 能力亲和度 |")
        self.assertIn("0…10", row)
        for dimension in dispatch._KIND_AFFINITY:
            with self.subTest(dimension=dimension):
                self.assertIn(dimension, row)

    def test_review_bonus_values(self):
        from app.core import dispatch

        src = inspect.getsource(dispatch.agent_affinity)
        self.assertIn('{"claude": 4, "codex": 3, "qwen": 2}', src)
        self.assertIn("+4(claude)/+3(codex)/+2(qwen)", _row("| 能力亲和度 |"))

    def test_agent_side_online_caps(self):
        from app.core import router

        src = inspect.getsource(router._online_bonus)
        for needle in ("min(8.0", "min(18.0", "min(4.0"):
            with self.subTest(needle=needle):
                self.assertIn(needle, src)
        row = _row("| 在线信号 | 成功率 ±8")
        for marker in ("成功率 ±8", "P95 延迟 ≤-18", "均价 ≤-4"):
            with self.subTest(marker=marker):
                self.assertIn(marker, row)

    def test_model_side_online_caps(self):
        from app.core import dispatch

        src = inspect.getsource(dispatch._online_model_bonus)
        for needle in ("min(6.0", "min(12.0", "min(3.0"):
            with self.subTest(needle=needle):
                self.assertIn(needle, src)
        rows = [line for line in _section().splitlines()
                if line.startswith("| 在线信号 |")]
        self.assertEqual(2, len(rows), "CLI 与模型链各应有一行在线信号")
        self.assertIn("成功率 ±6", rows[1])
        self.assertIn("P95 ≤-12", rows[1])
        self.assertIn("均价 ≤-3", rows[1])


class TestModelChainWeightsMatchDoc(BaseTest):
    def test_priority_quality_formula(self):
        from app.core import dispatch

        src = inspect.getsource(dispatch.score_model_entry)
        self.assertIn("max(-8.0, 20.0 - (priority - 1) * 4.0)", src)
        self.assertIn("quality *= 0.25", src)
        self.assertIn("quality *= 0.65", src)
        row = _row("| 质量（由 priority 折算） |")
        self.assertIn("easy ×0.25", row)
        self.assertIn("default ×0.65", row)
        self.assertIn("hard ×1.0", row)
        # priority 的真实语义是列表排位，文档必须点破，否则会被读成能力实测
        self.assertIn("排位", row)

    def test_tier_table_values(self):
        from app.core import dispatch

        cell = _row("| 档位 |").split("|")[2]
        segments = {}
        for part in cell.split("；"):
            for difficulty in dispatch._TIER_SCORE:
                if part.strip().startswith(difficulty):
                    segments[difficulty] = part
        self.assertEqual(sorted(dispatch._TIER_SCORE), sorted(segments),
                         "文档必须为每个难度档各写一段")
        for difficulty, tiers in dispatch._TIER_SCORE.items():
            segment = segments[difficulty]
            for tier, value in tiers.items():
                rendered = ("+%d" if value >= 0 else "-%d") % abs(int(value))
                with self.subTest(difficulty=difficulty, tier=tier):
                    self.assertIn(tier, segment)
                    self.assertIn(rendered, segment)

    def test_price_formula_and_unknown_price(self):
        from app.core import dispatch

        src = inspect.getsource(dispatch._price_score)
        self.assertIn("math.log10(blended + 1.0)", src)
        self.assertIn('2.0 * float(price.get("out")', src)
        self.assertIn('7.0 if difficulty == "easy" else 2.0', src)
        row = _row("| 价格 |")
        self.assertIn("log10(in + 2×out + 1)", row)
        self.assertIn("7.0 若 easy 否则 2.0", row)
        self.assertIn("不得猜价", row)

    def test_strength_and_vision_weights(self):
        from app.core import dispatch

        src = inspect.getsource(dispatch.score_model_entry)
        self.assertIn("10.0 if dim in strengths else 0.0", src)
        self.assertIn('24.0 if meta.get("image_in") else -24.0', src)
        self.assertIn("+10", _row("| 能力命中 |"))
        vision = _row("| 视觉 |")
        self.assertIn("真 +24、假 -24", vision)
        self.assertIn("以实测评测为准", vision)

    def test_bench_row_matches_code(self):
        """实测（评测台）分项：文档数值与 benchstore 代码常量逐格一致。"""
        from app.core import benchstore, dispatch

        row = _row("| 实测（评测台） |")
        self.assertIn("(overall − 7.0) × 1.5", row)
        self.assertIn("±4.5", row)
        self.assertIn("14 天", row)
        self.assertIn("过期记 0", row)
        self.assertIn("无数据记 0", row)
        # 代码常量与表一致
        self.assertEqual(benchstore._BASELINE, 7.0)
        self.assertEqual(benchstore._K, 1.5)
        self.assertEqual(benchstore._MAX_BONUS, 4.5)
        self.assertEqual(benchstore.FRESH_DAYS, 14)
        # 分项真的进了总分与理由（理由是权重的唯一可观测面）
        src = inspect.getsource(dispatch.score_model_entry)
        self.assertIn("_bench_bonus(entry)", src)
        self.assertIn("bench_score", src)

    def test_rerank_only_under_easy_or_hard(self):
        from app.core import dispatch

        src = inspect.getsource(dispatch.rank_model_entries)
        self.assertIn('if difficulty in ("easy", "hard") or force:', src)
        self.assertIn("**default 难度必须保持用户配置的原始顺序**", _section())

    def test_chain_cap_and_no_premature_truncation(self):
        from app.core import modelhub

        self.assertEqual(8, modelhub.MAX_CHAIN_ATTEMPTS)
        section = _section()
        self.assertIn("`MAX_CHAIN_ATTEMPTS`（8，按模型×KEY 展开计）", section)
        self.assertIn("不得先截前三条", section)


class TestProvidersAreNotScored(BaseTest):
    """厂商只做状态闸；一旦有人加厂商分，本节口径必须同步改写。"""

    def test_doc_states_provider_is_not_scored(self):
        self.assertIn("**厂商与供应商不打分**", _section())

    def test_health_and_evaluation_have_no_score_axis(self):
        from app.core import evaluation, health

        for module in (health, evaluation):
            src = inspect.getsource(module).lower()
            with self.subTest(module=module.__name__):
                self.assertNotIn("score", src)

    def test_health_state_machine_names_are_in_doc(self):
        from app.core import health

        section = _section()
        for name, value in (("FAILING_AFTER", 2), ("DOWN_AFTER_FAILURES", 4),
                            ("DOWN_AFTER_SECONDS", 600)):
            with self.subTest(name=name):
                self.assertEqual(value, getattr(health, name))
        self.assertIn("ok → failing → down → recovered", section)


class TestHardRulesStayOutOfScores(BaseTest):
    """可否决排序的规则必须在文档里以硬规则形态存在，不得降格为加减分。"""

    def test_cross_vendor_reviewer_is_hard_rule(self):
        from app.core import router

        self.assertIn("a.get(\"kind\") != impl_kind",
                      inspect.getsource(router.pick_reviewer))
        self.assertIn("跨 CLI 家族", _section())

    def test_same_upstream_deferral_is_hard_rule(self):
        from app.core import router

        src = inspect.getsource(router.pick_switch_candidate)
        self.assertIn("blocked_upstreams", inspect.signature(
            router.pick_switch_candidate).parameters)
        self.assertIn("deferred", src)
        section = _section()
        self.assertIn("403 明确拒绝的上游始终剔除", section)
        self.assertIn("延后让位", section)

    def test_critic_pool_limit(self):
        from app.core import router

        self.assertEqual(2, router.AUTO_CRITIC_LIMIT)
        self.assertIn("`AUTO_CRITIC_LIMIT`", _section())


class TestAdjustmentDisciplineWritten(BaseTest):
    def test_discipline_items_exist(self):
        section = _section()
        for marker in ("改动本节任一数值必须在同一提交内更新本表",
                       "保留理由字符串里的分项回显",
                       "先回答它能否被硬约束否决",
                       "不得只删文档留码"):
            with self.subTest(marker=marker):
                self.assertIn(marker, section)

    def test_reason_strings_are_emitted_by_code(self):
        """文档声称理由是这套权重的可观测面——代码必须真的回显分项。"""
        from app.core import dispatch, router

        self.assertIn("能力基线 %d", inspect.getsource(router.score))
        self.assertIn("总分", inspect.getsource(router.score))
        self.assertIn("质量 %+.1f", inspect.getsource(dispatch.score_model_entry))
        self.assertIn("样本层", inspect.getsource(router._online_bonus))


if __name__ == "__main__":
    import unittest
    unittest.main()
