# -*- coding: utf-8 -*-
"""连载起草撞 403 的止损回归（2026-09-24 实案）。

真实台账 r-20260924-120208-0649：第 9 章起草在同一条 403 网关上撞了 5 次
（同作者退避重试 3 遍 + 换将补位又回到同一台），最后由重复守卫兜底收口。
403 是上游权限判死、确定性秒死，按统一失败码应当「同上游不重复撞、无异上游
即返回权限诊断」，不该把退避 30/60s 与四遍白烧留给守卫。
"""
from __future__ import annotations

import json
from unittest import mock

from base import BaseTest

_403 = '退出码 1；stderr/stdout: 403 {"error":{"type":"forbidden","message":"Request not allowed"}}'


class TestSerialDraftForbidden(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core import pipeline
        self.pipeline = pipeline
        self.calls = []

    def _pool(self):
        def agent(aid, kind):
            # 与 registry._build_agent 同形状：runner 侧要读 command/argv_template
            return {"id": aid, "label": aid, "kind": kind, "mode": "real",
                    "command": aid, "env": {}, "argv_template": ["-p", "{prompt}"],
                    "resume_argv_template": None, "quota_tokens_per_hour": 0,
                    "orch": {}}
        return [agent("pi", "generic"), agent("opencode", "opencode")]

    def _patch_spawn(self):
        """draft 步一律回 403；其余步骤照常成功（走 mock 形态返回值）。"""
        def fake(*a, **k):
            role = k.get("role") or (a[1] if len(a) > 1 else "")
            agent = k.get("agent") or (a[2] if len(a) > 2 else {}) or {}
            if str(role).startswith("draft-c"):
                self.calls.append((str(role), agent.get("id")))
                return {"ok": False, "text": "", "json": None, "cost_usd": 0.0,
                        "tokens": 0, "usage": None, "error": _403,
                        "raw": {"exit_code": 1}, "kind": agent.get("kind") or "generic",
                        "model": ""}
            return {"ok": True, "text": "ok", "json": None, "cost_usd": 0.0,
                    "tokens": 1, "usage": None, "error": "",
                    "raw": {"exit_code": 0}, "kind": agent.get("kind") or "generic",
                    "model": ""}
        patcher = mock.patch.object(self.pipeline, "_spawn_step", fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _patch_outline(self):
        """大纲走 planner 的 runner.run_agent（不经 _spawn_step）：给一份确定性大纲。"""
        from app.core import runner

        def fake_run_agent(*a, **k):
            return {"ok": True, "text": json.dumps({
                "book_title": "止损测试书",
                "chapters": [{"title": "第 1 章", "beats": "冲突起", "hook": "钩子"}]}),
                "json": None, "cost_usd": 0.0, "tokens": 0, "usage": None,
                "error": "", "raw": {"exit_code": 0}}
        patcher = mock.patch.object(runner, "run_agent", fake_run_agent)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run_serial(self, upstreams):
        from app.core import router, store
        self._patch_outline()
        task = store.create_task({
            "type": "serial_novel", "title": "四十三案连载", "goal": "写一章",
            "workdir": str(self.workdir),
            "serial": {"chapters": 1, "words_per_chapter": 300}})
        run = store.create_run("orchestration", task["title"], task_id=task["id"])
        store.update_task_status(task["id"], "queued")
        self.pipeline._agents = lambda: self._pool()   # _agents 是被调用的函数
        patcher = mock.patch.object(
            router, "agent_upstreams",
            lambda agent_id, **kw: set(upstreams.get(agent_id) or ()))
        patcher.start()
        self.addCleanup(patcher.stop)
        self._patch_spawn()
        self.pipeline.execute_run(run["id"])
        return store.get_run(run["id"])

    def test_same_gateway_403_does_not_repeatedly_burn(self):
        """同作者重试遇 403 立即跳出；换将只走异上游（真实台账是 5 次同网关撞墙）。"""
        run = self._run_serial({"pi": ["gw-a.example"], "opencode": ["gw-b.example"]})
        self.assertEqual(run["status"], "failed")
        self.assertIn("起草失败", run["error"] or "")
        ids = [c[1] for c in self.calls]
        self.assertEqual(len(ids), 2, "只允许「链首一次 + 异上游换将一次」：%s" % ids)
        self.assertEqual(len(set(ids)), 2, "换将必须换到另一台 CLI：%s" % ids)

    def test_no_alt_upstream_fails_fast_without_switch(self):
        """候选全在同一条 403 网关上：不再换将白撞，直接判失败。"""
        run = self._run_serial({"pi": ["gw-a.example"], "opencode": ["gw-a.example"]})
        self.assertEqual(run["status"], "failed")
        self.assertIn("起草失败", run["error"] or "")
        self.assertEqual(len(self.calls), 1,
                         "同上游候选不得捡回来重试：%s" % self.calls)
