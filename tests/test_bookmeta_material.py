# -*- coding: utf-8 -*-
"""作品信息素材收集对章节文件命名的兼容：连载流水线落 chapter-01.md（两位），
旧逻辑只认 chapter-001.md（三位），有 8 章成稿在手却报「没有正文」
（2026-09-28 马甲书一键生成案）。"""
from __future__ import annotations

import sys
from pathlib import Path

from base import BaseTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))


class TestCollectMaterialChapterNames(BaseTest):
    def _material_has_head(self, first_chapter_name):
        from app.core import bookmeta, store

        wd = self.workdir
        (wd / first_chapter_name).write_text(
            "# 第一章 二十万的黑锅\n\n\"签了它，二十万的事就算了。\"", encoding="utf-8")
        task = store.create_task({"type": "serial_novel", "title": "素材样例",
                                  "goal": "x", "workdir": str(wd)})
        material, outline = bookmeta._collect_material(task)
        return "## 第一章开头" in material, outline

    def test_two_digit_chapter_name_yields_first_chapter_head(self):
        has_head, _ = self._material_has_head("chapter-01.md")
        self.assertTrue(has_head)

    def test_three_digit_chapter_name_still_works(self):
        has_head, _ = self._material_has_head("chapter-001.md")
        self.assertTrue(has_head)

    def test_no_chapter_file_means_no_head(self):
        from app.core import bookmeta, store

        task = store.create_task({"type": "serial_novel", "title": "空目录",
                                  "goal": "x", "workdir": str(self.workdir)})
        material, _ = bookmeta._collect_material(task)
        self.assertNotIn("## 第一章开头", material)
