# -*- coding: utf-8 -*-
"""手绘视频类型（handdraw_video）注册与全链接线回归。

sweety-handdraw-video（MIT）方法论整合（2026-10-21）：新类型必须同时挂上
流程注册、交付契约、调度维度、耗时基线、经验包 scope 与节奏检测措辞，
本文件逐处锁定，防止后续类型加注册漏接线。
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import aiflavor, dispatch, flows, paths, skills, store, usage
from app.core.pipeline import CONTENT_DELIVERY_CONTRACTS, _content_contract, _content_role


class HanddrawVideoFlowTests(unittest.TestCase):
    def test_builtin_flow_registered(self):
        flow = flows.get_flow("handdraw_video")
        self.assertIsNotNone(flow)
        self.assertTrue(flow["builtin"])
        self.assertEqual(flow["engine"], "review")
        self.assertEqual(flow["manuscript"], "handdraw-video.md")
        self.assertEqual(flow["icon"], "i-palette")
        self.assertNotIn("serial", flow)
        self.assertIn("开场钩子", flow["rubric"])
        self.assertEqual(flow["threshold"], 7.0)
        # 流程 ID 必须满足自定义流程同一套 ID 规范（overrides/分享码依赖）
        self.assertIsNotNone(flows._ID_RE.match(flow["id"]))

    def test_all_builtin_flows_have_dispatch_and_baseline(self):
        for f in flows.BUILTIN_FLOWS:
            self.assertIn(f["id"], dispatch.TYPE_DIMENSIONS, f["id"])
            self.assertIn(f["id"], usage._DURATION_BASELINES, f["id"])
        self.assertEqual(dispatch.TYPE_DIMENSIONS["handdraw_video"], "writing")

    def test_review_flows_have_delivery_contract(self):
        for f in flows.BUILTIN_FLOWS:
            # 连载走逐章管线，不消费单稿交付契约
            if f["engine"] == "review" and "serial" not in f:
                self.assertIn(f["id"], CONTENT_DELIVERY_CONTRACTS, f["id"])
        self.assertEqual(_content_role({"type": "handdraw_video"}), "手绘视频导演")
        block = _content_contract({"type": "handdraw_video"})
        self.assertIn("黄金5秒", block)
        self.assertIn("分镜", block)
        self.assertIn("制作包", block)
        self.assertIn("不静默降级", block)

    def test_create_task_fixes_handdraw_params(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = (paths.DATA_DIR, paths.TASKS_DIR, paths.RUNS_DIR)
            paths.DATA_DIR = root
            paths.TASKS_DIR = root / "tasks"
            paths.RUNS_DIR = root / "runs"
            try:
                wd = root / "workspace"
                wd.mkdir()
                task = store.create_task({"type": "handdraw_video",
                                          "goal": "讲清楚复利原理",
                                          "workdir": str(wd)})
                self.assertEqual(task["type"], "handdraw_video")
                self.assertEqual(task["engine"], "review")
                self.assertEqual(task["manuscript"], "handdraw-video.md")
                self.assertEqual(task["rubric"], flows.get_flow("handdraw_video")["rubric"])
                self.assertEqual(task["threshold"], 7.0)
                self.assertEqual(task["rounds"], 2)
                self.assertNotIn("serial", task)
            finally:
                paths.DATA_DIR, paths.TASKS_DIR, paths.RUNS_DIR = old

    def test_skillpack_scoped_injection(self):
        task = {"type": "handdraw_video", "goal": "讲清楚复利原理"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_data, old_file = paths.DATA_DIR, skills._FILE
            paths.DATA_DIR = root          # 用户包目录随 DATA_DIR 现取 → 指向空临时目录
            skills._FILE = root / "skills.json"
            try:
                with mock.patch.object(skills.novel_feedback, "block_for", return_value=""):
                    text, used = skills.block_for(task)
                self.assertIn("handdraw-video", used)
                self.assertIn("手绘讲解视频制作规范", text)
                self.assertIn("黄金5秒", text)
                # scope 隔离：其他类型不注入本包
                with mock.patch.object(skills.novel_feedback, "block_for", return_value=""):
                    _, used2 = skills.block_for({"type": "doc", "goal": "写文档"})
                self.assertNotIn("handdraw-video", used2)
            finally:
                paths.DATA_DIR, skills._FILE = old_data, old_file

    def test_pacing_unit_wording_for_handdraw(self):
        text = "\n\n".join(
            "复利的力量在于时间，第%d年本金翻了一倍，突然所有人都开始注意到这件事的代价。" % i
            for i in range(16))
        line = aiflavor.report_line(text, "handdraw_video")
        self.assertTrue(line)
        self.assertIn("段末", line)
        self.assertNotIn("章末", line)


if __name__ == "__main__":
    unittest.main()
