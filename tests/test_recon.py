# -*- coding: utf-8 -*-
"""工作目录侦察（planner.workdir_recon + 规划 prompt 注入）单测。

覆盖：剪枝（node_modules/target/.codebee 不出现）、清单文件头部摘要、空目录/
不存在目录、截断、编排者与 CLI 两条规划链都注入侦察块。

跑法：python -m unittest discover -s tests -p "test_recon.py" -v
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from base import BaseTest

from app.core import planner


def _make_project(root):
    base = Path(root).resolve()
    assert base.exists() and base.parent.exists()
    (base / "src").mkdir(parents=True, exist_ok=True)
    (base / "node_modules" / "pkg").mkdir(parents=True, exist_ok=True)
    (base / "target").mkdir(parents=True, exist_ok=True)
    (base / ".codebee").mkdir(parents=True, exist_ok=True)
    (base / "package.json").write_text(
        '{\n  "name": "demo",\n  "version": "1.0.0",\n  "main": "src/a.py"\n}\n',
        encoding="utf-8")
    (base / "README.md").write_text("# Demo 项目\n\n一个用于测试的假项目。\n",
                                    encoding="utf-8")
    (base / "src" / "a.py").write_text("print('hi')\n", encoding="utf-8")
    (base / "node_modules" / "pkg" / "x.js").write_text("// dep\n", encoding="utf-8")
    (base / "target" / "b.o").write_text("binary\n", encoding="utf-8")
    (base / ".codebee" / "task_plan.md").write_text("# 任务计划\n", encoding="utf-8")


class TestWorkdirRecon(BaseTest):

    def test_lists_files_and_prunes(self):
        _make_project(self.workdir)
        out = planner.workdir_recon(self.workdir)
        self.assertIn("package.json", out)
        self.assertIn("src/a.py", out)
        self.assertIn("README.md 头部：", out)
        self.assertIn("Demo 项目", out)          # 清单文件头部摘要真读到了
        self.assertNotIn("node_modules", out)    # 依赖目录剪枝
        self.assertNotIn("target", out)          # 构建产物剪枝
        self.assertNotIn(".codebee", out)        # 任务元数据剪枝

    def test_empty_dir(self):
        out = planner.workdir_recon(self.workdir)
        self.assertIn("空目录", out)
        self.assertIn("全新项目", out)

    def test_missing_dir(self):
        out = planner.workdir_recon(str(Path(self.workdir).resolve() / "no-such"))
        self.assertIn("不存在", out)

    def test_empty_workdir_arg(self):
        self.assertIn("全新项目", planner.workdir_recon(""))

    def test_truncation_marks(self):
        base = Path(self.workdir).resolve()
        for i in range(planner._RECON_MAX_FILES + 30):
            (base / ("f%03d.txt" % i)).write_text("x", encoding="utf-8")
        out = planner.workdir_recon(self.workdir)
        self.assertIn("截断显示", out)
        self.assertIn("已省略", out)

    def test_char_budget_truncates(self):
        _make_project(self.workdir)
        out = planner.workdir_recon(self.workdir, max_chars=60)
        self.assertLessEqual(len(out), 80)
        self.assertIn("已截断", out)

    def test_recon_never_raises(self):
        # workdir 是文件不是目录——walk 会炸，必须折返成一句话
        base = Path(self.workdir).resolve()
        target = base / "afile"
        target.write_text("x", encoding="utf-8")
        out = planner.workdir_recon(str(target))
        self.assertTrue(out)


class TestPlanPromptInjection(BaseTest):

    def _task(self):
        return {"goal": "给 demo 加一个导出功能", "type": "code",
                "workdir": self.workdir, "context": "", "verify_command": ""}

    def test_prompt_contains_recon_block(self):
        _make_project(self.workdir)
        prompt = planner._code_plan_prompt(self._task())
        self.assertNotIn("__RECON__", prompt)            # 占位符必须被替换
        self.assertIn("工作目录侦察", prompt)
        self.assertIn("src/a.py", prompt)
        self.assertIn("给 demo 加一个导出功能", prompt)

    def test_orchestrator_plan_receives_recon(self):
        _make_project(self.workdir)
        captured = {}

        def fake_chat(pid, model, prompt, **kw):
            captured["prompt"] = prompt
            return {"ok": True, "text": json.dumps(
                {"difficulty": "easy",
                 "subtasks": [{"title": "t", "detail": "d", "files": ["src/a.py"]}]}),
                "error": ""}

        with mock.patch.object(planner, "_orchestrator",
                               return_value=({"id": "prov-x", "name": "X"}, "m1")), \
             mock.patch.object(planner.modelhub, "chat", side_effect=fake_chat):
            plan = planner.make_code_plan(self._task(), None, self.workdir)
        self.assertIsNotNone(plan)
        self.assertIn("src/a.py", captured["prompt"])    # 直连编排者拿到侦察块
        self.assertIn("编排者", plan["source"])

    def test_cli_plan_receives_recon(self):
        _make_project(self.workdir)
        captured = {}

        def fake_run_agent(agent, prompt, **kw):
            captured["prompt"] = prompt
            return {"ok": True, "text": json.dumps(
                {"difficulty": "easy",
                 "subtasks": [{"title": "t", "detail": "d"}]}), "error": ""}

        agent = {"id": "cli-x", "label": "CLI X", "mode": "cli", "kind": "generic"}
        with mock.patch.object(planner, "_orchestrator", return_value=None), \
             mock.patch.object(planner.runner, "run_agent", side_effect=fake_run_agent):
            plan = planner.make_code_plan(self._task(), agent, self.workdir)
        self.assertIsNotNone(plan)
        self.assertIn("src/a.py", captured["prompt"])
        self.assertIn("cli-x", plan["source"])


if __name__ == "__main__":
    unittest.main()
