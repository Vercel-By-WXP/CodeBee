# -*- coding: utf-8 -*-
"""Regression for timeout/event noise and stderr hidden by tool output."""
import json
from unittest import mock

from base import BaseTest
from app.core import runner
from app.core.error_codes import ErrorCode


class TestFailureDiagnostics(BaseTest):
    def _result(self, **changes):
        result = {"ok": False, "exit_code": 1, "stdout": "", "stderr": "",
                  "duration": 320, "cancelled": False, "timed_out": False,
                  "stalled": False}
        result.update(changes)
        return result

    def _run(self, result):
        agent = {"id": "opencode", "kind": "opencode", "mode": "real",
                 "command": "opencode", "model": "m1", "env": {}}
        with mock.patch.object(runner, "run_process", return_value=result):
            return runner.run_agent(agent, "test", workdir=str(self.workdir))

    def test_stderr_error_survives_long_stdout(self):
        result = self._result(stderr="Error: database is locked",
                              stdout="href=style.css\n" * 1000)
        out = self._run(result)
        self.assertIn("database is locked", out["error"])
        self.assertEqual(out["error_code"], ErrorCode.LOCAL_STATE)

    def test_timeout_head_survives_large_event_and_log(self):
        event = json.dumps({"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "quota 429 " + "x" * 1000}]}})
        log = self.workdir / "step.log"
        log.write_text(event, encoding="utf-8")
        out = {"error": "超时；stderr/stdout: " + "x" * 600}
        runner._augment_error_from_log(out, str(log))
        self.assertTrue(out["error"].startswith("超时"))
        self.assertNotIn("日志错误行", out["error"])

    def test_timeout_and_cancel_flags_precede_upstream_words(self):
        for flag, expected in (("timed_out", ErrorCode.TIMEOUT),
                               ("cancelled", ErrorCode.CANCELLED)):
            with self.subTest(flag=flag):
                out = self._run(self._result(**{flag: True}, stderr="Error: HTTP 403"))
                self.assertEqual(out["error_code"], expected)
                self.assertTrue(out["error"].startswith("超时" if flag == "timed_out" else "取消"))

    def test_event_uuid_is_not_http_status(self):
        event = json.dumps({"type": "assistant", "message": {
            "id": "msg_20261014152551ed54d63a7e7f4150",
            "content": [{"type": "thinking", "thinking": "normal"}]}})
        self.assertEqual(runner._cli_error_lines(event), [])
        self.assertEqual(runner._cli_error_lines("uuid=abcdef403012345"), [])

    def test_real_structured_errors_are_extracted(self):
        for event in ({"type": "error", "error": {"message": "HTTP 429"}},
                      {"type": "turn.failed", "error": {"message": "API offline"}},
                      {"type": "result", "is_error": True, "result": "model unavailable"},
                      {"type": "system", "subtype": "api_retry", "error": "HTTP 503"}):
            with self.subTest(event=event):
                self.assertTrue(runner._cli_error_lines(json.dumps(event)))

    def test_tool_output_and_partial_json_not_error_events(self):
        event = {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "HTTP 403 source code"}]}}
        self.assertEqual(runner._cli_error_lines(json.dumps(event)), [])
        self.assertEqual(runner._cli_error_lines('{"type":"assistant","id":"429'), [])

    def test_source_line_429_and_echoed_command_are_not_errors(self):
        text = ('Error: ripgrep execution failed\n'
                '429:    url = SOURCES_BY_ID["clawhub"]["urls"][0]\n'
                '$ grep -n "quota|403|forbidden" app/core/runner.py\n')
        detail = runner._failure_output_detail(self._result(stdout=text))
        self.assertEqual(detail, "Error: ripgrep execution failed")

    def test_log_probe_keeps_head_and_ignores_audit_prompt(self):
        log = self.workdir / "step.log"
        log.write_text("--- 指令 ---\nHTTP 403 forbidden example\n--- 输出 ---\nall good\n",
                       encoding="utf-8")
        out = {"error": "退出码 1；" + "x" * 600}
        original = out["error"]
        runner._augment_error_from_log(out, str(log))
        self.assertEqual(out["error"], original)
        log.write_text("Error: request rejected (429)\n", encoding="utf-8")
        runner._augment_error_from_log(out, str(log))
        self.assertTrue(out["error"].startswith("退出码 1"))
        self.assertIn("429", out["error"])

    def test_timeout_context_uses_readable_messages(self):
        events = "\n".join(json.dumps(ev, ensure_ascii=False) for ev in (
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "正在扫描发行渠道"}]}},
            {"type": "tool_progress", "uuid": "event-uuid"},
            {"type": "system", "subtype": "task_started", "description": "发行扫描"}))
        detail = runner._failure_output_detail(self._result(
            stdout=events, stderr="[超时 1200s，已终止进程树]"))
        self.assertIn("超时 1200s", detail)
        self.assertIn("正在扫描发行渠道", detail)
        self.assertNotIn("event-uuid", detail)
