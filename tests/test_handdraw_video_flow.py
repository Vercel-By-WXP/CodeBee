# -*- coding: utf-8 -*-
"""手绘视频类型（handdraw_video）注册与全链接线回归。

sweety-handdraw-video（MIT）方法论整合（2026-10-21）：新类型必须同时挂上
流程注册、交付契约、调度维度、耗时基线、经验包 scope 与节奏检测措辞，
本文件逐处锁定，防止后续类型加注册漏接线。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import aiflavor, dispatch, flows, paths, skills, store, usage
from app.core import handdraw_render, task_compile
from app.core.pipeline import CONTENT_DELIVERY_CONTRACTS, _content_contract, _content_role

REPO = Path(__file__).resolve().parents[1]

# 集成回归探针：TUTTI_DATA 隔离 + mock 执行跑完 _run_content_review，
# 断言终态 status（未达标≠done 是 2026-10-21 r-20261021-164007 实案的修复）。
PROBE = r"""
import json, sys
from unittest import mock
from app.core import store
from app.core import pipeline
from app.core.pipeline import _run_content_review

mode, workdir = sys.argv[1], sys.argv[2]
task = {"type": "handdraw_video", "title": "回归", "goal": "验证终态",
        "workdir": workdir, "manuscript": "handdraw-video.md",
        "rubric": ["开场钩子", "讲解因果"], "threshold": 7.0}
agents = [{"id": "impl-a", "mode": "mock", "label": "MockAuthor"}]
if mode == "critic_broken":
    agents.append({"id": "critic-x", "mode": "real", "label": "BrokenCritic"})
    task["implementer"] = "impl-a"
    task["critics"] = ["critic-x"]
    task["rounds"] = 2
else:
    task["rounds"] = 1 if mode == "fail1" else 2

t = store.create_task(task)
run = store.create_run("orchestration", "回归", task_id=t["id"])
run_id = run["id"]
store.update_run(run_id, status="running")
if mode == "critic_broken":
    with mock.patch.object(pipeline, "_run_step",
                           return_value={"ok": False, "text": "boom"}), \
         mock.patch.object(pipeline.time, "sleep", lambda *_a, **_k: None):
        _run_content_review({"id": run_id}, task, agents, None, {}, "manual")
else:
    _run_content_review({"id": run_id}, task, agents, None, {}, "manual")
r = store.get_run(run_id)
v = r.get("verdict") or {}
print("@@RESULT@@" + json.dumps({
    "status": r.get("status"), "error": (r.get("error") or "")[:80],
    "summary": (r.get("summary") or "")[:40],
    "publishable": v.get("publishable"),
    "rounds_used": v.get("rounds_used"),
    "review_degraded": v.get("review_degraded"),
    "has_video": "video" in v,
}, ensure_ascii=True))
"""


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


class HanddrawRevisionRoundsTests(unittest.TestCase):
    """成片类型在 auto 车道必须保留修订回炉（r-20261021-164007 实案：
    被单评审短链压成 1 轮后，评审断流即终局，修订循环从未生效）。"""

    def test_auto_lane_keeps_revision_rounds_for_handdraw(self):
        wf = task_compile.content_workflow(
            {"type": "handdraw_video", "rounds": 2}, "default", mode="auto")
        self.assertFalse(wf["outline"])
        self.assertEqual(wf["reviewers"], 1)
        self.assertEqual(wf["review_rounds"], 2)
        self.assertIn("回炉", wf["reason"])

    def test_plain_doc_keeps_revision_rounds(self):
        """2026-10-22 起修订回炉对全部评审型内容开放（article 实案：综合过线
        但单维卡线，1 轮终局连修订都没进），标准内容不再 1 轮定生死。"""
        wf = task_compile.content_workflow(
            {"type": "doc", "rounds": 2}, "default", mode="auto")
        self.assertEqual(wf["reviewers"], 1)
        self.assertEqual(wf["review_rounds"], 2)
        self.assertIn("回炉", wf["reason"])

    def test_fast_lane_stays_single_round(self):
        wf = task_compile.content_workflow(
            {"type": "handdraw_video", "rounds": 2}, "easy", mode="fast")
        self.assertEqual(wf["review_rounds"], 1)

    def test_user_rounds_respected(self):
        wf = task_compile.content_workflow(
            {"type": "handdraw_video", "rounds": 1}, "default", mode="auto")
        self.assertEqual(wf["review_rounds"], 1)


class StoryboardSchemaTests(unittest.TestCase):
    def _write(self, payload):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                        encoding="utf-8")
        json.dump(payload, f, ensure_ascii=False)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def _valid(self):
        return {"title": "演示", "resolution": [720, 1280], "fps": 30,
                "scenes": [
                    {"id": "s1", "narration": "第一段旁白。",
                     "elements": [{"kind": "title", "text": "标题"},
                                  {"kind": "flow", "items": ["甲", "乙"]}]}]}

    def test_valid_board_passes(self):
        board, err = handdraw_render.validate_storyboard(self._write(self._valid()))
        self.assertIsNone(err)
        self.assertEqual(len(board["scenes"]), 1)

    def test_missing_scenes_rejected(self):
        _, err = handdraw_render.validate_storyboard(self._write({"title": "x"}))
        self.assertIn("scenes", err)

    def test_unknown_kind_rejected(self):
        bad = self._valid()
        bad["scenes"][0]["elements"].append({"kind": "video", "text": "x"})
        _, err = handdraw_render.validate_storyboard(self._write(bad))
        self.assertIn("kind", err)

    def test_flow_needs_two_items(self):
        bad = self._valid()
        bad["scenes"][0]["elements"][1]["items"] = ["只有一项"]
        _, err = handdraw_render.validate_storyboard(self._write(bad))
        self.assertIn("flow", err)

    def test_empty_narration_rejected(self):
        bad = self._valid()
        bad["scenes"][0]["narration"] = "  "
        _, err = handdraw_render.validate_storyboard(self._write(bad))
        self.assertIn("narration", err)

    def test_resolution_bounds(self):
        bad = self._valid()
        bad["resolution"] = [100, 99]
        _, err = handdraw_render.validate_storyboard(self._write(bad))
        self.assertIn("resolution", err)


class RenderEntryTests(unittest.TestCase):
    def test_missing_storyboard_reports_honestly(self):
        ok, video, note = handdraw_render.render_for_run(
            {}, os.path.join(tempfile.gettempdir(), "no-such-ws-8f3d1"))
        self.assertFalse(ok)
        self.assertIsNone(video)
        self.assertIn("storyboard", note)

    def test_invalid_schema_fails_before_interpreter_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "storyboard.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"title": "x"}, f)
            with mock.patch.object(handdraw_render, "find_interpreter",
                                   side_effect=AssertionError("should not probe")):
                ok, video, note = handdraw_render.render_for_run({}, tmp)
        self.assertFalse(ok)
        self.assertIn("不合渲染契约", note)

    def test_subprocess_review_outcomes(self):
        for mode, expect in (("fail1", "failed"), ("pass2", "done"),
                             ("critic_broken", "done")):
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory() as tmp:
                    ws = os.path.join(tmp, "ws")
                    os.makedirs(ws)
                    env = dict(os.environ)
                    env["TUTTI_DATA"] = tmp
                    env["PYTHONIOENCODING"] = "utf-8"
                    proc = subprocess.run(
                        [sys.executable, "-c", PROBE, mode, ws],
                        cwd=str(REPO), env=env, timeout=180,
                        capture_output=True, text=True)
                    if proc.returncode != 0:
                        raise AssertionError(
                            "probe %s failed: %s" % (mode, proc.stderr[-2000:]))
                    line = [l for l in proc.stdout.splitlines()
                            if l.startswith("@@RESULT@@")][-1]
                    result = json.loads(line[len("@@RESULT@@"):])
                    self.assertEqual(result["status"], expect, result)
                    if mode == "fail1":
                        self.assertFalse(result["publishable"])
                        # 2026-10-24 起评审做完了只是没过线＝诚实报「评审未达标」，
                        # 不再套「评审未完成/服务不可用」模板（把内容问题谎报成故障）
                        self.assertIn("评审未达标", result["error"])
                        self.assertEqual(result["rounds_used"], 1)
                    elif mode == "pass2":
                        self.assertTrue(result["publishable"])
                        self.assertFalse(result["has_video"])
                    else:
                        self.assertFalse(result["publishable"])
                        self.assertFalse(result["review_degraded"])
                        self.assertIn("本地兜底完成", result["summary"])


if __name__ == "__main__":
    unittest.main()
