# -*- coding: utf-8 -*-
"""list_lessons karma 感知排序单测。

跑法：python -m unittest discover -s tests -p "test_lesson_karma_order.py" -v
"""
from __future__ import annotations

from base import BaseTest


class KarmaOrderTests(BaseTest):
    def _sk(self):
        from app.core import skills
        skills._FILE = self.data_dir / "skills.json"
        return skills

    def test_high_karma_first(self):
        """won-lost 高的排最前（即便 hits 低）；失守多的沉底。"""
        sk = self._sk()
        good = sk.upsert_lesson("code", "有效教训", "A" * 6)
        bad = sk.upsert_lesson("code", "失守教训", "B" * 6)
        mid = sk.upsert_lesson("code", "普通教训", "C" * 6)
        with sk._LOCK:
            data = sk._load()
            for it in data["lessons"]:
                if it["id"] == good["id"]:
                    it["won"], it["hits"] = 3, 0
                elif it["id"] == bad["id"]:
                    it["lost"], it["hits"] = 2, 5   # 热度高但净失守
                elif it["id"] == mid["id"]:
                    it["hits"] = 2
            sk._save(data)
        order = [x["title"] for x in sk.list_lessons("code")]
        self.assertEqual(order[0], "有效教训")          # karma 最高居首
        self.assertEqual(order[-1], "失守教训")         # 净失守沉底（不删除）
        self.assertIn("普通教训", order[1:2])

    def test_hits_tiebreak_within_same_karma(self):
        """同 karma 下按注入热度降序（原语义保留为次级键）。"""
        sk = self._sk()
        a = sk.upsert_lesson("code", "教训甲", "A" * 6)
        b = sk.upsert_lesson("code", "教训乙", "B" * 6)
        with sk._LOCK:
            data = sk._load()
            for it in data["lessons"]:
                if it["id"] == b["id"]:
                    it["hits"] = 9
            sk._save(data)
        order = [x["title"] for x in sk.list_lessons("code")]
        self.assertEqual(order[0], "教训乙")

    def test_relevance_top_unchanged(self):
        """注入排序（relevance_top）不受影响：相关性仍第一优先。"""
        sk = self._sk()
        good = sk.upsert_lesson("code", "共享标题甲", "A" * 6)
        with sk._LOCK:
            data = sk._load()
            for it in data["lessons"]:
                if it["id"] == good["id"]:
                    it["lost"] = 1
            sk._save(data)
        top = sk.relevance_top(sk.list_lessons("code", only_enabled=True),
                               {"type": "code", "goal": "共享标题"}, 1)
        self.assertEqual(top[0]["title"], "共享标题甲")  # 唯一相关仍注入


if __name__ == "__main__":
    import unittest
    unittest.main()
