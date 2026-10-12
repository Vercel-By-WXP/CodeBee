"""内置智能体步骤日志兜底：steps/*.log 缺席时用 run.json 实时流拼可读日志。

背景：CodeBee（builtin）的实时输出走 stream_step 写 run.json 的
thinking/stream/activity，从不创建日志文件；大屏下钻抽屉/蜂巢弹窗等读日志
文件的面板对它永远「日志为空」，分不清真死还是在干活（2026-10-12）。
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import store

LOG_REL = "steps/01-chat-builtin.log"


class TestBuiltinStepLogFallback(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="steplog-fallback-"))
        for p in (mock.patch.object(store, "_RUNS", {}),
                  mock.patch.object(store.paths, "RUNS_DIR", tmp)):
            p.start()
            self.addCleanup(p.stop)
        store._RUNS["r1"] = {"id": "r1", "steps": []}

    def test_running_builtin_step_shows_activity_thinking_stream(self):
        store._RUNS["r1"]["steps"] = [{
            "n": 1, "role": "chat", "agent": "builtin", "status": "running",
            "log": LOG_REL, "activity": ["请求工具: read_file", "请求工具: search_content"],
            "thinking": "正在检查大纲结构", "stream": "",
        }]
        text = store.read_step_log("r1", LOG_REL, pretty=True)
        self.assertIn("【活动】请求工具: read_file", text)
        self.assertIn("【思考】 正在检查大纲结构", text)
        self.assertNotIn("【正文】", text)  # 正文流还没到就不硬造一节

    def test_finished_builtin_step_shows_thinking_output_summary(self):
        store._RUNS["r1"]["steps"] = [{
            "n": 1, "role": "chat", "agent": "builtin", "status": "done",
            "log": LOG_REL, "thinking": "收尾前的思考",
            "output": "最终正文", "summary": "结论一句话",
        }]
        text = store.read_step_log("r1", LOG_REL)
        self.assertIn("【思考】 收尾前的思考", text)
        self.assertIn("【正文】 最终正文", text)
        self.assertIn("【摘要】 结论一句话", text)
        self.assertNotIn("【活动】", text)  # 收尾即清的过程快照不再出现

    def test_existing_log_file_wins_over_live_fields(self):
        step = {"n": 1, "role": "implement", "agent": "codex", "status": "done",
                "log": "steps/01-implement-codex.log", "summary": "CLI 结论"}
        store._RUNS["r1"]["steps"] = [step]
        log_file = store.paths.RUNS_DIR / "r1" / "steps" / "01-implement-codex.log"
        log_file.parent.mkdir(parents=True)
        log_file.write_text("CLI 原始输出", encoding="utf-8")
        text = store.read_step_log("r1", step["log"])
        self.assertIn("CLI 原始输出", text)
        self.assertNotIn("【摘要】", text)

    def test_no_step_and_no_live_data_stay_empty(self):
        store._RUNS["r1"]["steps"] = [{
            "n": 1, "role": "chat", "agent": "builtin", "status": "running",
            "log": LOG_REL,
        }]
        self.assertEqual(store.read_step_log("r1", LOG_REL), "")
        self.assertEqual(store.read_step_log("r1", "steps/99-ghost.log"), "")

    def test_synthesized_text_respects_tail(self):
        store._RUNS["r1"]["steps"] = [{
            "n": 1, "role": "chat", "agent": "builtin", "status": "done",
            "log": LOG_REL, "thinking": "想" * 9000, "summary": "结尾结论",
        }]
        text = store.read_step_log("r1", LOG_REL, tail=4000)
        self.assertLessEqual(len(text), 4100)
        self.assertIn("...(已截断)...", text)
        self.assertIn("【摘要】 结尾结论", text)  # 尾部夹取保住收尾结论


if __name__ == "__main__":
    unittest.main()
