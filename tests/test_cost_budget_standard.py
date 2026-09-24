# -*- coding: utf-8 -*-
"""成本与时延口径回归：标准写下的计量字段、量纲区分和旋钮名必须与代码一致。

背景（2026-09-24 复查）：`docs/execution-standard.md` 原先只在失败面提到 token
（`MAX_TOKENS`/`CONTEXT_OVERFLOW`），耗时面只写了超时预算；而用量台账、
per-run 预算闸、路由的 P95/均价打分早已落地。文档不收编就会退化成"第二套记忆"，
本节按 `test_error_codes.test_execution_standard_matrix_matches_enum` 的同一手法
把口径钉成可执行契约。运行行为本身已有 `test_token_meter`、
`test_token_cost_t2t3` 覆盖，这里只锁文档与实现的对应关系。
"""
from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from base import BaseTest

SECTION = "## 成本与时延预算"


def _doc():
    root = Path(__file__).resolve().parents[1]
    return (root / "docs" / "execution-standard.md").read_text(encoding="utf-8")


def _section():
    return _doc().split(SECTION, 1)[1].split("\n## ", 1)[0]


class TestCostSectionIsWritten(unittest.TestCase):
    def test_section_and_impl_row_exist(self):
        text = _doc()
        self.assertIn(SECTION, text)
        self.assertIn("| 成本与时延计量 |", text)

    def test_attempt_record_carries_token_and_cost(self):
        row = _doc().split("4. **共享预算并记录尝试**", 1)[1].split("\n", 1)[0]
        for field in ("token 分项", "成本", "耗时", "错误码"):
            with self.subTest(field=field):
                self.assertIn(field, row)

    def test_distinctions_are_pinned(self):
        section = _section()
        for marker in (
            "不含 `cached`",              # 压力口径不得把缓存读算进上下文
            "两个量纲不得互换",          # used() 响应式 vs last_context() 事前
            "不得记成模型质量失败",      # 预算熔断是运维开关
            "样本为 0 时该项记 0",       # 无历史不猜候选好坏
            "不构成任务通过条件",        # estimate 只服务排程
            "既无计量字段也无口径",      # TTFT/tokens-per-sec 是已知空白
            "单点跑得快慢不构成结论",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, section)


class TestCostStandardMatchesCode(unittest.TestCase):
    """文档点名的每个旋钮都要在代码里存在，且数值口径一致。"""

    def test_ledger_fields_match_record_contract(self):
        from app.core import usage

        params = set(inspect.signature(usage.record).parameters)
        self.assertTrue({"duration_s", "cost_usd", "usage"} <= params)
        for field in ("input", "output", "cached", "reasoning", "total",
                      "duration_s", "cost_usd"):
            with self.subTest(field=field):
                self.assertIn(field, usage.FIELDS)
                self.assertIn("`%s`" % field, _section())
        self.assertIn("saved", inspect.getsource(usage.record))
        self.assertIn("`saved`", _section())

    def test_pressure_threshold_and_capacity_match_doc_numbers(self):
        from app.core import token_meter

        section = _section()
        self.assertEqual(0.8, token_meter.DEFAULT_PRESSURE_THRESHOLD)
        self.assertIn("默认 0.8", section)
        self.assertEqual(128_000, token_meter.DEFAULT_CAPACITY[""])
        self.assertIn("128000", section)
        self.assertIn("0.7", section)   # 推演让位阈值与 branching.py 同值
        from app.core import branching

        self.assertRegex(inspect.getsource(branching), r"pressure_ratio\([^)]*\)\s*>\s*0\.7")

    def test_meter_accessors_named_in_doc_exist(self):
        from app.core.token_meter import TokenMeter

        for name in ("used", "cached", "last_context", "capacity",
                     "pressure_ratio"):
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(TokenMeter, name)))
        section = _section()
        # 文档点名的两套量纲必须各自对应一个真实方法
        self.assertIn("`token_meter.used()`", section)
        self.assertIn("`token_meter.last_context()`", section)

    def test_budget_knob_name_and_default_match_doc(self):
        from app.core import settings_schema

        settings_schema.register_default_namespaces()
        fields = settings_schema._NAMESPACES["budget"]["fields"]
        self.assertEqual(0, fields["max_tokens_per_run"].default)
        self.assertIn("`budget.max_tokens_per_run`（默认 0=不限）", _section())

    def test_router_penalty_caps_match_doc(self):
        from app.core import router

        src = inspect.getsource(router._online_bonus)
        section = _section()
        for needle in ("min(18.0", "min(4.0", "p95_duration_s", "avg_cost_usd"):
            with self.subTest(needle=needle):
                self.assertIn(needle, src)
        self.assertIn("上限 -18", section)
        self.assertIn("惩罚 -4", section)

    def test_cache_rate_tripwire(self):
        """标准声称 TTFT/吞吐无口径；一旦代码补上就必须同步改文档。"""
        from app.core import usage

        src = inspect.getsource(usage.record) + ",".join(usage.FIELDS)
        for field in ("ttft", "tokens_per_sec"):
            with self.subTest(field=field):
                self.assertNotIn(field, src)


class TestRoutingMetricsShape(BaseTest):
    def test_doc_named_metrics_are_returned(self):
        from app.core import usage

        stats = usage.routing_stats(task_type="not-a-real-task")
        self.assertEqual(0, stats["samples"])
        for key in ("p50_duration_s", "p95_duration_s", "avg_cost_usd",
                    "success_rate", "fallback"):
            with self.subTest(key=key):
                self.assertIn(key, stats)
        self.assertEqual("global", stats["fallback"])
        # Beta(3,1) 平滑：零样本时成功率是 3/4 而非 0，文档口径同此
        self.assertAlmostEqual(0.75, stats["success_rate"], places=4)
        section = _section()
        self.assertIn("Beta(3,1)", section)
        for metric in ("`p95_duration_s`", "`avg_cost_usd`",
                       "`usage.routing_stats`"):
            with self.subTest(metric=metric):
                self.assertIn(metric, section)

    def test_estimate_source_labels_match_doc(self):
        from app.core import usage

        src = inspect.getsource(usage.estimate)
        section = _section()
        self.assertIn('"duration_source": "baseline"', src)
        self.assertIn('duration_source = "history"', src)
        self.assertIn("`history`", section)
        self.assertIn("`baseline`", section)
        for key in ("avg_tokens", "median_tokens", "p90_tokens", "avg_cost_usd"):
            self.assertIn(key, src)


if __name__ == "__main__":
    unittest.main()
