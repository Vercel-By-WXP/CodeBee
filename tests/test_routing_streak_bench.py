# -*- coding: utf-8 -*-
"""路由连败冷却：近窗连败的 CLI 起跑位沉底 + 粘性抑制。

2026-10-22 codex 实案：当日 0/6 败绩仍凭能力基线 84 + 会话粘性 +4，以 45 分
压过健康候选先跑起草位，白烧 250s 确认失败才让位。修复 = history.agent_stats
统计「近窗起草/实现位连败步数」，score() 对 ≥STREAK_BENCH_MIN 的 CLI 叠
STREAK_BENCH_PENALTY 重锤（仅全场皆冷却时兜底当选），并停用粘性加成；
败迹出窗（history.STREAK_WINDOW_S）自愈，无需人工拉黑。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]

from app.core import history
from app.core import router

TTYPE = "handdraw_video"


def _agent(aid, kind):
    return {"id": aid, "kind": kind, "mode": "real",
            "_dispatch_task_type": TTYPE}


def _isolated_score(agent, stats):
    """屏蔽绑定/健康/在线画像等外设信号，只看评分主干（对齐健康降权测试）。"""
    with mock.patch.object(router.modelhub, "_binding_for",
                           return_value={}), \
         mock.patch.object(router.modelhub, "providers", return_value=[]), \
         mock.patch("app.core.health.down_names", return_value=set()), \
         mock.patch.object(router, "agent_upstreams", return_value=set()), \
         mock.patch("app.core.usage.routing_stats", return_value={}), \
         mock.patch.object(router.modelhub, "chain_tool_verdict",
                           return_value=(None, "")):
        return router.score(agent, "implement", TTYPE, stats)


def _streak_stats(aid, streak, runs=6, wins=0):
    return {aid: {TTYPE: {"runs": runs, "wins": wins, "streak": streak}}}


class StreakBenchScoreTests(unittest.TestCase):
    def setUp(self):
        router.sticky_reset()

    def tearDown(self):
        router.sticky_reset()

    def test_streak_bench_sinks_agent_below_healthy(self):
        bad = _agent("badcli", "codex")
        ok = _agent("okcli", "mimo")
        stats = {}
        stats.update(_streak_stats("badcli", 4))
        bad_score, bad_reason = _isolated_score(bad, stats)
        ok_score, _ = _isolated_score(ok, stats)
        self.assertIn("连败冷却", bad_reason)
        self.assertLess(bad_score, ok_score)

    def test_below_threshold_no_bench(self):
        bad = _agent("badcli", "codex")
        _, reason = _isolated_score(bad, _streak_stats("badcli", 2))
        self.assertNotIn("连败冷却", reason)

    def test_sticky_bonus_suppressed_while_benched(self):
        aid = "badcli"
        router._sticky_remember("implement", TTYPE, aid)
        _, benched_reason = _isolated_score(
            _agent(aid, "codex"), _streak_stats(aid, 3))
        self.assertNotIn("会话粘性", benched_reason)
        self.assertIn("粘性停用", benched_reason)
        # 未连败的同一粘性记忆照常生效（对照）
        router.sticky_reset()
        router._sticky_remember("implement", TTYPE, aid)
        _, normal_reason = _isolated_score(_agent(aid, "codex"), {})
        self.assertIn("会话粘性", normal_reason)

    def test_pick_prefers_healthy_over_benched(self):
        stats = _streak_stats("badcli", 5)
        sel, _ = router._pick_scored(
            [_agent("badcli", "codex"), _agent("okcli", "mimo")],
            "implement", TTYPE, stats)
        self.assertEqual(sel and sel.get("id"), "okcli")

    def test_all_benched_still_picks_fallback(self):
        stats = {}
        stats.update(_streak_stats("a", 5))
        stats.update(_streak_stats("b", 4))
        sel, _ = router._pick_scored(
            [_agent("a", "codex"), _agent("b", "mimo")],
            "implement", TTYPE, stats)
        self.assertIsNotNone(sel)

    def test_mock_agents_never_benched(self):
        m = {"id": "mock-x", "kind": "mock", "mode": "mock"}
        _, reason = _isolated_score(m, _streak_stats("mock-x", 9))
        self.assertNotIn("连败冷却", reason)


PROBE = r"""
import json, sys, time
from app.core import store, history

t = store.create_task({"title": "连败统计", "goal": "测试", "type": "handdraw_video"})


def make_run(status, steps):
    run = store.create_run("orchestration", "r", task_id=t["id"])
    rid = run["id"]
    store.update_run(rid, status="running")
    for i, (role, agent, st) in enumerate(steps, start=1):
        store.add_step(rid, role, agent, agent)
        store.finish_step(rid, i, status=st, summary="")
    store.update_run(rid, status=status)
    return rid


# badcli：三个失败 run 各一步起草 failed（都在窗内）→ streak 3
for _ in range(3):
    make_run("failed", [("draft", "badcli", "failed")])

# goodcli：较老 run 起草失败、最新 run 起草 done → done 即断，老败不入账
make_run("failed", [("draft", "goodcli", "failed")])
make_run("done", [("draft", "goodcli", "done")])

# oldcli：2 小时前的失败出窗 → 不入账
rid = make_run("failed", [("draft", "oldcli", "failed")])
store.get_run(rid)["steps"][0]["ended_at_epoch"] = time.time() - 7200

# cncli：cancelled run 里的失败步不算（用户取消非智能体之过）
make_run("cancelled", [("draft", "cncli", "failed")])

# critcli：只有评审位失败 → 不参与起草连败
make_run("failed", [("critique", "critcli", "failed")])

# mixcli：较老起草失败 + 较新评审 done → 评审成败不洗白起草连败 → streak 1
make_run("failed", [("draft", "mixcli", "failed")])
make_run("done", [("critique", "mixcli", "done")])

# walkcli：同一 run 先败后成（换将成功）→ 成步即断
make_run("failed", [("draft", "walkcli", "failed"), ("draft", "walkcli", "done")])

stats = history.agent_stats()
out = {aid: int((stats.get(aid) or {}).get("handdraw_video", {}).get("streak") or 0)
       for aid in ("badcli", "goodcli", "oldcli", "cncli", "critcli", "mixcli",
                   "walkcli")}
print("@@RESULT@@" + json.dumps(out))
"""


def _run_probe():
    """在隔离子进程里执行探针，返回结果 dict。"""
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ)
        env["TUTTI_DATA"] = str(tmp)
        proc = subprocess.run(
            [sys.executable, "-c", PROBE],
            cwd=str(REPO), env=env, timeout=60,
            capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError("probe failed: %s" % proc.stderr[-2000:])
        for line in proc.stdout.splitlines():
            if line.startswith("@@RESULT@@"):
                return json.loads(line[len("@@RESULT@@"):])
        raise AssertionError("no result marker in probe output: %s"
                             % proc.stdout[-2000:])


class AgentStatsStreakTests(unittest.TestCase):
    def test_streak_windows(self):
        out = _run_probe()
        self.assertEqual(out["badcli"], 3)
        self.assertEqual(out["goodcli"], 0)
        self.assertEqual(out["oldcli"], 0)
        self.assertEqual(out["cncli"], 0)
        self.assertEqual(out["critcli"], 0)
        self.assertEqual(out["mixcli"], 1)
        self.assertEqual(out["walkcli"], 0)


if __name__ == "__main__":
    unittest.main()
