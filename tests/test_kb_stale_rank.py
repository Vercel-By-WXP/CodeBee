# -*- coding: utf-8 -*-
"""知识库过期条目降权（block_for rank stale 惩罚，inkos 检索启发）单测。

跑法：python -m unittest discover -s tests -p "test_kb_stale_rank.py" -v
"""
from __future__ import annotations

import time

from base import BaseTest


class StaleRankTests(BaseTest):
    def _kb(self):
        from app.core import knowledge
        knowledge._FILE = self.data_dir / "knowledge.json"
        return knowledge

    def test_stale_ranks_below_fresh_at_same_relevance(self):
        """同等相关性：过期事实排在新鲜事实之后（top-k 截断时旧知识先出局）。"""
        from app.core import knowledge
        kb = self._kb()
        kb.STALE_DAYS = 30   # 测试缩短窗口
        old_day = time.strftime("%Y-%m-%d", time.localtime(time.time() - 40 * 86400))
        kb.upsert_entry("code", "接口性能基线", "旧版数据 A" * 3,
                        as_of=old_day, status="approved")
        kb.upsert_entry("code", "接口性能指标", "新版数据 B" * 3,
                        as_of=time.strftime("%Y-%m-%d"), status="approved")
        txt = kb.block_for({"type": "code", "goal": "接口性能"})
        # 两条都注入时，新鲜的在前
        self.assertLess(txt.index("新版数据"), txt.index("旧版数据"))

    def test_strong_relevance_still_wins_over_staleness(self):
        """相关性优先级不变：高度相关但过期的条目仍排在上位（stale 只是次级惩罚）。"""
        kb = self._kb()
        old_day = time.strftime("%Y-%m-%d", time.localtime(time.time() - 400 * 86400))
        kb.upsert_entry("code", "接口性能基线", "高度相关旧条" * 3,
                        as_of=old_day, status="approved")
        kb.upsert_entry("code", "完全无关话题", "低相关新条" * 3,
                        as_of=time.strftime("%Y-%m-%d"), status="approved")
        txt = kb.block_for({"type": "code", "goal": "接口性能基线"})
        # 高相关旧条排在上位（即便过期、即便两条都进 top-k）
        self.assertLess(txt.index("高度相关旧条"), txt.index("低相关新条"))

    def test_stale_marker_still_present(self):
        """既有 as_of 过期标注不回归。"""
        from app.core import knowledge
        kb = self._kb()
        knowledge.STALE_DAYS = 30
        old_day = time.strftime("%Y-%m-%d", time.localtime(time.time() - 40 * 86400))
        kb.upsert_entry("code", "接口性能基线", "内容" * 3, as_of=old_day, status="approved")
        txt = kb.block_for({"type": "code", "goal": "接口性能"})
        self.assertIn("可能过期", txt)


if __name__ == "__main__":
    import unittest
    unittest.main()
