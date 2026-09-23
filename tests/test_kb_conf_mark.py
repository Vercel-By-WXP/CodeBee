# -*- coding: utf-8 -*-
"""知识注入置信标注（block_for ［有据］标记）单测。

跑法：python -m unittest discover -s tests -p "test_kb_conf_mark.py" -v
"""
from __future__ import annotations

from base import BaseTest


class ConfMarkTests(BaseTest):
    def _kb(self):
        from app.core import knowledge
        knowledge._FILE = self.data_dir / "knowledge.json"
        return knowledge

    def test_high_gets_mark_medium_not(self):
        kb = self._kb()
        kb.upsert_entry("code", "有据事实", "内容A" * 3,
                        status="approved", confidence="high")
        kb.upsert_entry("code", "普通事实", "内容B" * 3, status="approved")
        txt = kb.block_for({"type": "code", "goal": "事实"})
        self.assertIn("［有据］", txt)
        self.assertIn("有据事实", txt)
        # medium 无标记（行内不出现「有据」字样才对——用整行校验）
        import re
        line_med = [ln for ln in txt.splitlines() if "普通事实" in ln]
        self.assertTrue(line_med)
        self.assertNotIn("［有据］", line_med[0])

    def test_old_entries_without_confidence_fine(self):
        """老数据无 confidence 字段：不炸、不加标记。"""
        kb = self._kb()
        kb.upsert_entry("code", "老条目", "内容" * 3, status="approved")
        import json
        import pathlib
        p = pathlib.Path(str(self.data_dir / "knowledge.json"))
        raw = json.loads(p.read_text(encoding="utf-8"))
        raw["entries"][0].pop("confidence", None)
        p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        txt = kb.block_for({"type": "code", "goal": "老条目"})
        self.assertIn("老条目", txt)
        self.assertNotIn("［有据］", txt)


if __name__ == "__main__":
    import unittest
    unittest.main()
