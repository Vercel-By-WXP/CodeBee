# -*- coding: utf-8 -*-
"""settings_v2 消费路线单测：三个消费者（pipeline 压缩总开关 / step_runner
压缩调参 / goal_service 轮数上限）读 settings_v2 且未配置时零行为变化。

跑法：python -m unittest discover -s tests -p "test_settings_v2_consumers.py" -v
不碰真实 data/：settings_schema.init 显式指到本模块临时目录（不受其他测试
模块的 TUTTI_DATA 抢设影响——init 是公开入口，可重指）。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("TUTTI_DATA", tempfile.mkdtemp(prefix="tutti-v2c-"))
os.environ.pop("TUTTI_COMPACTION", None)     # 总开关测试要求 env 起点为关
sys.path.insert(0, str(ROOT / "app"))

from core import goal_service, pipeline, settings_schema, step_runner  # noqa: E402

V2DIR = tempfile.mkdtemp(prefix="tutti-v2c-file-")


def _reset_v2(*pairs):
    """重指 settings_v2 文件并重注册默认 namespace；pairs=(ns, [(path, value)…])。"""
    settings_schema.init(V2DIR)
    # init 清了注册表：重注册（幂等入口即本函数）
    settings_schema._NAMESPACES.clear()
    settings_schema.register_default_namespaces()
    for ns, ops in pairs:
        settings_schema.mutate(ns, [{"op": "set", "path": p, "value": v}
                                    for p, v in ops])


class TestCompactionEnabled(unittest.TestCase):
    def test_default_off(self):
        _reset_v2()
        self.assertFalse(pipeline._compaction_enabled())

    def test_v2_on(self):
        _reset_v2(("orchestrator", [("compaction.enabled", True)]))
        self.assertTrue(pipeline._compaction_enabled())

    def test_env_on_overrides_v2_off(self):
        _reset_v2()
        os.environ["TUTTI_COMPACTION"] = "1"
        try:
            self.assertTrue(pipeline._compaction_enabled())
        finally:
            os.environ.pop("TUTTI_COMPACTION", None)


class TestStepRunnerTuning(unittest.TestCase):
    def test_defaults_none_when_unconfigured(self):
        _reset_v2()
        th, rt = step_runner._v2_compaction_tuning()
        # 默认值已注册进 v2（0.8/8000）：助手回读注册默认而非 None
        self.assertAlmostEqual(th, 0.8)
        self.assertEqual(rt, 8000)

    def test_reads_mutated_values(self):
        _reset_v2(("orchestrator", [("compaction.pressure_threshold", 0.5),
                                    ("compaction.retain_tail_tokens", 1234)]))
        th, rt = step_runner._v2_compaction_tuning()
        self.assertAlmostEqual(th, 0.5)
        self.assertEqual(rt, 1234)

    def test_zero_retain_is_meaningful(self):
        # 0 = 尾部不保留（与 None 未配置语义不同，助手不得把 0 吞成默认）
        _reset_v2(("orchestrator", [("compaction.retain_tail_tokens", 0)]))
        _th, rt = step_runner._v2_compaction_tuning()
        self.assertEqual(rt, 0)


class TestGoalRoundsMax(unittest.TestCase):
    def test_default_reads_v2(self):
        _reset_v2(("orchestrator", [("max_goal_rounds", 9)]))
        self._ensure_no_current_goal()
        g = goal_service.create("v2 轮数测试")
        self.assertEqual(g["rounds_max"], 9)

    def _ensure_no_current_goal(self):
        cur = goal_service.current()
        if cur is not None:
            goal_service.abandon(cur["goal_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
