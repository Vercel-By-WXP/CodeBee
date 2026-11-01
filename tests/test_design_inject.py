# -*- coding: utf-8 -*-
"""DESIGN.md 注入（P1 借研收尾：UI 任务提示词接设计宪法）回归。

核心语义（2026-11-01，行动清单 #1 后半句）：
1. UI 任务（goal/title/context 关键词启发）→ discover 追加最近作用域的
   DESIGN.md，metadata.design 在案；
2. 非 UI 任务 → 不注入（注意力守恒：无关任务不吃宪法字数）；
3. 截断上限 + 预算共用；无 DESIGN.md → 静默跳过不报错。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core import agent_context


class _Root(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "AGENTS.md").write_text("# 仓库规矩\n直接在 main 干活。",
                                             encoding="utf-8")
        self.design = ("# CodeBee DESIGN.md\n\n" + "## 2. 色板\n" +
                       "强调色 #4e5ba6；禁止写死十六进制。\n" * 30)
        (self.root / "DESIGN.md").write_text(self.design, encoding="utf-8")

    def inject(self, goal, workdir=None):
        task = {"goal": goal, "title": goal, "workdir": str(workdir or self.root)}
        return agent_context.inject(task, str(workdir or self.root))


class InjectTests(_Root):
    def test_ui_task_gets_design_md(self):
        task, res = self.inject("把设置页的按钮改成圆角，样式跟主题走")
        self.assertIn("DESIGN.md", task["context"])
        self.assertIn("#4e5ba6", task["context"])
        self.assertIsNotNone(res.get("design"))
        self.assertGreater(res["design"]["chars"], 0)
        self.assertIn("design", task["agent_context"])

    def test_ui_keywords_english_and_cas(self):
        for goal in ("restyle the settings page CSS", "fix UI 布局",
                     "移动端适配手机壳", "顶栏图标换一套"):
            _task, res = self.inject(goal)
            self.assertIsNotNone(res.get("design"), goal)

    def test_non_ui_task_not_injected(self):
        task, res = self.inject("写一篇周报，总结本周发章情况")
        self.assertNotIn("DESIGN.md", task["context"])
        self.assertIsNone(res.get("design"))
        self.assertNotIn("design", task["agent_context"])
        self.assertIn("AGENTS.md", task["context"])   # 基线注入不受影响

    def test_missing_design_md_silent(self):
        (self.root / "DESIGN.md").unlink()
        task, res = self.inject("改一下按钮样式")
        self.assertIsNone(res.get("design"))
        self.assertIn("AGENTS.md", task["context"])

    def test_truncation_cap(self):
        task, res = self.inject("优化页面观感")
        self.assertLessEqual(res["design"]["chars"], 12000)
        self.assertLessEqual(res["chars"], agent_context.MAX_PROMPT_CHARS)

    def test_is_ui_task_edge(self):
        self.assertFalse(agent_context.is_ui_task(None))
        self.assertFalse(agent_context.is_ui_task({"goal": ""}))
        self.assertTrue(agent_context.is_ui_task({"goal": "CSS 修一下",
                                                  "title": "", "context": ""}))


if __name__ == "__main__":
    unittest.main()
