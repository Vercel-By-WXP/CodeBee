# -*- coding: utf-8 -*-
"""类型注册表对账（2026-10-06 第 3/4 步落地班·提案甲）。

巡检发现三处对账缺口：dispatch.TYPE_DIMENSIONS 漏 defect_retro（长期靠
兜底回落）且残留 flows 已不存在的 "zentao" 死映射；usage._DURATION_BASELINES
缺 defect_retro/presentation/bid_doc 三型（跑前预估回落 480s 常数失真）。
本组把「调度维度表与耗时基线表必须与 flows 注册表一一对应」锁成契约：
日后加型漏配、删型留尸，在此先红，倒逼同步台账。

跑法：python -m unittest discover -s tests -p "test_type_registry_audit.py" -v
"""
from __future__ import annotations

from base import BaseTest


class TypeRegistryAuditTests(BaseTest):
    """TYPE_DIMENSIONS / _DURATION_BASELINES 与 flows 注册表一一对应。"""

    def _flow_ids(self):
        from app.core import flows
        return {f["id"] for f in flows.BUILTIN_FLOWS}

    def test_dimensions_match_registry_no_extra(self):
        """维度表键集合 == 注册表 id 集合：无漏（不再靠兜底）无多（无死映射）。"""
        from app.core import dispatch
        self.assertEqual(set(dispatch.TYPE_DIMENSIONS), self._flow_ids())
        # 值域必须落在亲和表键域内，否则 agent_affinity 对该型恒 0 分
        for dim in dispatch.TYPE_DIMENSIONS.values():
            self.assertIn(dim, dispatch._KIND_AFFINITY)

    def test_duration_baselines_match_registry_no_extra(self):
        """耗时基线表键集合 == 注册表 id 集合，值均为正秒数。

        缺型时 estimate() 回落 480s 常数，跑前预估失真；多余键则暗示
        注册表删型留尸（同 zentao 死映射的事故形态）。
        """
        from app.core import usage
        self.assertEqual(set(usage._DURATION_BASELINES), self._flow_ids())
        for name, seconds in usage._DURATION_BASELINES.items():
            self.assertIsInstance(seconds, int, name)
            self.assertGreater(seconds, 0, name)

    def test_new_baseline_values_locked(self):
        """提案甲取值锁定：presentation=480 / bid_doc=720 / defect_retro=240。

        bid_doc 对齐 tech_proposal（同为长文档应答），presentation 对齐
        article/doc（单篇产出），defect_retro 对齐 rank_scan（240s 量级）。
        """
        from app.core import usage
        self.assertEqual(usage._DURATION_BASELINES["presentation"], 480)
        self.assertEqual(usage._DURATION_BASELINES["bid_doc"], 720)
        self.assertEqual(usage._DURATION_BASELINES["defect_retro"], 240)

    def test_defect_retro_explicit_and_fallback_unchanged(self):
        """defect_retro 显式映射生效（不带 role 也命中表项）；既有行为不变。

        既有锚点：code→coding / novel→writing / direct→reasoning 保持原值；
        未知类型仍回落 reasoning（兜底路径不受对账影响）。
        """
        from app.core import dispatch
        self.assertEqual(dispatch.task_dimension("defect_retro", role=""),
                         "reasoning")
        self.assertEqual(dispatch.task_dimension("code"), "coding")
        self.assertEqual(dispatch.task_dimension("novel"), "writing")
        self.assertEqual(dispatch.task_dimension("direct"), "reasoning")
        self.assertEqual(dispatch.task_dimension("no_such_type"), "reasoning")


if __name__ == "__main__":
    import unittest
    unittest.main()
