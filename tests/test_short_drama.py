# -*- coding: utf-8 -*-
"""短剧剧本 MVP 的流程、路由和交付契约回归。"""
from __future__ import annotations

import re
from pathlib import Path

from base import BaseTest


class ShortDramaFlowTests(BaseTest):
    def test_flow_has_script_first_metadata(self):
        from app.core import flows, dispatch, task_compile

        flow = flows.get_flow("short_drama")
        self.assertIsNotNone(flow)
        self.assertEqual(flow["engine"], "review")
        self.assertEqual(flow["manuscript"], "drama.md")
        self.assertEqual(dispatch.TYPE_DIMENSIONS["short_drama"], "writing")
        self.assertEqual(flow["threshold"], 7.0)
        self.assertGreaterEqual(len(flow["rubric"]), 5)
        compiled = task_compile.compile_task({"type": "short_drama", "goal": "都市悬疑短剧"})
        self.assertEqual(compiled["deliverable"], "drama.md")
        self.assertEqual(compiled["engine"], "review")
        self.assertIn("continuity", compiled["capabilities"])
        self.assertIn("long_context", compiled["capabilities"])

    def test_delivery_contract_requires_production_ready_structure(self):
        from app.core import pipeline

        role = pipeline._content_role({"type": "short_drama"})
        contract = pipeline._content_contract({"type": "short_drama"})
        self.assertEqual(role, "短剧编剧与剧本统筹")
        for marker in ("项目圣经", "角色表", "场景表", "分集大纲", "逐场剧本", "集尾钩子"):
            self.assertIn(marker, contract)
        self.assertIn("单集目标时长", contract)
        self.assertIn("不生成视频", contract)

    def test_auto_workflow_keeps_outline_and_two_reviewers(self):
        from app.core import task_compile

        workflow = task_compile.content_workflow(
            {"type": "short_drama"}, "default", mode="auto")
        self.assertTrue(workflow["outline"])
        self.assertEqual(workflow["reviewers"], 2)
        self.assertEqual(workflow["review_rounds"], 2)

    def test_task_request_recognizes_short_drama(self):
        from app.core import task_request

        payload = task_request.parse("创建一个短剧剧本任务，做 12 集都市悬疑，每集 90 秒")
        self.assertIsNotNone(payload)
        self.assertEqual(payload["type"], "short_drama")
        self.assertIn("12 集", payload["goal"])
        self.assertEqual(payload["drama"], {"episodes": 12, "seconds_per_episode": 90})

    def test_task_stores_bounded_drama_production_spec(self):
        from app.core import pipeline, store, task_compile

        task = store.create_task({
            "type": "short_drama", "goal": "都市悬疑短剧",
            "workdir": str(self.workdir),
            "drama": {"episodes": 250, "seconds_per_episode": 12},
        })
        self.assertEqual(task["drama"], {"episodes": 200, "seconds_per_episode": 15})
        self.assertEqual(task_compile.compile_task(task)["drama"], task["drama"])
        contract = pipeline._content_contract(task)
        self.assertIn("共 200 集", contract)
        self.assertIn("每集约 15 秒", contract)

    def test_ui_recommends_short_drama_before_generic_video_script(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "ui" / "app.js").read_text(encoding="utf-8")
        match = re.search(r'\["short_drama",\s*/(.*?)/i\].*?\["video_script",', source, re.S)
        self.assertIsNotNone(match, "short_drama 推荐规则应排在 video_script 前")
        self.assertRegex("写一个 12 集微短剧剧本", re.compile(match.group(1), re.I))

    def test_ui_describes_short_drama_specific_pipeline(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "ui" / "app.js").read_text(encoding="utf-8")
        match = re.search(r"function flowDesc\(f\)\s*\{(.*?)\n\}", source, re.S)
        self.assertIsNotNone(match)
        self.assertIn('f.id === "short_drama"', match.group(1))
        self.assertIn("项目圣经 → 分集大纲 → 逐场对白剧本", match.group(1))

    def test_ui_has_episode_and_duration_fields(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "app" / "ui" / "index.html").read_text(encoding="utf-8")
        js = (root / "app" / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="f-drama-only"', html)
        self.assertIn('id="f-drama-episodes"', html)
        self.assertIn('id="f-drama-seconds"', html)
        self.assertIn("payload.drama", js)


if __name__ == "__main__":
    import unittest
    unittest.main()
