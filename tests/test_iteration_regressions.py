# -*- coding: utf-8 -*-
"""知识库注入预算回归（2026-10-07 整条装箱落地件）。

block_for 旧实现超预算时对整块 text[:3000] 拦腰硬截，且 used 在截断前收满
条目 id、被截条目照常 bump hits——「从未被读到」的内容反而升权、继续挤占
预算的反馈回路缺陷。本文件只覆盖该行为的正常/边界/回归三面。
"""
from __future__ import annotations

from base import BaseTest

HEADER = "## 知识库（已确认的领域知识，供参考）\n\n"


class TestKnowledgeBlockBudget(BaseTest):
    def runTest(self):
        from app.core import knowledge
        knowledge._FILE = self.data_dir / "knowledge.json"
        today = knowledge._now()[:10]

        # —— 正常：预算内输出与改动前逐字节一致，hits 照常 +1 ——
        a = knowledge.upsert_entry("code", "构建缓存目录约定",
                                   "缓存统一放 build/.cache，清理不碰源码。",
                                   status="approved")
        b = knowledge.upsert_entry("code", "依赖升级流程",
                                   "先跑单测再合入，升级后必须回归冒烟。",
                                   status="approved")
        ents = {x["id"]: x for x in (a, b)}
        expected = HEADER + "\n".join(
            "- **%s**（事实截至 %s）：%s" % (ents[i]["title"], today, ents[i]["body"])
            for i in sorted(ents))
        text = knowledge.block_for({"type": "code"})
        self.assertEqual(text, expected)
        self.assertLessEqual(len(text), knowledge.KNOWLEDGE_BUDGET)
        self.assertNotIn("未注入", text)
        self.assertNotIn("已截断", text)
        for x in knowledge.list_entries(scope="code"):
            self.assertEqual(x["hits"], 1)

        # —— 边界：3 条 body 各 1400 字，任两条在预算内、第 3 条整条丢弃 ——
        body = "知识条目正文。" * 200   # 恰 1400 字
        t1 = knowledge.upsert_entry("novel", "连载更新节奏", body, status="approved")
        t2 = knowledge.upsert_entry("novel", "读者爽点安排", body, status="approved")
        t3 = knowledge.upsert_entry("novel", "断章钩子写法", body, status="approved")
        titles = {t1["id"]: t1["title"], t2["id"]: t2["title"], t3["id"]: t3["title"]}
        text2 = knowledge.block_for({"type": "novel"})
        self.assertLessEqual(len(text2), knowledge.KNOWLEDGE_BUDGET)
        self.assertTrue(text2.startswith(HEADER))
        self.assertTrue(text2.endswith("…（1 条超出预算未注入）"))
        self.assertEqual(text2.count("- **"), 2)   # 整条装箱恰好 2 条
        injected = [t for t in titles.values() if ("- **%s**" % t) in text2]
        dropped = [t for t in titles.values() if t not in injected]
        self.assertEqual(len(injected), 2)
        self.assertEqual(len(dropped), 1)
        # 丢弃条整条缺席（无拦腰半句）
        self.assertNotIn(dropped[0], text2)
        # hits：注入的 +1，被丢弃的不动
        hits = {x["title"]: int(x.get("hits") or 0)
                for x in knowledge.list_entries(scope="novel")}
        self.assertEqual(hits[dropped[0]], 0)
        for t in injected:
            self.assertEqual(hits[t], 1)

        # —— 回归：重复调用下被丢弃条目 hits 恒 0（切断自增强回路）——
        # 无 goal → 按 id 排序装箱，两次调用注入同一批
        knowledge.block_for({"type": "novel"})
        hits2 = {x["title"]: int(x.get("hits") or 0)
                 for x in knowledge.list_entries(scope="novel")}
        for t in injected:
            self.assertEqual(hits2[t], 2)
        self.assertEqual(hits2[dropped[0]], 0)

        # —— 边界：首条本身超过预算也必须整条丢弃，不能放行后超预算或记 hits ——
        old_budget = knowledge.KNOWLEDGE_BUDGET
        knowledge.KNOWLEDGE_BUDGET = 80
        oversized = knowledge.upsert_entry(
            "code", "单条超预算", "超长知识。" * 200, status="approved")
        try:
            text3 = knowledge.block_for({"type": "code"})
            self.assertLessEqual(len(text3), knowledge.KNOWLEDGE_BUDGET)
            self.assertNotIn("单条超预算", text3)
            self.assertEqual(
                next(x["hits"] for x in knowledge.list_entries("code")
                     if x["id"] == oversized["id"]), 0)
        finally:
            knowledge.KNOWLEDGE_BUDGET = old_budget


if __name__ == "__main__":
    import unittest
    unittest.main()
