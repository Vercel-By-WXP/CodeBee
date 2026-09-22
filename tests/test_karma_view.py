# -*- coding: utf-8 -*-
"""karma 可见化（won/lost 透传到 view）保险测试。

跑法：python -m unittest discover -s tests -p "test_karma_view.py" -v
背景：outcome 加权已接线但 UI 依赖 view() 透传 won/lost——字段只在
note_outcome 写入后才存在，此测试锁住「写入→view 可见」的通路。
"""
from __future__ import annotations

from base import BaseTest


class KarmaViewTests(BaseTest):
    def test_note_outcome_fields_reach_view(self):
        from app.core import skills
        skills._FILE = self.data_dir / "skills.json"
        a = skills.upsert_lesson("code", "karma 教训", "内容" * 4)
        skills.block_for({"type": "code", "goal": "x"}, run_id="r-k1")
        skills.note_outcome("r-k1", True)    # 过审 → won=1
        skills.block_for({"type": "code", "goal": "x"}, run_id="r-k2")
        skills.note_outcome("r-k2", False)   # 失败 → lost=1
        v = skills.view()
        hit = next(x for x in v["lessons"] if x["id"] == a["id"])
        self.assertEqual(hit.get("won"), 1)
        self.assertEqual(hit.get("lost"), 1)
        # 老教训（无 karma 字段）不炸：徽章渲染条件 won>0 缺字段即 falsy
        for x in v["lessons"]:
            self.assertIn(x.get("won", 0), (0, 1, int(x.get("won") or 0)))

    def test_ranking_uses_karma(self):
        """同相关性下 karma 参与排序（通路端到端复核）。"""
        from app.core import skills
        skills._FILE = self.data_dir / "skills.json"
        good = skills.upsert_lesson("code", "共享标题甲", "做法A" * 3)
        bad = skills.upsert_lesson("code", "共享标题乙", "做法B" * 3)
        for i in range(3):
            skills.block_for({"type": "code", "goal": "x"}, run_id="rg%d" % i)
            skills.note_outcome("rg%d" % i, True)
        with skills._LOCK:
            data = skills._load()
            for it in data["lessons"]:
                if it["id"] == bad["id"]:
                    it["lost"] = 2
            skills._save(data)
        top = skills.relevance_top(skills.list_lessons("code", only_enabled=True),
                                    {"type": "code", "goal": "共享标题"}, 1)
        self.assertEqual(top[0]["id"], good["id"])


if __name__ == "__main__":
    import unittest
    unittest.main()
