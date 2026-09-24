# -*- coding: utf-8 -*-
"""知识检索命中热度平级决胜（教训库 karma 的轻量同构）单测：
_bump_hits 的注入命中计数此前只记账不回流；现在相关性同档时
高频命中条目优先——被反复召回的知识已验证过任务面可用性。

跑法：python -m unittest discover -s tests -p "test_kb_hits_rank.py" -v
"""
from __future__ import annotations

from base import BaseTest


class KbHitsRankTests(BaseTest):

    def _kb(self):
        from app.core import knowledge
        knowledge._FILE = self.data_dir / "knowledge.json"
        return knowledge

    def _heat(self, kb, eid, times):
        for _ in range(times):
            kb._bump_hits([eid])

    def test_hits_break_relevance_tie(self):
        """相关性同档（等重叠标题对）：hits 高者排前。"""
        kb = self._kb()
        kb.upsert_entry("code", "网关限流口径第一案", "并发超限走冷却", tags=["网关"],
                        status="approved")
        kb.upsert_entry("code", "网关限流口径第二案", "并发超限补充", tags=["网关"],
                        status="approved")
        ids = {it["title"]: it["id"] for it in kb.list_entries("code")}
        self._heat(kb, ids["网关限流口径第二案"], 7)
        txt = kb.block_for({"type": "code", "goal": "网关限流",
                            "context": "", "title": ""})
        self.assertIn("网关限流口径", txt)
        self.assertLess(txt.index("并发超限补充"), txt.index("并发超限走冷却"))

    def test_relevance_still_dominates(self):
        """相关性仍最高优先——热度只平级决胜，不越过相关性。"""
        kb = self._kb()
        kb.upsert_entry("code", "完全无关但很热", "别的事", status="approved")
        kb.upsert_entry("code", "网关限流", "相关内容", status="approved")
        ids = {it["title"]: it["id"] for it in kb.list_entries("code")}
        self._heat(kb, ids["完全无关但很热"], 99)
        txt = kb.block_for({"type": "code", "goal": "查网关限流",
                            "context": "", "title": ""})
        self.assertLess(txt.index("网关限流"), txt.index("完全无关"))

    def test_bump_then_rerank_promotes(self):
        """记账回流：_bump_hits 加热的条目下轮平级胜出。"""
        kb = self._kb()
        kb.upsert_entry("code", "部署口径第一案", "部署要点甲", tags=["部署"],
                        status="approved")
        kb.upsert_entry("code", "部署口径第二案", "部署要点乙", tags=["部署"],
                        status="approved")
        ids = {it["title"]: it["id"] for it in kb.list_entries("code")}
        b1 = kb.block_for({"type": "code", "goal": "部署口径", "context": "", "title": ""})
        self.assertIn("部署口径", b1)
        self._heat(kb, ids["部署口径第二案"], 1)
        b2 = kb.block_for({"type": "code", "goal": "部署口径", "context": "", "title": ""})
        self.assertLess(b2.index("部署要点乙"), b2.index("部署要点甲"))  # 加热后乙胜出


if __name__ == "__main__":
    import unittest
    unittest.main()
