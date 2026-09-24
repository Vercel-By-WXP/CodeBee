# -*- coding: utf-8 -*-
"""回归：换将成功后评审者重选须剔除「已知死者」与「与死者同上游」候选。

2026-09-24 实案：实现步换将走查暴露 quota/限流死链，但预先选定的评审者不被
刷新，评审撞同一失效网关（kimi 与 claude 同骑智谱）白烧一轮后误报「评审器
故障」。修后 pick_reviewer 增死上游剔除：同上游 = 同配额桶 = 同死。
"""
from __future__ import annotations

from base import BaseTest


class TestPickReviewerDeadUpstream(BaseTest):
    def _patch(self, ups_map):
        from app.core import router
        orig_score = router.score
        orig_ups = router.agent_upstreams
        router.score = lambda a, role, ttype, stats=None: (a.get("_s", 0), "s")
        router.agent_upstreams = lambda aid, **_kwargs: ups_map.get(aid, set())
        return router, orig_score, orig_ups

    def test_excludes_dead_upstream_cross(self):
        """同上游死者即便评分更高，也不得当选评审者——选活的跨厂商。"""
        from app.core import router
        impl = {"id": "i", "kind": "codex", "mode": "real"}
        agents = [
            impl,
            {"id": "dead-cross", "kind": "claude", "mode": "real", "_s": 90},
            {"id": "live-cross", "kind": "qwen", "mode": "real", "_s": 50},
        ]
        router, o1, o2 = self._patch(
            {"dead-cross": {"dead.io"}, "live-cross": {"live.io"}})
        try:
            rev, note = router.pick_reviewer(
                agents, impl, "code", {}, dead_upstreams=[{"dead.io"}])
        finally:
            router.score, router.agent_upstreams = o1, o2
        self.assertEqual(rev["id"], "live-cross")
        self.assertTrue(note.startswith("跨厂商评审"))
        self.assertIn("qwen", note)

    def test_falls_back_to_same_vendor_when_all_cross_dead(self):
        """跨厂商候选全在死上游：不得硬选死者，回退同厂商活口并如实备注。"""
        from app.core import router
        impl = {"id": "i", "kind": "codex", "mode": "real"}
        agents = [
            impl,
            {"id": "dead-cross", "kind": "claude", "mode": "real", "_s": 90},
            {"id": "same-live", "kind": "codex", "mode": "real", "_s": 80},
        ]
        router, o1, o2 = self._patch(
            {"dead-cross": {"dead.io"}, "same-live": {"live.io"}})
        try:
            rev, note = router.pick_reviewer(
                agents, impl, "code", {}, dead_upstreams=[{"dead.io"}])
        finally:
            router.score, router.agent_upstreams = o1, o2
        self.assertEqual(rev["id"], "same-live")
        self.assertTrue(note.startswith("（无跨厂商智能体可用"))

    def test_no_dead_upstream_keeps_original_behavior(self):
        """无死上游信息时行为与旧版一致：跨厂商最高分胜出。"""
        from app.core import router
        impl = {"id": "i", "kind": "codex", "mode": "real"}
        agents = [
            impl,
            {"id": "c1", "kind": "claude", "mode": "real", "_s": 90},
            {"id": "c2", "kind": "qwen", "mode": "real", "_s": 50},
        ]
        router, o1, o2 = self._patch({"c1": {"x.io"}, "c2": {"y.io"}})
        try:
            rev, _ = router.pick_reviewer(agents, impl, "code", {})
        finally:
            router.score, router.agent_upstreams = o1, o2
        self.assertEqual(rev["id"], "c1")

    def test_unknown_upstream_not_excluded(self):
        """上游未知的候选不参与剔除（宁白试不误杀）。"""
        from app.core import router
        impl = {"id": "i", "kind": "codex", "mode": "real"}
        agents = [
            impl,
            {"id": "unk", "kind": "claude", "mode": "real", "_s": 70},
            {"id": "live", "kind": "qwen", "mode": "real", "_s": 40},
        ]
        router, o1, o2 = self._patch({"unk": set(), "live": {"live.io"}})
        try:
            rev, _ = router.pick_reviewer(
                agents, impl, "code", {}, dead_upstreams=[{"dead.io"}])
        finally:
            router.score, router.agent_upstreams = o1, o2
        # unk 上游未知不剔除，且评分更高 → 当选
        self.assertEqual(rev["id"], "unk")


if __name__ == "__main__":
    import unittest
    unittest.main()
