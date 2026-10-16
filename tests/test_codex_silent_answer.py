# -*- coding: utf-8 -*-
"""codex「轮次静默空答」回归（2026-10-16 神瞳实案）。

qa 轮模型吐完开场白、跑完 6 条读文件命令就 turn.completed（run
r-20261016-164939-6833：exit 0、output 仅 711 tokens、无任何晚于工具动作的
agent_message）——开场白被当成最终答案展示，用户看到「本轮完成」却没有结论。

锁定行为：
1. 静默形态识别：有工具动作且没有晚于它们的 agent_message；纯说话/正常
   先调查后收尾的轮次不误伤；
2. 带会话原地追问一次（exec resume 同 sid + nudge 指令），取得结论则替换
   答案并合并两段 usage，nudged=True；
3. 追问仍静默 → 步骤标 partial，答案位明示「未给出结论」+ 保留开场白；
   无 sid（会话不可续）只明示不追问；
4. 实现步（require_tools=True）不适用——改完文件静默收尾是常态，不追问
   不标 partial。
"""
from __future__ import annotations

import json
from unittest import mock

from base import BaseTest


def _ev(obj):
    return json.dumps(obj, ensure_ascii=False)


def _silent_stdout(with_sid=True, final=None):
    """真实案例形态：开场白 → 两条命令 → turn.completed，无收尾消息。"""
    lines = []
    if with_sid:
        lines.append(_ev({"type": "thread.started", "thread_id": "thr-1"}))
    lines.append(_ev({"type": "turn.started"}))
    lines.append(_ev({"type": "item.completed", "item": {
        "id": "item_3", "type": "agent_message",
        "text": "我先查看项目目录中的分章文件、大纲分卷规划。"}}))
    for i, (cmd, code) in enumerate((("ls", 0), ("cat findings", 1))):
        lines.append(_ev({"type": "item.completed", "item": {
            "id": "item_%d" % (4 + i), "type": "command_execution",
            "command": cmd, "exit_code": code}}))
    if final:
        lines.append(_ev({"type": "item.completed", "item": {
            "id": "item_9", "type": "agent_message", "text": final}}))
    lines.append(_ev({"type": "turn.completed", "usage": {
        "input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 0,
        "reasoning_output_tokens": 0, "total_tokens": 15}}))
    return "\n".join(lines) + "\n"


def _nudge_stdout(text="结论：达到发布标准。全书共三卷。"):
    return "\n".join([
        _ev({"type": "thread.started", "thread_id": "thr-1"}),
        _ev({"type": "turn.started"}),
        _ev({"type": "item.completed", "item": {
            "id": "n1", "type": "agent_message", "text": text}}),
        _ev({"type": "turn.completed", "usage": {
            "input_tokens": 3, "output_tokens": 7, "cached_input_tokens": 0,
            "reasoning_output_tokens": 0, "total_tokens": 10}}),
    ]) + "\n"


def _res(stdout, exit_code=0):
    return {"ok": True, "exit_code": exit_code, "stdout": stdout,
            "stderr": "", "duration": 1.0, "cancelled": False,
            "timed_out": False, "stalled": False}


def _codex_agent():
    return {"id": "cli-x", "kind": "codex", "mode": "real", "command": "echo",
            "env": {},
            "call_chain": [{"model": "gemini-4-argon", "env": {},
                            "provider": {"id": "prov-40", "name": "gw",
                                         "protocol": "responses"},
                            "provider_id": "prov-40"}]}


class TestCodexSilentAnswer(BaseTest):
    def _run(self, results, require_tools=False):
        from app.core import runner
        calls = []

        def fake_run_process(**kwargs):
            calls.append(kwargs)
            return results[len(calls) - 1]

        with mock.patch.object(runner, "run_process", side_effect=fake_run_process):
            r = runner.run_agent(_codex_agent(), "现在11-20章达到发布标准了吗？",
                                 workdir=str(self.workdir),
                                 require_tools=require_tools)
        return r, calls

    def test_silent_qa_gets_nudged_answer(self):
        from app.core import runner
        r, calls = self._run([_res(_silent_stdout()), _res(_nudge_stdout())])
        self.assertTrue(r["ok"])
        self.assertEqual(r["text"], "结论：达到发布标准。全书共三卷。")
        self.assertTrue(r.get("nudged"))
        self.assertNotIn("partial", r)
        self.assertEqual(len(calls), 2)
        argv = calls[1]["argv"]
        self.assertIn("resume", argv)
        self.assertEqual(argv[argv.index("resume") + 1], "thr-1")
        self.assertEqual(calls[1]["stdin_text"], runner._CODEX_NUDGE_PROMPT)
        # 两段轮次 usage 合计入账
        self.assertEqual(r["usage"]["input"], 13)
        self.assertEqual(r["usage"]["output"], 12)
        self.assertEqual(r["tokens"], 25)

    def test_silent_qa_unrecovered_marks_partial(self):
        r, calls = self._run([_res(_silent_stdout()), _res(_silent_stdout())])
        self.assertTrue(r["ok"])
        self.assertTrue(r.get("partial"))
        self.assertIn("模型本轮未给出结论", r["text"])
        self.assertIn("已自动追问一次仍未恢复", r["text"])
        # 开场白保留作上下文，不许 JSONL 尾巴冒充答案
        self.assertIn("我先查看项目目录", r["text"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(r["usage"]["input"], 20)

    def test_silent_without_sid_skips_nudge(self):
        r, calls = self._run([_res(_silent_stdout(with_sid=False))])
        self.assertTrue(r["ok"])
        self.assertTrue(r.get("partial"))
        self.assertIn("未追问", r["text"])
        self.assertEqual(len(calls), 1)

    def test_impl_step_not_nudged(self):
        # 实现步改完文件静默收尾是常态：不追问、不标 partial
        r, calls = self._run([_res(_silent_stdout())], require_tools=True)
        self.assertTrue(r["ok"])
        self.assertNotIn("partial", r)
        self.assertNotIn("nudged", r)
        self.assertEqual(len(calls), 1)
        self.assertEqual(r["text"], "我先查看项目目录中的分章文件、大纲分卷规划。")

    def test_normal_answer_not_flagged(self):
        r, calls = self._run([
            _res(_silent_stdout(final="结论：达到发布标准，全书共三卷。"))])
        self.assertTrue(r["ok"])
        self.assertEqual(r["text"], "结论：达到发布标准，全书共三卷。")
        self.assertNotIn("partial", r)
        self.assertNotIn("nudged", r)
        self.assertEqual(len(calls), 1)
