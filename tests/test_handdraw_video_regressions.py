# -*- coding: utf-8 -*-
"""Regression coverage for handdraw video packaging and terminal status."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.core import handdraw_render


REPO = Path(__file__).resolve().parents[1]
RENDER_FAILURE_PROBE = r"""
import json, sys
from unittest import mock
from app.core import handdraw_render, pipeline, store

workdir = sys.argv[1]
task = {
    "type": "handdraw_video", "title": "渲染失败回归",
    "goal": "验证渲染失败不显示成功", "workdir": workdir,
    "manuscript": "handdraw-video.md",
    "rubric": ["开场钩子", "讲解因果"], "threshold": 7.0, "rounds": 1,
}
created = store.create_task(task)
run = store.create_run("orchestration", task["title"], task_id=created["id"])
store.update_run(run["id"], status="running")
review = {"scores": {"开场钩子": 9, "讲解因果": 9}, "issues": [], "summary": "通过"}
with mock.patch.object(pipeline, "_run_step", return_value={
        "ok": True, "text": json.dumps(review, ensure_ascii=False)}), \
     mock.patch.object(handdraw_render, "render_for_run",
                       return_value=(False, None, "渲染器退出码 2：脚本不存在")):
    pipeline._run_content_review(
        {"id": run["id"]}, task,
        [{"id": "author", "mode": "real", "label": "作者"}],
        None, {}, "manual")
finished = store.get_run(run["id"])
report = (store.run_dir(run["id"]) / "report.md").read_text(encoding="utf-8")
print("@@RESULT@@" + json.dumps({
    "status": finished.get("status"), "error": finished.get("error"),
    "summary": finished.get("summary"), "verdict": finished.get("verdict"),
    "report": report,
    "render_steps": [step.get("status") for step in finished.get("steps", [])
                     if step.get("role") == "render"],
}, ensure_ascii=True))
"""


class HanddrawPackagingTests(unittest.TestCase):
    def test_renderer_comes_from_repository_tools_directory(self):
        renderer = handdraw_render.renderer_path()
        self.assertEqual(renderer, str(REPO / "tools" / "render_handdraw.py"))
        self.assertTrue(os.path.isfile(renderer))

    def test_npm_package_includes_renderer_script(self):
        package = json.loads((REPO / "package.json").read_text(encoding="utf-8"))
        self.assertIn("tools/render_handdraw.py", package["files"])


class HanddrawTerminalStatusTests(unittest.TestCase):
    def test_render_failure_blocks_success_terminal_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / "workdir"
            workdir.mkdir()
            (workdir / "handdraw-video.md").write_text("已存在的稿件", encoding="utf-8")
            (workdir / "storyboard.json").write_text(json.dumps({
                "title": "回归", "resolution": [720, 1280], "fps": 30,
                "scenes": [{
                    "id": "s1", "narration": "一段旁白。",
                    "elements": [{"kind": "title", "text": "标题"}],
                }],
            }, ensure_ascii=False), encoding="utf-8")
            env = dict(os.environ)
            env["TUTTI_DATA"] = str(Path(tmp) / "data")
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.run(
                [sys.executable, "-c", RENDER_FAILURE_PROBE, str(workdir)],
                cwd=str(REPO), env=env, timeout=180,
                capture_output=True, text=True)
            if proc.returncode != 0:
                raise AssertionError("probe failed: %s" % proc.stderr[-2000:])
            line = [item for item in proc.stdout.splitlines()
                    if item.startswith("@@RESULT@@")][-1]
            result = json.loads(line[len("@@RESULT@@"):])
            verdict = result.get("verdict") or {}
            self.assertEqual(result.get("status"), "failed")
            self.assertFalse(verdict.get("publishable"))
            self.assertTrue(verdict.get("review_passed"))
            self.assertIn("成片渲染失败", result.get("summary") or "")
            self.assertIn("渲染器退出码", result.get("error") or "")
            self.assertEqual(result.get("render_steps"), ["failed"])
            self.assertIn("评审通过但成片渲染失败", result.get("report") or "")


if __name__ == "__main__":
    unittest.main()
