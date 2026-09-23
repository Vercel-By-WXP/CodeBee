# -*- coding: utf-8 -*-
"""branching 压力守卫单测——token 压力 >0.7 时跳过推演。

跑法：python -m unittest discover -s tests -p "test_branch_pressure.py" -v
"""
from __future__ import annotations

from unittest import mock

from base import BaseTest


class PressureGuardTests(BaseTest):
    def test_high_pressure_skips(self):
        """token 压力 >0.7 → 跳过推演（不调编排者）。"""
        from app.core import branching, modelhub
        with mock.patch("app.core.token_meter") as tm, \
             mock.patch.object(modelhub, "resolve_orchestrator") as orch:
            tm.pressure_ratio.return_value = 0.85
            got = branching.plan_branches("r-x", {}, 1, "g", "o", "p", 2)
        self.assertIsNone(got)
        orch.assert_not_called()   # 压力高时连编排者都不解析

    def test_low_pressure_proceeds(self):
        """压力 ≤0.7 → 正常走推演路径（编排者被调用）。"""
        from app.core import branching, modelhub
        with mock.patch("app.core.token_meter") as tm, \
             mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=None) as orch:
            tm.pressure_ratio.return_value = 0.3
            branching.plan_branches("r-x", {}, 1, "g", "o", "p", 2)
        orch.assert_called_once()  # 正常解析了编排者

    def test_meter_unavailable_proceeds(self):
        """token_meter 异常 → 守卫不拦（宁可多花一次推演，不因守卫炸掉）。"""
        from app.core import branching, modelhub
        with mock.patch("app.core.token_meter") as tm, \
             mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=None) as orch:
            tm.pressure_ratio.side_effect = RuntimeError("meter broken")
            branching.plan_branches("r-x", {}, 1, "g", "o", "p", 2)
        orch.assert_called_once()

    def test_disabled_n1_never_reaches_meter(self):
        """n=1（推演关闭）在最前面短路，不会碰 token_meter。"""
        from app.core import branching
        with mock.patch("app.core.token_meter") as tm:
            branching.plan_branches("r-x", {}, 1, "g", "o", "p", 1)
        tm.pressure_ratio.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
