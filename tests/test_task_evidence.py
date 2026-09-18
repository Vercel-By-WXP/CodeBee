# -*- coding: utf-8 -*-
"""验证证据持久化（.codebee/evidence.md，借鉴 gsd-pi validation evidence）单测。

跑法：python -m unittest discover -s tests -p "test_task_evidence.py" -v
"""
from __future__ import annotations

import os

from base import BaseTest


class TaskEvidenceTests(BaseTest):

    def _ev(self, lines, workdir=None):
        from app.core import pipeline
        return pipeline._write_task_evidence("r-ev", {"id": "t-ev"},
                                             workdir or str(self.workdir), lines)

    def _read(self, workdir=None):
        p = os.path.join(workdir or str(self.workdir), ".codebee", "evidence.md")
        with open(p, encoding="utf-8") as f:
            return f.read()

    def test_append_creates_with_header(self):
        path = self._ev(["验证命令 `pytest -q` → done（验证通过）"])
        self.assertTrue(path and os.path.isfile(path))
        text = self._read()
        self.assertIn("# 验证证据", text)
        self.assertIn("run r-ev", text)
        self.assertIn("pytest -q", text)

    def test_second_run_appends_without_header(self):
        self._ev(["第一轮证据"])
        self._ev(["第二轮证据"])
        text = self._read()
        self.assertEqual(text.count("# 验证证据"), 1)   # 头只写一次
        self.assertIn("第一轮证据", text)
        self.assertIn("第二轮证据", text)
        self.assertEqual(text.count("## "), 2)

    def test_empty_lines_no_write(self):
        res = self._ev([])
        self.assertEqual(res, "")
        self.assertFalse(os.path.exists(os.path.join(str(self.workdir),
                                                     ".codebee", "evidence.md")))

    def test_evidence_lines_from_run(self):
        from app.core import pipeline, store
        run = store.create_run("orchestration", "证据任务")
        store.add_step(run["id"], "verify", "builtin", "内置验证器")
        store.finish_step(run["id"], 1, "done", summary="验证通过")
        run2 = store.get_run(run["id"])
        run2["verdict"] = {"scores": {"情节": 8.5, "文笔": 9.0}, "threshold": 7.0,
                           "publishable": True, "overall": 8.8, "rounds_used": 2}
        store._RUNS[run["id"]] = run2   # 测试直塞 verdict
        lines = pipeline._evidence_lines_from_run(run["id"], {"verify_command": "pytest"})
        joined = "\n".join(lines)
        self.assertIn("验证命令 `pytest` → done", joined)
        self.assertIn("情节 8.5", joined)
        self.assertIn("达标", joined)
        self.assertIn("综合分 8.8 / 2 轮", joined)


if __name__ == "__main__":
    unittest.main()
