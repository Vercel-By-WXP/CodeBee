# -*- coding: utf-8 -*-
"""编排者幂等调用精确缓存接线（§07 T2.2 补全）单测。

跑法：python -m unittest discover -s tests -p "test_planner_cache_wiring.py" -v

契约：大纲（首次 3600/重试 0）/代码计划 3600/评审大纲 3600——同任务断点
续跑命中缓存省一次全量规划；重试第 2 次故意旁路（要新样本不要同一份坏文本）。
"""
from __future__ import annotations

from unittest import mock

from base import BaseTest


class PlannerCacheWiringTests(BaseTest):
    def _mock_chat(self):
        """捕获 modelhub.chat 调用参数；返回可解析的最小结果。"""
        from app.core import modelhub
        calls = []

        def fake_chat(pid, model, prompt, **kw):
            calls.append(kw)
            return {"ok": True, "text": "", "tokens": 1, "usage": None,
                    "error": ""}
        patcher = mock.patch.object(modelhub, "chat", side_effect=fake_chat)
        return calls, patcher

    def test_serial_outline_first_attempt_cached_retry_bypassed(self):
        """连载大纲：尝试 1 带 cache_ttl=3600，尝试 2 故意 0（换样本语义）。"""
        from app.core import planner
        calls, patcher = self._mock_chat()
        with patcher, \
             mock.patch.object(planner, "_orchestrator",
                               return_value=({"id": "p", "name": "P"}, "m")), \
             mock.patch.object(planner, "_log_streamer",
                               return_value=mock.MagicMock()), \
             mock.patch.object(planner, "_append_log"), \
             mock.patch.object(planner, "_log_usage"):
            # 第一次 ok 但 JSON 解析失败 → 进第二次尝试
            with mock.patch.object(planner.runner, "extract_json",
                                   side_effect=[None, None]):
                planner.make_serial_outline(
                    {"goal": "写书", "context": "", "serial": {"chapters": 2}},
                    workdir=str(self.workdir))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].get("cache_ttl"), 3600)
        self.assertEqual(calls[1].get("cache_ttl"), 0)

    def test_code_plan_cached(self):
        """代码计划：cache_ttl=3600（同任务重试/续跑幂等）。"""
        from app.core import planner
        calls, patcher = self._mock_chat()
        with patcher:
            planner._orch_code_plan({"goal": "g", "context": ""},
                                    {"id": "p"}, "m")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].get("cache_ttl"), 3600)

    def test_review_outline_cached(self):
        """评审大纲：cache_ttl=3600。"""
        from app.core import planner
        calls, patcher = self._mock_chat()
        with patcher,              mock.patch.object(planner, "_orchestrator",
                               return_value=({"id": "p"}, "m")),              mock.patch.object(planner, "_log_usage"):
            planner.make_review_outline({"goal": "g", "context": ""})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].get("cache_ttl"), 3600)


if __name__ == "__main__":
    import unittest
    unittest.main()
