# -*- coding: utf-8 -*-
"""账号台账死号不得进编排（2026-10-22 cline-cli Unauthorized 案回归）。

实案：cline 账号凭据 10-17 起服务端失效，持久账号台账 down/AUTH、探针连败
243 次，但自动流水线的选组（pick_critics/pick）只按历史分排序、不查台账，
死号凭活着的时期攒的分当选唯一评审，两轮 6 次全撞 401；收尾文案还写
「不是稿件质量问题，直接重试即可」，用户重试一次死一次。

回归三面：
· score() 对台账死号决定性降权（罚分盖过一切加分），探活成功即回血；
· pick_critics / _pick_scored 因此绕开死号（仅当没有活号时才白试选中）；
· _critic_fail_hint 对账号级死错说「重试无法自愈」，不再误导重试。
"""
import tempfile
import unittest
from unittest import mock

from app.core import bench, pipeline, router


DEAD = "zz-ledger-dead-cli"
ALIVE_A = "zz-ledger-alive-a"
ALIVE_B = "zz-ledger-alive-b"


def _agents():
    # 死号能力基线更高（claude 84 > generic 55）：不接台账时会稳赢。
    return [
        {"id": "author", "kind": "console", "mode": "real"},
        {"id": DEAD, "kind": "claude", "mode": "real"},
        {"id": ALIVE_A, "kind": "generic", "mode": "real"},
        {"id": ALIVE_B, "kind": "generic", "mode": "real"},
    ]


def _binding_mocks():
    bindings = {
        agent["id"]: {"call_chain": [{"model": "m-%s" % agent["id"],
                                      "provider_id": "p-%s" % agent["id"]}]}
        for agent in _agents()
    }
    return (mock.patch.object(router.modelhub, "resolve_binding",
                              side_effect=lambda agent, difficulty="default":
                              bindings.get(agent)),
            mock.patch.object(router, "agent_upstreams", return_value=set()))


class LedgerDownRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        bench.init(self.tmp.name, start_probe=False)
        self.addCleanup(bench.init, self.tmp.name, start_probe=False)
        self.addCleanup(self.tmp.cleanup)

    def test_ledger_down_agent_loses_to_live_candidate(self):
        self.assertTrue(bench.record_failure(DEAD, "AUTH"))
        agents = _agents()
        dead_total, dead_reason = router.score(agents[1], "review", "story", stats={})
        alive_total, _ = router.score(agents[2], "review", "story", stats={})
        self.assertLess(dead_total, alive_total)
        self.assertIn("账号台账停用", dead_reason)
        self.assertIn("AUTH", dead_reason)

    def test_pick_critics_fills_slots_with_live_agents_first(self):
        self.assertTrue(bench.record_failure(DEAD, "AUTH"))
        agents = _agents()
        with _binding_mocks()[0], _binding_mocks()[1]:
            critics, _ = router.pick_critics(agents, "story", stats={}, impl=agents[0])
        self.assertNotIn(DEAD, {a["id"] for a in critics})
        self.assertEqual({a["id"] for a in critics}, {ALIVE_A, ALIVE_B})

    def test_pick_scored_prefers_any_live_candidate_over_ledger_down(self):
        self.assertTrue(bench.record_failure(DEAD, "AUTH"))
        agents = _agents()
        best, _ = router._pick_scored(agents, "implement", "code", stats={})
        self.assertNotEqual(best["id"], DEAD)
        # 死号（基线 claude 84）必须被压到活号（generic 55）之下。
        dead_total, dead_reason = router.score(agents[1], "implement", "code", stats={})
        self.assertLess(dead_total, router.score(best, "implement", "code", stats={})[0])
        self.assertIn("账号台账停用", dead_reason)

    def test_probe_recovery_restores_the_score(self):
        self.assertTrue(bench.record_failure(DEAD, "AUTH"))
        agents = _agents()
        down_total, _ = router.score(agents[1], "review", "story", stats={})
        self.assertTrue(bench.record_success(DEAD))
        self.assertFalse(bench.is_globally_benched(DEAD))
        recovered_total, recovered_reason = router.score(
            agents[1], "review", "story", stats={})
        self.assertGreater(recovered_total, down_total)
        self.assertNotIn("账号台账停用", recovered_reason)


class CriticFailHintTests(unittest.TestCase):
    def test_account_dead_error_says_retry_cannot_heal(self):
        hint = pipeline._critic_fail_hint({
            "cline-cli": "退出码 1；stderr/stdout: error: Unauthorized: Please make "
                         "sure you're using the latest version of Cline and "
                         "re-authenticate your Cline account. (Request ID: pFGrPnA"})
        self.assertIn("cline-cli", hint)
        self.assertIn("重试无法自愈", hint)
        self.assertNotIn("直接重试即可", hint)

    def test_quota_dead_error_also_warns(self):
        hint = pipeline._critic_fail_hint({"qwencode": "HTTP 402 payment required"})
        self.assertIn("重试无法自愈", hint)

    def test_transient_error_keeps_retry_hint(self):
        hint = pipeline._critic_fail_hint({"critic-a": "HTTP 503 upstream unavailable"})
        self.assertIn("直接重试即可", hint)
        self.assertNotIn("重试无法自愈", hint)

    def test_empty_errors_keep_retry_hint(self):
        self.assertIn("直接重试即可", pipeline._critic_fail_hint({}))


if __name__ == "__main__":
    unittest.main()
