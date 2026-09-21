# -*- coding: utf-8 -*-
"""经验库 kind 标记（做法/教训）单测——openhuman 记忆可视化借鉴。

跑法：python -m unittest discover -s tests -p "test_lesson_kind.py" -v
"""
from __future__ import annotations

from base import BaseTest


class LessonKindTests(BaseTest):
    def test_view_marks_procedure_vs_lesson(self):
        from app.core import skills
        skills._FILE = self.data_dir / "skills.json"
        skills.upsert_lesson("code", "做法：先列检查单", "步骤A" * 3)
        skills.upsert_lesson("code", "节奏拖沓", "每章要钩子" * 3)
        v = skills.view()
        kinds = {x["title"][:6]: x.get("kind") for x in v["lessons"]}
        self.assertEqual(kinds.get("做法：先列检查单"[:6] + "单"), None)  # 截断键不精确，改下查
        # 精确断言
        for x in v["lessons"]:
            if x["title"].startswith("做法："):
                self.assertEqual(x["kind"], "做法")
            else:
                self.assertEqual(x["kind"], "教训")

    def test_kind_not_persisted(self):
        """持久层是稳定 token（procedure/lesson），展示中文只存在于 view()。"""
        import json
        import pathlib
        from app.core import skills
        skills._FILE = self.data_dir / "skills.json"
        skills.upsert_lesson("code", "做法：再写一条", "步骤B" * 3)
        skills.upsert_lesson("code", "节奏拖沓", "要钩子" * 3)
        v = skills.view()
        by_title = {x["title"]: x for x in v["lessons"]}
        self.assertEqual(by_title["做法：再写一条"]["kind"], "做法")
        self.assertEqual(by_title["节奏拖沓"]["kind"], "教训")
        raw = json.loads(pathlib.Path(str(self.data_dir / "skills.json"))
                         .read_text(encoding="utf-8"))
        for x in raw.get("lessons") or []:
            self.assertIn(x.get("kind"), ("procedure", "lesson", None))
            self.assertNotIn(x.get("kind"), ("做法", "教训"))


if __name__ == "__main__":
    import unittest
    unittest.main()
