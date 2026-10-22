# -*- coding: utf-8 -*-
"""短篇故事类型（short_story）注册、单稿字数闸与全链接线回归。

2026-10-22 短篇流程：新类型必须同时挂上流程注册（含 story 规格）、交付契约、
调度维度、耗时基线、经验包 scope、AI 味叙事量词与字数闸；本文件逐处锁定，
防止后续类型加注册漏接线（同 test_handdraw_video_flow 的做法）。
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import aiflavor, dispatch, flows, paths, skills, store, task_request, usage
from app.core.pipeline import (CONTENT_DELIVERY_CONTRACTS, _content_contract,
                               _content_role, _story_length_repair)
from app.core.pipeline_content import story_length_block, story_spec


class ShortStoryFlowTests(unittest.TestCase):
    def test_builtin_flow_registered(self):
        flow = flows.get_flow("short_story")
        self.assertIsNotNone(flow)
        self.assertTrue(flow["builtin"])
        self.assertEqual(flow["engine"], "review")
        self.assertEqual(flow["manuscript"], "story.md")
        self.assertEqual(flow["icon"], "i-feather")
        self.assertNotIn("serial", flow)
        self.assertEqual(flow["story"], {"words": 12000})
        self.assertIn("开篇钩子", flow["rubric"])
        self.assertIn("结尾兑现", flow["rubric"])
        self.assertEqual(flow["threshold"], 7.5)
        self.assertIsNotNone(flows._ID_RE.match(flow["id"]))

    def test_dispatch_and_baseline_cover_short_story(self):
        self.assertIn("short_story", dispatch.TYPE_DIMENSIONS)
        self.assertEqual(dispatch.TYPE_DIMENSIONS["short_story"], "writing")
        self.assertIn("short_story", usage._DURATION_BASELINES)

    def test_norm_story_clamps(self):
        self.assertIsNone(flows._norm_story(None))
        self.assertIsNone(flows._norm_story({}))
        self.assertIsNone(flows._norm_story({"words": "abc"}))
        self.assertEqual(flows._norm_story({"words": 100}), {"words": 2000})
        self.assertEqual(flows._norm_story({"words": 999999}), {"words": 50000})
        self.assertEqual(flows._norm_story({"words": 9000}), {"words": 9000})

    def test_review_flow_has_delivery_contract(self):
        self.assertIn("short_story", CONTENT_DELIVERY_CONTRACTS)
        self.assertEqual(_content_role({"type": "short_story"}), "短篇小说作者")
        block = _content_contract({"type": "short_story",
                                   "story": {"words": 12000}})
        self.assertIn("单篇完整故事", block)
        self.assertIn("结尾", block)
        self.assertIn("12000", block)
        self.assertIn("9000", block)

    def test_story_spec_bounds_and_zero_noise(self):
        spec = story_spec({"story": {"words": 12000}})
        self.assertEqual(spec, {"words": 12000, "min_words": 9000, "max_words": 16200})
        self.assertIsNone(story_spec({}))
        self.assertIsNone(story_spec({"story": {}}))
        self.assertIsNone(story_spec({"story": {"words": 400}}))
        self.assertEqual(story_length_block({}), "")
        self.assertEqual(story_length_block({"story": {"words": 12000}}).count("12000"), 1)

    def test_create_task_fixes_short_story_params(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = (paths.DATA_DIR, paths.TASKS_DIR, paths.RUNS_DIR)
            paths.DATA_DIR = root
            paths.TASKS_DIR = root / "tasks"
            paths.RUNS_DIR = root / "runs"
            try:
                wd = root / "workspace"
                wd.mkdir()
                task = store.create_task({"type": "short_story",
                                          "goal": "写一个悬疑短篇",
                                          "workdir": str(wd)})
                self.assertEqual(task["type"], "short_story")
                self.assertEqual(task["engine"], "review")
                self.assertEqual(task["manuscript"], "story.md")
                self.assertEqual(task["rubric"], flows.get_flow("short_story")["rubric"])
                self.assertEqual(task["story"], {"words": 12000})
                self.assertNotIn("serial", task)
                # 任务级覆盖 + 钳位
                over = store.create_task({"type": "short_story", "goal": "写一个悬疑短篇",
                                          "workdir": str(wd),
                                          "story": {"words": 999999}})
                self.assertEqual(over["story"], {"words": 50000})
                # 显式 null 关闭流程默认（与 serial 同语义）
                off = store.create_task({"type": "short_story", "goal": "写一个悬疑短篇",
                                         "workdir": str(wd), "story": None})
                self.assertNotIn("story", off)
                # novel 流程无 story 默认，不凭空长出规格
                novel = store.create_task({"type": "novel", "goal": "写一个故事",
                                           "workdir": str(wd)})
                self.assertNotIn("story", novel)
            finally:
                paths.DATA_DIR, paths.TASKS_DIR, paths.RUNS_DIR = old

    def test_skillpack_scoped_injection(self):
        task = {"type": "short_story", "goal": "写一个悬疑短篇"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_data, old_file = paths.DATA_DIR, skills._FILE
            paths.DATA_DIR = root
            skills._FILE = root / "skills.json"
            try:
                with mock.patch.object(skills.novel_feedback, "block_for", return_value=""):
                    text, used = skills.block_for(task)
                self.assertIn("qimao-signing", used)
                self.assertIn("fanqie-novel", used)
                with mock.patch.object(skills.novel_feedback, "block_for", return_value=""):
                    _, used2 = skills.block_for({"type": "doc", "goal": "写文档"})
                self.assertNotIn("qimao-signing", used2)
            finally:
                paths.DATA_DIR, skills._FILE = old_data, old_file

    def test_aiflavor_unit_wording_for_short_story(self):
        text = "\n\n".join(
            "屋里的灯忽明忽暗，她数到第七声敲门忽然停住，门外传来说话声。难道是他回来了？"
            for _ in range(6))
        line = aiflavor.report_line(text, "short_story")
        if line:
            self.assertIn("篇末", line)
            self.assertNotIn("章末", line)


class TaskRequestShortStoryTests(unittest.TestCase):
    def test_parse_reads_target_words(self):
        p = task_request.parse("新建一个短篇故事任务：悬疑题材，1.2万字")
        self.assertEqual(p["type"], "short_story")
        self.assertEqual(p["story"], {"words": 12000})

    def test_parse_reads_plain_word_count(self):
        p = task_request.parse("新建短篇小说任务：写一个复仇故事，共9000字")
        self.assertEqual(p["type"], "short_story")
        self.assertEqual(p["story"], {"words": 9000})

    def test_parse_defaults_to_flow_words(self):
        p = task_request.parse("新建短篇任务：写个甜宠故事")
        self.assertEqual(p["type"], "short_story")
        self.assertEqual(p["story"], {"words": 12000})

    def test_serial_tasks_keep_serial_parsing(self):
        p = task_request.parse("新建连载小说任务：都市女频，共8章，每章2500字")
        self.assertEqual(p["type"], "serial_novel")
        self.assertEqual(p["serial"]["chapters"], 8)
        self.assertEqual(p["serial"]["words_per_chapter"], 2500)
        self.assertNotIn("story", p)


class StoryLengthGateTests(unittest.TestCase):
    """单稿字数闸：不足扩写、扩写步失败与耗尽三条路都要能走到（闸必须能红）。"""

    def _make_task(self, workdir, words=1000):
        return {"type": "short_story", "title": "闸门回归", "goal": "写悬疑短篇",
                "workdir": workdir, "manuscript": "story.md",
                "story": {"words": words}}

    def test_underlength_fails_after_exhausted_expansions(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / "story.md").write_text("短" * 100, encoding="utf-8")
            task = self._make_task(str(wd), words=1000)
            spec = story_spec(task)   # min 750
            # 扩写步"成功"但不改文件 → 两轮耗尽仍欠下限
            with mock.patch("app.core.pipeline._run_step",
                            return_value={"ok": True, "text": ""}):
                err = _story_length_repair(
                    "run-1", task, {"id": "impl", "mode": "real"}, "default",
                    "story.md", str(wd), spec, None, step_wd=str(wd))
            self.assertIn("字数闸未过", err)
            self.assertIn("750", err)
            self.assertIn("1000", err)

    def test_expand_step_writes_until_threshold_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / "story.md").write_text("短" * 100, encoding="utf-8")
            task = self._make_task(str(wd), words=1000)
            spec = story_spec(task)   # min 750

            def fake_expand(run_id, role, agent, prompt, cwd, **kwargs):
                Path(cwd).joinpath("story.md").write_text("长" * 800, encoding="utf-8")
                return {"ok": True}

            with mock.patch("app.core.pipeline._run_step", side_effect=fake_expand):
                err = _story_length_repair(
                    "run-1", task, {"id": "impl", "mode": "real"}, "default",
                    "story.md", str(wd), spec, None, step_wd=str(wd))
            self.assertEqual(err, "")

    def test_expand_step_failure_reports_step_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / "story.md").write_text("短" * 100, encoding="utf-8")
            task = self._make_task(str(wd), words=1000)
            spec = story_spec(task)
            with mock.patch("app.core.pipeline._run_step",
                            return_value={"ok": False, "error": "上游断流"}):
                err = _story_length_repair(
                    "run-1", task, {"id": "impl", "mode": "real"}, "default",
                    "story.md", str(wd), spec, None, step_wd=str(wd))
            self.assertIn("字数闸扩写失败", err)
            self.assertIn("上游断流", err)


if __name__ == "__main__":
    unittest.main()
