# -*- coding: utf-8 -*-
"""瞬态失败换将的故障域隔离回归（对应审查 C1）。

配额/403 会进健康台账，主循环按上游去重；但 5xx、超时、解析失败不记账，
原实现只看分数——分数最高的备份常常就是同一个网关（同厂商不同 CLI 壳），
换过去还是在同一个故障域，白烧一轮并把真正的跨族降级往后拖。

本文件把 `_switch_identity`（身份）与 `score`（候选序）打桩，只验重排逻辑
本身：不依赖本机 modelhub 配置，也不碰策略注册表。
"""
import unittest
from unittest import mock

from app.core import router

IDENT = {
    "a": ({"xai"}, {"gw.a"}),
    "b": ({"gpt"}, {"gw.a"}),      # 与 a 同网关（换壳不换命）
    "c": ({"claude"}, {"gw.b"}),   # 异上游异族
    "d": ({"qwen"}, {"gw.c"}),     # 异上游异族，分数低
    "e": ({"gemini"}, {"gw.b"}),
}

SCORES = {"a": 90.0, "b": 80.0, "c": 70.0, "d": 10.0, "e": 60.0}


def _agents(*ids, mock_ids=()):
    return [{"id": i, "mode": "mock" if i in mock_ids else "real"} for i in ids]


class SwitchIsolationTests(unittest.TestCase):
    def setUp(self):
        self._patches = [
            mock.patch.object(router, "_switch_identity",
                              side_effect=lambda agent, ttype, difficulty:
                              IDENT.get(str((agent or {}).get("id") or ""), (set(), set()))),
            mock.patch.object(router, "score",
                              side_effect=lambda agent, role, ttype, stats=None,
                              overrides=None: (SCORES.get(agent.get("id"), 0.0), "")),
            mock.patch.object(router, "_sticky_remember"),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def switch(self, agents, failed, code="TIMEOUT", **kw):
        # failed_cli 带 fallbacks：策略链把首选钉死在 b，好让断言只看隔离层
        failed_cli = {"id": failed, "fallbacks": [{"id": "b"}]}
        return router.pick_switch_candidate(
            agents, "implement", "code", {}, exclude={failed},
            failed_cli=failed_cli, error={"error_code": code}, **kw)

    def test_prefers_disjoint_upstream_and_family(self):
        picked, reason = self.switch(_agents("a", "b", "c"), "a")
        self.assertEqual(picked["id"], "c", "应换到异上游异族，而不是同网关的 b")
        self.assertIn("异上游异族", reason)

    def test_falls_back_to_disjoint_upstream_when_same_family(self):
        # e 与 a 同上游但异族；c 不存在 → 只能挑到「异上游」这一档
        picked, reason = self.switch(_agents("a", "b", "e"), "a")
        self.assertEqual(picked["id"], "e")
        self.assertIn("异上游", reason)

    def test_same_gateway_only_keeps_original_choice_and_reason(self):
        # 全部同一网关：选路结果与旧行为一致，reason 不得被追加噪音
        with self.assertLogs("app.core.router", level="WARNING") as logs:
            picked, reason = self.switch(_agents("a", "b"), "a")
        self.assertEqual(picked["id"], "b")
        self.assertEqual(reason, "策略后备链第 1 位")
        self.assertTrue(any("无满足隔离的候选" in line for line in logs.output))

    def test_mock_candidate_never_promoted(self):
        # d 分数最低但异上游异族；c 是 mock。应选 d，绝不能把 mock 顶上来
        picked, reason = self.switch(_agents("a", "b", "c", "d", mock_ids=("c",)), "a")
        self.assertEqual(picked["id"], "d")
        self.assertEqual(picked["mode"], "real")

    def test_unknown_failure_identity_does_not_reorder(self):
        with mock.patch.object(router, "_switch_identity", return_value=(set(), set())):
            picked, reason = self.switch(_agents("a", "b", "c"), "nosuchagent")
        self.assertEqual(picked["id"], "b", "死者身份未知时应保持原选择")
        self.assertEqual(reason, "策略后备链第 1 位")

    def test_dead_upstream_path_is_left_to_the_main_loop(self):
        # 有 dead 台账时不进隔离层（避免两套 reason 互相覆盖）：只看它有没有
        # 被调用，具体选谁由主循环的既有测试（test_ledger_down_routing）负责。
        with mock.patch.object(router, "_prefer_disjoint_candidate",
                               wraps=router._prefer_disjoint_candidate) as spy:
            self.switch(_agents("a", "b", "c"), "a", dead_upstreams=[{"gw.a"}])
        spy.assert_not_called()

    def test_non_fallback_error_returns_none(self):
        picked, reason = self.switch(_agents("a", "b", "c"), "a", code="PARSE_FAIL")
        self.assertIsNone(picked)
        self.assertIn("无需跨 CLI 降级", reason)


if __name__ == "__main__":
    unittest.main()
