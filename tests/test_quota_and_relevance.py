# -*- coding: utf-8 -*-
"""经验库相关性 top-k 注入 + 配额感知路由回归（pro-workflow / munder-difflin 借鉴项）。

锁定：
1. relevance_top：教训超上限时按与任务目标的 bigram 重叠选最相关的 k 条，
   相关性并列按 id 决序（字节稳定）；不超限不重排；任务无关键词回落前 k 条；
2. block_for 在超限时走相关性选择（经验库真实路径）；
3. 配额感知路由：registry 透传 quota_tokens_per_hour；本小时用量越接近配额
   总分越低（-45 封顶），满额后仍可选（垫底而非剔除）；无配额完全无感。
"""
from __future__ import annotations

import time

from base import BaseTest


class TestRelevanceInjection(BaseTest):

    def test_relevance_top_selects_related(self):
        from app.core import skills
        task = {"goal": "修好登录时的数据库连接泄漏", "title": "登录修复", "context": ""}
        lessons = [
            {"id": "l-ui", "title": "按钮配色", "content": "主按钮用品牌色"},
            {"id": "l-db", "title": "数据库连接池", "content": "连接用完必须归还池，避免泄漏"},
            {"id": "l-plot", "title": "章末钩子", "content": "每章结尾断章要狠"},
        ]
        out = skills.relevance_top(lessons, task, 1)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], "l-db")     # 与任务重叠度最高者入选

    def test_relevance_top_stable_and_fallbacks(self):
        from app.core import skills
        lessons = [{"id": "l%d" % i, "title": "T%d" % i, "content": "C%d" % i}
                   for i in range(5)]
        task = {"goal": "完全无关的任务词", "title": "x", "context": ""}
        twice = [skills.relevance_top(lessons, task, 2), skills.relevance_top(lessons, task, 2)]
        self.assertEqual(twice[0], twice[1])            # 同输入同输出（稳定）
        self.assertEqual([x["id"] for x in twice[0]], ["l0", "l1"])  # 并列按 id
        # 不超上限：原样返回（保 hits 语义）
        self.assertEqual(skills.relevance_top(lessons[:2], task, 8), lessons[:2])

    def test_block_for_uses_relevance_when_over_limit(self):
        from app.core import skills
        # 造 10 条教训，其中 1 条与任务强相关、其余不含任务词
        for i in range(9):
            skills.upsert_lesson("code", "通用教训%d" % i, "普适内容%d" % i)
        skills.upsert_lesson("code", "数据库连接池", "连接泄漏要归还池")
        task = {"type": "code", "title": "修复连接", "goal": "修复数据库连接泄漏问题",
                "context": ""}
        block, used = skills.block_for(task)
        self.assertIn("数据库连接池", block)          # 相关教训进了注入文本
        self.assertEqual(len(used), 8)                # 超限后仍只注入上限条数


class TestQuotaRouting(BaseTest):

    def test_registry_passes_quota(self):
        from app.core import registry
        agent = registry._build_agent({
            "id": "codex", "name": "Codex", "orch": {
                "kind": "codex", "command": "codex",
                "quota_tokens_per_hour": 50000}})
        self.assertEqual(agent["quota_tokens_per_hour"], 50000)
        agent2 = registry._build_agent({"id": "c2", "name": "C2", "orch": {"kind": "codex"}})
        self.assertEqual(agent2["quota_tokens_per_hour"], 0)   # 缺省=不限

    def test_score_quota_penalty(self):
        from app.core import usage, router
        agent = {"id": "q-cli", "kind": "codex", "quota_tokens_per_hour": 1000}
        # 预热缓存：本小时已用 900/1000 → 惩罚 ≈ -40.5
        usage._HOURLY_CACHE.update(
            ts=time.time(), val={"q-cli": 900})
        total, reason = router.score(agent, "implement", "code", {})
        self.assertLess(total, router.CAPABILITY["codex"])      # 被降权
        self.assertIn("900/1000", reason)
        # 满额：垫底但仍可选（不被剔除）
        usage._HOURLY_CACHE.update(ts=time.time(), val={"q-cli": 5000})
        total2, reason2 = router.score(agent, "implement", "code", {})
        self.assertLess(total2, total)
        self.assertIn("5000/1000", reason2)
        # 无配额：用量再大也无感
        usage._HOURLY_CACHE.update(ts=time.time(), val={"q-cli": 999999})
        free = {"id": "q-cli", "kind": "codex", "quota_tokens_per_hour": 0}
        total3, reason3 = router.score(free, "implement", "code", {})
        self.assertEqual(total3, router.CAPABILITY["codex"])
        self.assertNotIn("tokens", reason3)
        usage._HOURLY_CACHE.update(ts=0.0, val={})
