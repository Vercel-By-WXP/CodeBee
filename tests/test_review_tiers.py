# -*- coding: utf-8 -*-
"""评审强度分档（review_tiers / task_compile / router.pick_critics）单测。

P1-3 要锁死的几条业务规则：

1. **分档表是唯一来源**——哪个流程值几双眼睛只看这张表 + 用户配置，
   不看 `agents.seats` 这个展示字段。旧逻辑硬编码 `min(2,...)` /
   `AUTO_CRITIC_LIMIT=2`，导致短篇/标书只有一席、三席档永远两席。
2. **档位管席数，不管轮数**——轻量档（verify/single）强制 1 轮是省「再评一遍」
   的往返；`rank_scan` 写 3 轮是有原因的，档位压不成 2 轮。
3. **覆盖优先级**：flows.json 的 `review_tier` > agents 的 `seats` > 内置表
   > 保守兜底（未知流程按 lean）。
4. **第三席是读者视角**——`seat_brief(>=2)` 多出「读者会在哪一段放下这本书」，
   前两席返回空串（= 现状行为，零回归）。
5. **manual 模式不替用户加人**——`reviewers: 0` = 「按你点的人来」。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.flows as flows
import core.paths as paths
from core import review_tiers, task_compile


# ---- 内置分档期望，作为可审计的契约
EXPECT_BUILTIN = {
    # verify：有确定性验证，模型评审只在缺验证时兜底
    "code": ("verify", 1, 1, False),
    "direct": ("verify", 1, 1, False),
    "defect_retro": ("verify", 1, 1, False),
    # single：轻量内容，判宽了没人受伤
    "email": ("single", 1, 1, False),
    "weekly_report": ("single", 1, 1, False),
    "translation": ("single", 1, 1, False),
    # lean：中等内容，单席但给回炉
    "article": ("lean", 1, 2, False),
    "video_script": ("lean", 1, 2, False),
    "doc": ("lean", 1, 2, False),
    "speech": ("lean", 1, 2, False),
    "presentation": ("lean", 1, 2, False),
    "resume": ("lean", 1, 2, False),
    "handdraw_video": ("lean", 1, 2, False),
    # dual：判错了要返工 / 平台会拒
    "serial_novel": ("dual", 2, 2, True),
    "research": ("dual", 2, 2, True),
    "tech_proposal": ("dual", 2, 2, True),
    "short_drama": ("dual", 2, 2, True),
    "bid_doc": ("dual", 2, 2, True),
    "rank_scan": ("dual", 2, 2, True),
    # panel：平台按签约标准判，拒一次损失整本
    "novel": ("panel", 3, 2, True),
    "short_story": ("panel", 3, 2, True),
}


class TierOfBuiltinTest(unittest.TestCase):
    def test_every_builtin_flow(self):
        for flow, (tier, seats, rounds, outline) in EXPECT_BUILTIN.items():
            spec = review_tiers.tier_of({"type": flow})
            self.assertEqual(spec["tier"], tier, flow)
            self.assertEqual(spec["seats"], seats, flow)
            self.assertEqual(spec["rounds"], rounds, flow)
            self.assertEqual(spec["outline"], outline, flow)
            self.assertEqual(spec["source"], "builtin", flow)

    def test_unknown_flow_falls_back_to_lean(self):
        spec = review_tiers.tier_of({"type": "some_totally_new_flow"})
        self.assertEqual(spec["tier"], "lean")
        self.assertEqual(spec["seats"], 1)
        self.assertEqual(spec["rounds"], 2)
        self.assertFalse(spec["outline"])

    def test_spec_has_audit_keys(self):
        spec = review_tiers.tier_of({"type": "novel"})
        for key in ("tier", "tier_label", "why", "source", "flow",
                    "seats", "rounds", "outline"):
            self.assertIn(key, spec)

    def test_non_dict_task_is_safe(self):
        spec = review_tiers.tier_of(None)
        self.assertEqual(spec["tier"], "lean")


class RoundsRuleTest(unittest.TestCase):
    """档位管席数不管轮数；轻量档强制 1 轮；其余尊重用户写的轮数。"""

    def test_verify_forces_one_round_regardless_of_ask(self):
        # code 是 verify 档，用户写 rounds=5 也必须压成 1（省再评一遍的往返）
        spec = review_tiers.tier_of({"type": "code", "rounds": 5})
        self.assertEqual(spec["rounds"], 1)

    def test_single_forces_one_round_regardless_of_ask(self):
        spec = review_tiers.tier_of({"type": "email", "rounds": 9})
        self.assertEqual(spec["rounds"], 1)

    def test_lean_respects_asked_rounds(self):
        spec = review_tiers.tier_of({"type": "article", "rounds": 4})
        self.assertEqual(spec["rounds"], 4)

    def test_lean_default_rounds_when_unasked(self):
        spec = review_tiers.tier_of({"type": "article"})
        self.assertEqual(spec["rounds"], 2)

    def test_panel_respects_asked_rounds(self):
        spec = review_tiers.tier_of({"type": "novel", "rounds": 1})
        self.assertEqual(spec["rounds"], 1)

    def test_panel_default_rounds_when_unasked(self):
        spec = review_tiers.tier_of({"type": "novel"})
        self.assertEqual(spec["rounds"], 2)


class GlobalSeatsTest(unittest.TestCase):
    def test_serial_novel_global_is_panel(self):
        # 逐章 dual(2) 但终局升 panel(3)
        self.assertEqual(review_tiers.global_seats({"type": "serial_novel"}), 3)

    def test_novel_global_equals_its_seats(self):
        self.assertEqual(review_tiers.global_seats({"type": "novel"}), 3)

    def test_doc_global_equals_its_seats(self):
        self.assertEqual(review_tiers.global_seats({"type": "doc"}), 1)

    def test_code_global_equals_its_seats(self):
        self.assertEqual(review_tiers.global_seats({"type": "code"}), 1)


class SeatBriefTest(unittest.TestCase):
    def test_first_two_seats_no_extra_brief(self):
        self.assertEqual(review_tiers.seat_brief(0), "")
        self.assertEqual(review_tiers.seat_brief(1), "")

    def test_third_seat_is_reader_view(self):
        brief = review_tiers.seat_brief(2)
        self.assertIn("读者", brief)
        self.assertIn("放下", brief)

    def test_seat_brief_does_not_leak_into_first_seats(self):
        # 保证前两席返回空串不是偶然（回归：第三席只挂一个调用点）
        for i in (0, 1):
            self.assertFalse(review_tiers.seat_brief(i))


class OverrideByFlowTierTest(unittest.TestCase):
    """flows.json 的 review_tier 直接指定档位（source=flow），优先级最高。"""

    def test_review_tier_promotes_doc_to_panel(self):
        with mock.patch.object(flows, "get_flow",
                               return_value={"id": "doc", "review_tier": "panel"}):
            spec = review_tiers.tier_of({"type": "doc"})
        self.assertEqual(spec["tier"], "panel")
        self.assertEqual(spec["seats"], 3)
        self.assertEqual(spec["source"], "flow")

    def test_invalid_review_tier_ignored(self):
        with mock.patch.object(flows, "get_flow",
                               return_value={"id": "doc", "review_tier": "nope"}):
            spec = review_tiers.tier_of({"type": "doc"})
        self.assertEqual(spec["tier"], "lean")  # 回退内置表
        self.assertEqual(spec["source"], "builtin")


class OverrideByAgentSeatsTest(unittest.TestCase):
    """`data/agents/<flow>.json` 的 seats 反查档位（source=agent）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)
        (self._root / "agents").mkdir(parents=True, exist_ok=True)
        self._saved = paths.DATA_DIR
        paths.DATA_DIR = self._root

    def tearDown(self):
        paths.DATA_DIR = self._saved
        self._tmp.cleanup()

    def _write_agent(self, flow, seats):
        (self._root / "agents" / ("%s.json" % flow)).write_text(
            json.dumps({"seats": seats}, ensure_ascii=False),
            encoding="utf-8")

    def test_seats_3_promotes_to_panel(self):
        self._write_agent("doc", 3)  # doc 内置 lean(1)
        spec = review_tiers.tier_of({"type": "doc"})
        self.assertEqual(spec["tier"], "panel")
        self.assertEqual(spec["seats"], 3)
        self.assertEqual(spec["source"], "agent")

    def test_seats_2_maps_to_dual(self):
        self._write_agent("doc", 2)
        spec = review_tiers.tier_of({"type": "doc"})
        self.assertEqual(spec["tier"], "dual")
        self.assertEqual(spec["seats"], 2)

    def test_seats_1_maps_to_single_not_verify(self):
        self._write_agent("doc", 1)
        spec = review_tiers.tier_of({"type": "doc"})
        self.assertEqual(spec["tier"], "single")
        self.assertEqual(spec["seats"], 1)


class OverridePriorityTest(unittest.TestCase):
    """flow review_tier 必须压过 agents seats。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)
        (self._root / "agents").mkdir(parents=True, exist_ok=True)
        (self._root / "agents" / "doc.json").write_text(
            json.dumps({"seats": 3}, ensure_ascii=False), encoding="utf-8")
        self._saved = paths.DATA_DIR
        paths.DATA_DIR = self._root

    def tearDown(self):
        paths.DATA_DIR = self._saved
        self._tmp.cleanup()

    def test_flow_tier_beats_agent_seats(self):
        # agents 写 3（panel），但 flow 指定 single → 取 single
        with mock.patch.object(flows, "get_flow",
                               return_value={"id": "doc", "review_tier": "single"}):
            spec = review_tiers.tier_of({"type": "doc"})
        self.assertEqual(spec["tier"], "single")
        self.assertEqual(spec["source"], "flow")


class ContentWorkflowIntegrationTest(unittest.TestCase):
    """content_workflow 的席/轮基线必须来自 review_tiers。"""

    def test_auto_doc_is_single_lean(self):
        wf = task_compile.content_workflow({"type": "doc"}, "default", mode="auto")
        self.assertEqual(wf["reviewers"], 1)
        self.assertEqual(wf["review_rounds"], 2)
        self.assertFalse(wf["outline"])

    def test_auto_novel_is_panel_three_seats(self):
        wf = task_compile.content_workflow({"type": "novel"}, "default", mode="auto")
        self.assertEqual(wf["reviewers"], 3)
        self.assertEqual(wf["review_rounds"], 2)
        self.assertTrue(wf["outline"])

    def test_auto_short_story_is_panel(self):
        # 关键修复：原来短篇只有一席，现在三席
        wf = task_compile.content_workflow({"type": "short_story"}, "default",
                                           mode="auto")
        self.assertEqual(wf["reviewers"], 3)

    def test_rank_scan_keeps_min_two_seats_and_rounds(self):
        wf = task_compile.content_workflow({"type": "rank_scan"}, "default",
                                           mode="auto")
        self.assertGreaterEqual(wf["reviewers"], 2)
        self.assertGreaterEqual(wf["review_rounds"], 2)
        self.assertTrue(wf["outline"])

    def test_fast_mode_collapses_to_one(self):
        wf = task_compile.content_workflow({"type": "novel"}, "default", mode="fast")
        self.assertEqual(wf["reviewers"], 1)
        self.assertEqual(wf["review_rounds"], 1)
        self.assertFalse(wf["outline"])

    def test_expert_mode_at_least_two_seats(self):
        wf = task_compile.content_workflow({"type": "doc"}, "default", mode="expert")
        self.assertGreaterEqual(wf["reviewers"], 2)
        self.assertTrue(wf["outline"])

    def test_manual_mode_respects_user_zero(self):
        wf = task_compile.content_workflow({"type": "novel"}, "default",
                                           mode="manual")
        self.assertEqual(wf["reviewers"], 0)
        self.assertTrue(wf["reason"].startswith("手动模式"))

    def test_high_threshold_forces_dual_minimum(self):
        # 单席 lean 档（doc）遇到 8.0 门槛也要至少双席
        wf = task_compile.content_workflow(
            {"type": "doc", "threshold": 8.0}, "default", mode="auto")
        self.assertGreaterEqual(wf["reviewers"], 2)

    def test_planner_cannot_raise_seats(self):
        # 规划器只能在基线以内降，不能凭空升（doc 基线 1）
        wf = task_compile.content_workflow(
            {"type": "doc"}, "default",
            plan={"workflow": {"reviewers": 5, "review_rounds": 5}}, mode="auto")
        self.assertEqual(wf["reviewers"], 1)


class PickCriticsLimitTest(unittest.TestCase):
    """pick_critics 的 limit 由档位驱动，真正封顶评审组规模。"""

    def _agents(self, n):
        return [{"id": "a%d" % i, "kind": "generic", "mode": "real",
                 "provider_id": ""} for i in range(n)]

    def test_limit_caps_critic_count(self):
        from core import router
        agents = self._agents(5)
        chosen, _note = router.pick_critics(agents, "doc", limit=2)
        self.assertLessEqual(len(chosen), 2)

    def test_no_limit_uses_auto_cap(self):
        from core import router
        agents = self._agents(5)
        chosen, _note = router.pick_critics(agents, "doc", limit=None)
        self.assertLessEqual(len(chosen), router.AUTO_CRITIC_LIMIT)

    def test_limit_minimum_one(self):
        from core import router
        agents = self._agents(3)
        chosen, _note = router.pick_critics(agents, "doc", limit=0)
        self.assertGreaterEqual(len(chosen), 1)


class ReportHelpersTest(unittest.TestCase):
    """table / estimate / baseline / describe_all 结构稳定、可复算。"""

    def test_table_returns_rows(self):
        rows = review_tiers.table()
        self.assertTrue(rows)
        for r in rows:
            for key in ("flow", "tier", "seats", "rounds", "outline",
                        "global_seats", "why", "source"):
                self.assertIn(key, r)

    def test_estimate_adds_review_calls(self):
        rows = review_tiers.estimate()
        for r in rows:
            self.assertIn("review_calls", r)
            self.assertIn("review_calls_best", r)
            self.assertGreaterEqual(r["review_calls"], r["seats"])

    def test_baseline_has_dual_reference(self):
        out = review_tiers.baseline()
        self.assertIn("base", out)
        self.assertEqual(out["base"], review_tiers.TIERS["dual"]["label"])
        for r in out["rows"]:
            self.assertIn("vs_dual", r)
            self.assertIn("vs_dual_best", r)

    def test_describe_all_is_string_lines(self):
        lines = review_tiers.describe_all()
        self.assertTrue(lines)
        self.assertTrue(all(isinstance(x, str) for x in lines))


if __name__ == "__main__":
    unittest.main()
