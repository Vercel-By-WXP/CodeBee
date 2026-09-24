# -*- coding: utf-8 -*-
"""近似合并留痕（批2 记忆卫生）单测：教训库/知识库近似题被吸收时
记录变体标题 merged_titles——溯源可见、变体检索有据；去重+封顶 8。

跑法：python -m unittest discover -s tests -p "test_merge_trace.py" -v
"""
from __future__ import annotations

from base import BaseTest


class LessonMergeTraceTests(BaseTest):

    def _skills(self):
        from app.core import skills
        skills._FILE = self.data_dir / "skills.json"
        return skills

    def test_near_dup_merge_records_variant(self):
        """近似题吸收 → survivor.merged_titles 留变体，seen 累加。"""
        sk = self._skills()
        a = sk.upsert_lesson("novel", "节奏拖沓", "正文别拖", category="节奏爽点")
        sk.upsert_lesson("novel", "节奏拖沓问题", "正文别拖 v2", category="节奏爽点")
        items = [x for x in sk.list_lessons() if x["id"] == a["id"]]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].get("merged_titles"), ["节奏拖沓问题"])
        self.assertEqual(items[0]["seen"], 2)

    def test_repeated_variant_dedup_and_cap(self):
        """同变体重复只记一次；超 8 条截断留新。"""
        sk = self._skills()
        a = sk.upsert_lesson("novel", "主线偏航", "守住主线", category="情节逻辑")
        for i in range(10):
            sk.upsert_lesson("novel", "主线偏航变种%d号" % i, "守住主线",
                             category="情节逻辑")
        it = [x for x in sk.list_lessons() if x["id"] == a["id"]][0]
        mt = it.get("merged_titles") or []
        self.assertLessEqual(len(mt), 8)
        self.assertNotIn("主线偏航变种0号", mt)       # 最老的被截掉
        self.assertIn("主线偏航变种9号", mt)

    def test_distinct_title_separate_entry(self):
        """低相似标题各自成条，互不留痕。"""
        sk = self._skills()
        a = sk.upsert_lesson("novel", "人物扁平", "给欲望给弱点", category="人物塑造")
        b = sk.upsert_lesson("novel", "世界观断裂", "设定账本对齐", category="一致性")
        self.assertNotEqual(a["id"], b["id"])
        self.assertNotIn("merged_titles", a)


class KnowledgeMergeTraceTests(BaseTest):

    def _kb(self):
        from app.core import knowledge
        knowledge._FILE = self.data_dir / "knowledge.json"
        return knowledge

    def test_kb_near_dup_records_variant(self):
        """知识库同款：近似题吸收留痕。"""
        kb = self._kb()
        a = kb.upsert_entry("code", "网关限流规则", "429 进冷却", status="approved")
        kb.upsert_entry("code", "网关限流规则补充", "429 进冷却 v2", status="approved")
        it = [x for x in kb.list_entries("code") if x["id"] == a["id"]][0]
        self.assertEqual(it.get("merged_titles"), ["网关限流规则补充"])
        self.assertEqual(it["seen"], 2)

    def test_kb_exact_same_title_no_trace(self):
        """精确同题走同条路径不产生变体痕迹（title 相同不记）。"""
        kb = self._kb()
        a = kb.upsert_entry("code", "部署口径", "v1", status="approved")
        kb.upsert_entry("code", "部署口径", "v2", status="approved")
        it = [x for x in kb.list_entries("code") if x["id"] == a["id"]][0]
        self.assertNotIn("merged_titles", it)
        self.assertEqual(it["seen"], 2)


if __name__ == "__main__":
    import unittest
    unittest.main()
