# -*- coding: utf-8 -*-
"""任务宪章（.codebee/constitution.md，借鉴 spec-kit constitution）单测。

跑法：python -m unittest discover -s tests -p "test_constitution.py" -v
"""
from __future__ import annotations

import os

from base import BaseTest


class ConstitutionTests(BaseTest):

    def _write(self, text):
        d = self.workdir / ".codebee"
        d.mkdir(exist_ok=True)
        (d / "constitution.md").write_text(text, encoding="utf-8")

    def test_absent_returns_empty(self):
        from app.core import pipeline
        self.assertEqual(pipeline._read_constitution(str(self.workdir)), "")

    def test_present_returns_block(self):
        from app.core import pipeline
        self._write("- 所有代码必须带类型注解\n- 测试覆盖 ≥80%")
        block = pipeline._read_constitution(str(self.workdir))
        self.assertIn("项目宪章", block)
        self.assertIn("类型注解", block)
        self.assertIn("优先级最高", block)

    def test_empty_file_returns_empty(self):
        from app.core import pipeline
        self._write("  \n")
        self.assertEqual(pipeline._read_constitution(str(self.workdir)), "")

    def test_size_cap(self):
        from app.core import pipeline
        self._write("字" * 20000)
        block = pipeline._read_constitution(str(self.workdir))
        self.assertLessEqual(len(block), 6200)   # 6000 上限+头尾


if __name__ == "__main__":
    unittest.main()
