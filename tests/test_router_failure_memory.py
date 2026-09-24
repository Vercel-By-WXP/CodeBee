# -*- coding: utf-8 -*-
"""路由失败记忆回归：失败运行必须进历史、败率必须扣分。

2026-09-17 mo-so 实测：history 只数 done 运行——opencode 当日秒挂 3 次仍是
「无历史记录」满血参选，换将反复选中同一个挂掉的 CLI。同时旧加分公式恒为正
（0/3 全败还拿 +6 经验分，比没跑过的 0 分还高）。修后：失败记为全负 +
败率惩罚随样本量爬满（0/3 = -18，必排到无历史新面孔之后）。
"""
from __future__ import annotations

from base import BaseTest


class TestFailureHistoryMemory(BaseTest):
    def runTest(self):
        from app.core import store, history, router
        for i in range(3):
            r = store.create_run(kind="orchestration", title="fail %d" % i)
            store.add_step(r["id"], "implement", "opencode", "OpenCode")
            store.update_run(r["id"], status="failed", error="boom", ended_at="t",
                             verdict={"type": "code"})
        # 用户取消不算智能体的账
        rc = store.create_run(kind="orchestration", title="cancelled")
        store.add_step(rc["id"], "implement", "opencode", "OpenCode")
        store.update_run(rc["id"], status="cancelled", ended_at="t")
        stats = history.agent_stats()
        s = (stats.get("opencode") or {}).get("code") or {}
        self.assertEqual((s.get("runs"), s.get("wins")), (3, 0))
        # 加分公式关键值：0/3 = -21（必低于无历史的 0）；3/3 = +21；1/1 = +19
        self.assertEqual(router._history_bonus(stats, "opencode", "code"), -21.0)
        stats3 = {"a": {"code": {"runs": 3, "wins": 3}}}
        self.assertEqual(router._history_bonus(stats3, "a", "code"), 21.0)
        stats1 = {"a": {"code": {"runs": 1, "wins": 1}}}
        self.assertEqual(router._history_bonus(stats1, "a", "code"), 19.0)
        # 路由实测：同能力同绑定的两个 CLI，全败者必须落选
        agents = [{"id": "bad-cli", "kind": "codex", "mode": "real"},
                  {"id": "fresh-cli", "kind": "codex", "mode": "real"}]
        bad_stats = {"bad-cli": {"code": {"runs": 3, "wins": 0}}}
        picked, _ = router.pick(agents, "implement", "code", bad_stats)
        self.assertEqual(picked["id"], "fresh-cli")
