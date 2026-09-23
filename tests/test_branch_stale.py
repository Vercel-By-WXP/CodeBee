# -*- coding: utf-8 -*-
"""分支计划过期标记（branching.mark_stale，inkos 借鉴第三点）单测。

跑法：python -m unittest discover -s tests -p "test_branch_stale.py" -v
"""
from __future__ import annotations

from base import BaseTest


class BranchStaleTests(BaseTest):
    def _mk_audit(self, chapters=(1, 2, 3)):
        """造含多章的 branch-plans.md 审计文件。"""
        from app.core import branching
        lines = ["# 多线剧情推演（每章分支计划与择优，供回看）\n"]
        for c in chapters:
            lines.append("## 第 %d 章" % c)
            lines.append("- ✅ 分支甲（钩子：钩）——理由")
            lines.append("- 分支乙（钩子：钩）——理由\n")
        p = self.workdir / ".codebee" / "branch-plans.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines), encoding="utf-8")
        return p

    def test_mark_stale_only_target_chapter(self):
        """标记只影响目标章，其余章节头不动。"""
        from app.core import branching
        p = self._mk_audit(chapters=(1, 2, 3))
        branching.mark_stale(str(self.workdir), 2)
        txt = p.read_text(encoding="utf-8")
        self.assertIn("## 第 2 章（已过期，正史已重写）", txt)
        self.assertIn("## 第 1 章\n", txt)          # 其余不动
        self.assertIn("## 第 3 章\n", txt)

    def test_idempotent_no_double_mark(self):
        """重复标记不叠加（幂等）。"""
        from app.core import branching
        p = self._mk_audit(chapters=(1,))
        branching.mark_stale(str(self.workdir), 1)
        branching.mark_stale(str(self.workdir), 1)
        txt = p.read_text(encoding="utf-8")
        self.assertEqual(txt.count("已过期"), 1)

    def test_no_file_or_bad_dir_silent(self):
        """无审计文件/坏目录静默不炸。"""
        from app.core import branching
        branching.mark_stale(str(self.workdir), 1)      # 文件不存在
        branching.mark_stale(str(self.workdir / "nope"), 1)  # 目录不存在

    def test_append_after_stale_works(self):
        """标记过期后新推演照常追加（重跑场景：过期间隔+新计划）。"""
        from app.core import branching
        self._mk_audit(chapters=(1,))
        branching.mark_stale(str(self.workdir), 1)
        # 模拟重跑：新 plan_branches 的 _append_audit
        branching._append_audit(str(self.workdir), 1,
                               [{"beats": "新分支", "hook": "h", "why": "w"}], 0)
        txt = (self.workdir / ".codebee" / "branch-plans.md").read_text(encoding="utf-8")
        self.assertIn("已过期", txt)
        self.assertIn("新分支", txt)               # 新计划紧跟其后


if __name__ == "__main__":
    import unittest
    unittest.main()
