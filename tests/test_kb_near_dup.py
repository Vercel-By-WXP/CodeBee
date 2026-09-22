# -*- coding: utf-8 -*-
"""知识库近似题合并（upsert_entry bigram Jaccard ≥0.75）单测。

跑法：python -m unittest discover -s tests -p "test_kb_near_dup.py" -v
背景：自动提炼每次 done 运行都跑，「X 优化」与「X 优化指南」各建一条把库撑爆。
"""
from __future__ import annotations

from base import BaseTest


class NearDupTests(BaseTest):
    def _kb(self):
        from app.core import knowledge
        knowledge._FILE = self.data_dir / "knowledge.json"
        return knowledge

    def test_title_sim_bounds(self):
        kb = self._kb()
        self.assertGreaterEqual(kb._title_sim("接口性能优化", "接口性能优化指南"), 0.75)
        self.assertLess(kb._title_sim("接口性能优化", "登录鉴权方案"), 0.3)
        self.assertEqual(kb._title_sim("", "任意"), 0.0)

    def test_near_title_merges_not_duplicates(self):
        """同 scope 近似标题归并为一条：seen+1、正文取新、无新条目。"""
        kb = self._kb()
        a = kb.upsert_entry("code", "接口性能优化", "第一版内容")
        b = kb.upsert_entry("code", "接口性能优化指南", "第二版内容")
        self.assertEqual(a["id"], b["id"])                     # 归并同一条
        self.assertEqual(b["seen"], 2)
        self.assertEqual(b["body"], "第二版内容")               # draft 覆盖 draft
        self.assertEqual(len(kb.load()["entries"] if hasattr(kb, "load") else
                             self._entries()), 1)

    def _entries(self):
        import json
        import pathlib
        p = pathlib.Path(str(self.data_dir / "knowledge.json"))
        return json.loads(p.read_text(encoding="utf-8")).get("entries") or []

    def test_different_titles_stay_separate(self):
        kb = self._kb()
        kb.upsert_entry("code", "接口性能优化", "A" * 10)
        kb.upsert_entry("code", "登录鉴权方案", "B" * 10)
        self.assertEqual(len(self._entries()), 2)              # 互不相似不归并

    def test_cross_scope_no_merge(self):
        """不同 scope 的近似标题不归并（scope 是第一身份）。"""
        kb = self._kb()
        a = kb.upsert_entry("code", "接口性能优化", "A" * 10)
        b = kb.upsert_entry("novel", "接口性能优化指南", "B" * 10)
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(len(self._entries()), 2)

    def test_short_titles_exact_only(self):
        """<4 字符标题只认精确同题（bigram 噪声大，不参与近似扫描）。"""
        kb = self._kb()
        kb.upsert_entry("code", "优化", "A" * 10)
        kb.upsert_entry("code", "排序", "B" * 10)   # 同为短题但含义无关
        self.assertEqual(len(self._entries()), 2)

    def test_approved_protection_on_near_dup(self):
        """近似题打到 approved 条目走修订候选，不覆盖正文（与精确同题同语义）。"""
        kb = self._kb()
        a = kb.upsert_entry("code", "接口性能优化", "人工确认版", status="approved")
        b = kb.upsert_entry("code", "接口性能优化指南", "自动提炼版")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(b["body"], "人工确认版")               # 正文未被覆盖
        self.assertTrue(b.get("revisions"))                    # 进修订候选


if __name__ == "__main__":
    import unittest
    unittest.main()
