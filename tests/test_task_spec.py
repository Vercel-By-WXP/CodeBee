# -*- coding: utf-8 -*-
"""任务规格文件化（.codebee/spec.md）单测——借鉴 planning-with-files/agent-orchestrator。

跑法：python -m unittest discover -s tests -p "test_task_spec.py" -v
"""
from __future__ import annotations

import os

from base import BaseTest


class TaskSpecTests(BaseTest):

    def _write(self, task, workdir=None):
        from app.core import pipeline
        return pipeline._write_task_spec(task, workdir or str(self.workdir))

    def _read(self, workdir=None):
        p = os.path.join(workdir or str(self.workdir), ".codebee", "spec.md")
        with open(p, encoding="utf-8") as f:
            return f.read()

    def test_spec_written_with_core_fields(self):
        task = {"title": "写一本都市小说", "type": "serial_novel", "goal": "写 2 万字",
                "created_at": "2026-09-18 21:00:00", "mode": "auto", "rounds": 2,
                "threshold": 7.0, "best_of": 3,
                "rubric": ["情节", "人物"], "verify_command": "pytest -q"}
        path = self._write(task)
        self.assertTrue(path and os.path.isfile(path))
        text = self._read()
        for frag in ("# 任务规格", "写一本都市小说", "serial_novel", "写 2 万字",
                     "评审轮数：2", "发布阈值：7.0", "赛马候选数：3",
                     "情节、人物", "pytest -q"):
            self.assertIn(frag, text)

    def test_serial_line(self):
        task = {"title": "t", "type": "serial_novel", "goal": "g",
                "serial": {"chapters": 8, "words_per_chapter": 2500, "variants": 2}}
        self._write(task)
        self.assertIn("连载：8 章 × 2500 字（赛马变体 2）", self._read())

    def test_minimal_task_no_optional_sections(self):
        task = {"title": "t", "type": "direct", "goal": "g"}
        self._write(task)
        text = self._read()
        self.assertIn("- 目标：g", text)
        self.assertNotIn("评审轮数", text)
        self.assertNotIn("连载：", text)

    def test_failure_silent(self):
        # 工作目录路径被一个文件占用：makedirs 抛错 → 静默返回空串，绝不挡任务
        blocker = self.tmp / "afile"
        blocker.write_text("x", encoding="utf-8")
        res = self._write({"title": "t", "goal": "g"}, workdir=str(blocker))
        self.assertEqual(res, "")

    def test_path_stays_inside_resolved_workdir(self):
        # 守卫契约：spec 恒落在 resolve(workdir) 内。含 ../ 的脏 workdir 先被
        # 规范化再收权（写到它「真正指向」的目录里，不会越到别处）
        import pathlib
        outer = self.tmp / "outer"
        outer.mkdir()
        fake = os.path.join(str(self.tmp), "..",
                            os.path.basename(str(self.tmp)), "outer")
        path = self._write({"goal": "g"}, workdir=fake)
        self.assertTrue(path)
        self.assertIn(pathlib.Path(outer).resolve(),
                      pathlib.Path(path).resolve().parents)


if __name__ == "__main__":
    unittest.main()
