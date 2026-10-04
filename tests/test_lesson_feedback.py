# -*- coding: utf-8 -*-
"""教训卡「标记无用」显式负反馈（hippo-memory 差量，待深挖第 7 项清账）：

  1. 正常：lesson_op("useless") 停用 + useless 计数 +1；再启用不丢负反馈证据；
  2. 排序：带 useless 证据的同相关教训在注入候选排序中下沉（与失守同权粘滞）；
  3. 回归：enable/disable/delete 原语义不变、未知操作仍报错。

跑法：python -m unittest discover -s tests -p "test_lesson_feedback.py" -v
"""
from __future__ import annotations

from base import BaseTest


class LessonFeedbackTests(BaseTest):

    @staticmethod
    def _mk(skills, title):
        return skills.upsert_lesson("*", title, "正文内容", source="test",
                                    category="流程规范")

    def test_useless_disables_and_counts(self):
        from app.core import skills
        les = self._mk(skills, "示例教训甲")
        self.assertIsNone(skills.lesson_op(les["id"], "useless"))
        hit = next(x for x in skills.list_lessons("*") if x["id"] == les["id"])
        self.assertFalse(hit["enabled"], "标记无用应立即停用（不再注入）")
        self.assertEqual(hit["useless"], 1, "无用计数未落账")
        self.assertIsNone(skills.lesson_op(les["id"], "enable"))
        hit = next(x for x in skills.list_lessons("*") if x["id"] == les["id"])
        self.assertTrue(hit["enabled"])
        self.assertEqual(hit["useless"], 1, "再启用不应丢显式负反馈证据")

    def test_useless_sinks_injection_ranking(self):
        from app.core import skills
        keep_a = self._mk(skills, "连载前情提要怎么做")
        keep_b = self._mk(skills, "连载伏笔怎么埋")
        sink = self._mk(skills, "连载节奏怎么控")
        for x in (keep_a, keep_b, sink):
            skills.bump_hits([x["id"]])
            skills.bump_hits([x["id"]])   # hits=2：证据量对齐，只剩负反馈差量
        self.assertIsNone(skills.lesson_op(sink["id"], "useless"))
        lessons = [x for x in skills.list_lessons("*", only_enabled=True)
                   if x["id"] in {keep_a["id"], keep_b["id"]}]

        def by_id(items):
            return {x["id"] for x in items}

        task = {"goal": "连载前情提要与伏笔", "context": "", "title": ""}
        # 重新启用 sink 后（模拟用户反悔），排序应把它压到两条健康教训之后
        self.assertIsNone(skills.lesson_op(sink["id"], "enable"))
        pool = skills.list_lessons("*", only_enabled=True)
        top = skills.relevance_top(pool, task, limit=2)
        self.assertEqual(len(top), 2)
        self.assertNotIn(sink["id"], by_id(top),
                         "带无用证据的教训应排到同相关度的健康教训之后")
        top3 = skills.relevance_top(pool, task, limit=3)
        self.assertEqual(by_id(top3), {keep_a["id"], keep_b["id"], sink["id"]})

    def test_legacy_ops_and_unknown_op_unchanged(self):
        from app.core import skills
        les = self._mk(skills, "示例教训丁")
        self.assertIsNone(skills.lesson_op(les["id"], "disable"))
        hit = next(x for x in skills.list_lessons("*") if x["id"] == les["id"])
        self.assertFalse(hit["enabled"])
        self.assertNotIn("useless", hit, "disable 不应产生无用计数")
        self.assertIsNone(skills.lesson_op(les["id"], "delete"))
        self.assertEqual([x for x in skills.list_lessons("*")
                          if x["id"] == les["id"]], [])
        self.assertTrue(skills.lesson_op("no-such-id", "useless"), "不存在应报错")
        self.assertTrue(skills.lesson_op(les["id"], "explode"), "未知操作应报错")


if __name__ == "__main__":
    import unittest
    unittest.main()
