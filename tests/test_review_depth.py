# -*- coding: utf-8 -*-
"""评审深度随 diff 规模分级（pr-af 借鉴）单测：小改动不逼评审凑字数，
大变更先概览后深看高风险区；中等规模不给指引（默认深度）；
空 diff（无法获取变更）不误导评审员放松。
越范围编辑提醒（agent-delegate 借鉴）单测：计划锚定涉及文件而实际变更超出
清单时点名提醒评审员；未锚定/全部在清单内/目录声明/路径归一各分支。

跑法：python -m unittest discover -s tests -p "test_review_depth.py" -v
"""
from __future__ import annotations

from base import BaseTest


class ReviewDepthTests(BaseTest):

    def test_small_diff_gets_fast_track(self):
        """小 diff（<40 行）→ 快速评审指引，聚焦正确性不凑字数。"""
        from app.core import pipeline
        note = pipeline._review_depth_note("a\n" * 10)
        self.assertIn("小改动快速评审", note)
        self.assertIn("10 行", note)

    def test_large_diff_gets_risk_first(self):
        """大 diff（≥600 行）→ 概览+高风险区深看指引。"""
        from app.core import pipeline
        note = pipeline._review_depth_note("a\n" * 700)
        self.assertIn("大变更评审", note)
        self.assertIn("高风险区", note)

    def test_medium_diff_untouched(self):
        """中等规模 → 无指引（默认深度）。"""
        from app.core import pipeline
        self.assertEqual(pipeline._review_depth_note("a\n" * 100), "")

    def test_empty_diff_no_guidance(self):
        """空 diff → 不给快速评审指引（无法获取变更时应谨慎，不该说改动很小）。"""
        from app.core import pipeline
        self.assertEqual(pipeline._review_depth_note(""), "")
        self.assertEqual(pipeline._review_depth_note(None), "")


class OutOfScopeNoteTests(BaseTest):

    def test_no_declared_scope_silent(self):
        """计划未锚定文件（旧计划/手动/快路径）→ 不猜范围不提醒。"""
        from app.core import pipeline
        self.assertEqual(pipeline._scope_note(None, ["x.py"]), "")
        self.assertEqual(pipeline._scope_note([], ["x.py"]), "")

    def test_no_changed_files_silent(self):
        """声明了范围但拿不到变更清单 → 静默跳过（不拿声明清单空转提醒）。"""
        from app.core import pipeline
        self.assertEqual(pipeline._scope_note(["a.py"], None), "")

    def test_all_within_scope_silent(self):
        """变更全部落在声明清单内 → 无提醒。"""
        from app.core import pipeline
        note = pipeline._scope_note(
            ["app/core/pipeline.py", "tests/t.py"],
            ["app/core/pipeline.py", "tests/t.py", "app/core/pipeline.py"])
        self.assertEqual(note, "")

    def test_outside_file_flagged(self):
        """清单外文件 → 点名提醒，清单内文件不在点名之列。"""
        from app.core import pipeline
        note = pipeline._scope_note(
            ["app/core/pipeline.py"],
            ["app/core/pipeline.py", "app/ui/app.js"])
        self.assertIn("越范围变更提醒", note)
        self.assertIn("app/ui/app.js", note)
        self.assertIn("app/core/pipeline.py", note)

    def test_dir_scope_prefix_match(self):
        """目录型声明（以 / 结尾）按前缀覆盖其下文件。"""
        from app.core import pipeline
        self.assertEqual(
            pipeline._scope_note(["docs/"], ["docs/a.md", "docs/sub/b.md"]), "")
        note = pipeline._scope_note(["docs/"], ["docs/a.md", "src/x.py"])
        self.assertIn("src/x.py", note)

    def test_path_normalization(self):
        """反斜杠与 ./ 前缀归一后比对（计划侧 Windows 路径写法兼容）。"""
        from app.core import pipeline
        self.assertEqual(
            pipeline._scope_note([".\\app\\core\\pipeline.py"],
                                 ["./app/core/pipeline.py"]), "")
        note = pipeline._scope_note(["app\\core\\pipeline.py"], ["app\\ui\\app.js"])
        self.assertIn("app/ui/app.js", note)

    def test_plan_scope_union_dedup(self):
        """全步骤涉及文件并集：跨步去重、保持首现顺序、空/缺 files 步跳过。"""
        from app.core import pipeline
        subtasks = [
            {"title": "a", "files": ["x.py", "y.py"]},
            {"title": "b", "files": ["y.py", "z.py"]},
            {"title": "c"},
            {"title": "d", "files": []},
        ]
        self.assertEqual(pipeline._plan_scope_files(subtasks), ["x.py", "y.py", "z.py"])
        self.assertEqual(pipeline._plan_scope_files(None), [])


if __name__ == "__main__":
    import unittest
    unittest.main()
