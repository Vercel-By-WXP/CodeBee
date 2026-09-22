# -*- coding: utf-8 -*-
"""教训库近似题合并（upsert_lesson bigram 包含度 ≥0.8）单测——与知识库同款防膨胀。

跑法：python -m unittest discover -s tests -p "test_lesson_near_dup.py" -v
"""
from __future__ import annotations

from base import BaseTest


class LessonNearDupTests(BaseTest):
    def _sk(self):
        from app.core import skills
        skills._FILE = self.data_dir / "skills.json"
        return skills

    def _lessons(self):
        return self._sk().list_lessons()

    def test_near_title_merges(self):
        """同 scope 近似标题归并：seen+1、内容取新、不产生第二条。"""
        sk = self._sk()
        a = sk.upsert_lesson("code", "节奏拖沓", "每章结尾必须有钩子")
        b = sk.upsert_lesson("code", "节奏拖沓问题", "每章结尾必须有钩子；过渡章也要小冲突")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(b["seen"], 2)
        self.assertIn("过渡章", b["content"])
        self.assertEqual(len(self._lessons()), 1)

    def test_different_titles_seay_separate(self):
        sk = self._sk()
        sk.upsert_lesson("code", "节奏拖沓", "A" * 8)
        sk.upsert_lesson("code", "视角漂移", "B" * 8)
        self.assertEqual(len(self._lessons()), 2)

    def test_cross_scope_no_merge(self):
        sk = self._sk()
        a = sk.upsert_lesson("novel", "节奏拖沓", "A" * 8)
        b = sk.upsert_lesson("code", "节奏拖沓问题", "B" * 8)
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(len(self._lessons()), 2)

    def test_short_titles_exact_only(self):
        """<4 字符标题只认精确同题。"""
        sk = self._sk()
        sk.upsert_lesson("code", "拖沓", "A" * 8)
        sk.upsert_lesson("code", "拖沓.", "B" * 8)   # 标点归一后同指纹=精确合并
        sk.upsert_lesson("code", "跳戏", "C" * 8)
        titles = sorted(x["title"] for x in self._lessons())
        self.assertIn("跳戏", titles)
        self.assertEqual(len(self._lessons()), 2)   # 拖沓/拖沓. 合一条 + 跳戏

    def test_procedure_prefix_not_overmerged(self):
        """「做法：」前缀相同但内容方向不同的做法不误并（阈值保守验证）。"""
        sk = self._sk()
        a = sk.upsert_lesson("code", "做法：先列评分点再写", "A" * 8)
        b = sk.upsert_lesson("code", "做法：跑通验证命令再提交", "B" * 8)
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(len(self._lessons()), 2)

    def test_category_not_downgraded_on_merge(self):
        """近似合并时分类不降级（既有明确分类不被「未分类」覆盖）。"""
        sk = self._sk()
        sk.upsert_lesson("code", "节奏拖沓", "A" * 8, category="节奏爽点")
        b = sk.upsert_lesson("code", "节奏拖沓问题", "B" * 8, category=None)
        self.assertEqual(b["category"], "节奏爽点")


if __name__ == "__main__":
    import unittest
    unittest.main()
