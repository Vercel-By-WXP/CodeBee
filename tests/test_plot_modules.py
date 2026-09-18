# -*- coding: utf-8 -*-
"""剧情模块库（plot-modules.md，oh-story 拆文沉淀式）注入单测。

跑法：python -m unittest discover -s tests -p "test_plot_modules.py" -v
"""
from __future__ import annotations

import os

from base import BaseTest


class PlotModulesTests(BaseTest):

    def _mods(self, workdir=None):
        from app.core import pipeline
        return pipeline._plot_modules(workdir or str(self.workdir))

    def test_absent_returns_empty(self):
        self.assertEqual(self._mods(), "")

    def test_present_returns_block(self):
        (self.workdir / "plot-modules.md").write_text(
            "## 桥段：雨夜重逢\n两人檐下避雨，旧账重提。", encoding="utf-8")
        block = self._mods()
        self.assertIn("## 剧情模块库", block)
        self.assertIn("雨夜重逢", block)
        self.assertIn("不要照抄原句", block)

    def test_empty_file_returns_empty(self):
        (self.workdir / "plot-modules.md").write_text("   \n", encoding="utf-8")
        self.assertEqual(self._mods(), "")

    def test_bible_merge_in_serial_composition(self):
        # 组合规则：bible 与 modules 并成一个注入块；只有 modules 时块即 modules
        from app.core import pipeline
        (self.workdir / "story-bible.md").write_text("主角：林晚", encoding="utf-8")
        (self.workdir / "plot-modules.md").write_text("桥段A", encoding="utf-8")
        bible = pipeline._story_bible(str(self.workdir))
        mods = pipeline._plot_modules(str(self.workdir))
        merged = bible + "\n\n" + mods
        self.assertIn("故事圣经", merged)
        self.assertIn("剧情模块库", merged)


if __name__ == "__main__":
    unittest.main()
