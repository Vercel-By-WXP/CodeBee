# -*- coding: utf-8 -*-
"""Single-CLI continuity and Windows output decoding regressions."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from app.core import cli_text, health, pipeline, routing_strategy, runner


SINGLE_CLI_PROBE = r"""
import json, os, sys
from unittest import mock
from app.core import pipeline, store

workdir = sys.argv[1]
task = {"type": "doc", "title": "single model", "goal": "完成后审查",
        "workdir": workdir, "manuscript": "manuscript.md",
        "rubric": ["质量"], "threshold": 7.0, "rounds": 2}
agent = {"id": "claude", "mode": "real", "kind": "claude",
         "label": "Only CLI", "model": "only-model"}
calls = []
review_calls = 0
stall_draft = bool(os.environ.get("STALL_DRAFT"))

def fake_step(run_id, role, bound, prompt, wd, readonly, ev, **kwargs):
    global review_calls
    calls.append(role)
    if role == "draft":
        with open(os.path.join(workdir, "manuscript.md"), "w", encoding="utf-8") as f:
            f.write("初稿正文。\n")
        if stall_draft:
            return {"ok": False, "text": "", "error":
                    "输出停滞 600s（stall timed out，疑似卡死已提前终止）；"
                    "stderr/stdout: Reading prompt from stdin...",
                    "error_code": "TIMEOUT",
                    "raw": {"exit_code": -9, "timed_out": True, "stalled": True}}
        return {"ok": True, "text": "draft", "raw": {"exit_code": 0}}
    if role.startswith("critique"):
        if stall_draft:
            review_calls += 1
            score = 6 if review_calls == 1 else 8
            return {"ok": True,
                    "text": json.dumps({"scores": {"质量": score},
                                        "issues": [], "summary": "reviewed"},
                                       ensure_ascii=False),
                    "raw": {"exit_code": 0}}
        return {"ok": False, "text": "", "error":
                "claude 返回 is_error: API Error: 502 Sign up for free to continue and "
                "explore 500 other models. Or come back later. This is a "
                "server-side issue, usually temporary — try again in a moment.",
                "error_code": "UPSTREAM_SERVER", "raw": {"exit_code": 1}}
    if role.startswith("revise"):
        with open(os.path.join(workdir, "manuscript.md"), "a", encoding="utf-8") as f:
            f.write("修订记录。\n")
        return {"ok": True, "text": "revised", "raw": {"exit_code": 0}}
    raise AssertionError("unexpected pipeline step: " + role)

t = store.create_task(task)
run = store.create_run("orchestration", "single model", task_id=t["id"])
store.update_run(run["id"], status="running")
with mock.patch.object(pipeline, "_run_step", side_effect=fake_step), \
     mock.patch.object(pipeline.modelhub, "bind_agent", side_effect=lambda a, d="default": a), \
     mock.patch.object(pipeline.router, "pick", return_value=(agent, "only CLI")), \
     mock.patch.object(pipeline.router, "pick_critics", return_value=([agent], "only CLI")), \
     mock.patch.object(pipeline.router, "pick_reviewer", return_value=(None, "no alternate CLI")), \
     mock.patch.object(pipeline.router, "pick_switch_candidate", return_value=(None, "no alternate CLI")), \
     mock.patch.object(pipeline.planner, "make_novel_plan", return_value={"steps": []}), \
     mock.patch("app.core.bench.benched_ids", return_value=set()), \
     mock.patch.object(pipeline.time, "sleep", lambda *_a, **_k: None):
    pipeline._run_content_review({"id": run["id"]}, task, [agent], None, {}, "auto")
r = store.get_run(run["id"])
v = r.get("verdict") or {}
print("@@RESULT@@" + json.dumps({
    "status": r.get("status"), "calls": calls,
    "review_degraded": v.get("review_degraded"),
    "draft_degraded": v.get("draft_degraded"),
    "publishable": v.get("publishable"),
    "rounds_used": v.get("rounds_used"),
    "error": r.get("error") or "",
    "overall": v.get("overall"),
    "scores": v.get("scores"),
    "report": (store.run_dir(run["id"]) / "report.md").read_text(encoding="utf-8"),
}, ensure_ascii=True))
"""


class CliOutputRecoveryTests(unittest.TestCase):
    def test_mixed_code_page_bytes_preserve_valid_utf8(self):
        info = cli_text.decode_output_info(
            bytes([0xE4, 0xB8, 0xAD, 0xFF, 0xE6, 0x96, 0x87]))
        self.assertEqual(info["text"], "中ÿ文")
        self.assertTrue(info["decode_degraded"])

    def test_run_process_exposes_local_decode_metadata(self):
        result = runner.run_process(
            argv=[sys.executable, "-c",
                  "import sys; sys.stdout.buffer.write(bytes([214,208]))"],
            timeout=10)
        self.assertTrue(result["ok"])
        self.assertEqual(result["stdout"], "中")
        self.assertTrue(result["decode_degraded"])
        self.assertTrue(result["local_output_error"])
        self.assertIn("gb", result["stdout_encoding"])

    def test_run_agent_classifies_decode_error_as_local_state(self):
        agent = {"id": "generic", "kind": "generic", "mode": "real",
                 "command": "generic", "model": "m1"}
        process_result = {
            "ok": False, "exit_code": 1, "stdout": "recoverable text",
            "stderr": "", "duration": 0.1, "cancelled": False,
            "timed_out": False, "stalled": False,
            "deadline_exceeded": False, "output_truncated": False,
            "decode_degraded": True, "local_output_error": True,
        }
        with mock.patch.object(runner, "_build_call",
                               return_value=(["generic"], "p", "p", [])), \
                mock.patch.object(runner, "run_process", return_value=process_result):
            result = runner.run_agent(
                agent, "prompt", timeout=2,
                command_context={"run_id": "r-decode", "role": "draft"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "LOCAL_STATE")
        self.assertTrue(routing_strategy.BuiltinStrategy().should_fallback(result))

    def test_truncated_output_is_fallback_eligible(self):
        self.assertTrue(routing_strategy.BuiltinStrategy().should_fallback({
            "error_code": "PARSE_FAIL",
            "raw": {"output_truncated": True},
        }))

    def test_windows_shell_path_is_added_once_when_available(self):
        root = r"C:\Windows"
        powershell = os.path.join(
            root, "System32", "WindowsPowerShell", "v1.0")
        env = {"SystemRoot": root, "Path": r"C:\Windows\System32;C:\Tools"}
        with mock.patch.object(runner.os.path, "isdir", return_value=True):
            result = runner._augment_windows_shell_path(env, is_windows=True)
        self.assertEqual(result["Path"].split(os.pathsep).count(powershell), 1)
        self.assertEqual(env["Path"], r"C:\Windows\System32;C:\Tools")

    def test_windows_shell_path_deduplicates_case_and_slash_variants(self):
        root = r"C:\Windows"
        powershell = os.path.join(
            root, "System32", "WindowsPowerShell", "v1.0")
        env = {"SystemRoot": root,
               "PATH": r"C:\WINDOWS\system32\windowspowershell\v1.0"}
        with mock.patch.object(runner.os.path, "isdir", return_value=True):
            result = runner._augment_windows_shell_path(env, is_windows=True)
        self.assertEqual(result["PATH"], env["PATH"])


class TrustedFailureEvidenceTests(unittest.TestCase):
    def test_model_prose_quota_word_does_not_trip_quota_classification(self):
        result = runner._classify_failure({
            "ok": False, "exit_code": 1, "stderr": "",
            "stdout": "困难补助额度开春作废。"
        })
        self.assertEqual(result, runner.ErrorCode.VENDOR_ERROR)

    def test_quota_word_in_non_error_cli_output_is_not_diagnostic_evidence(self):
        self.assertEqual(runner._cli_error_lines(
            "正文提到 quota、额度不足和 HTTP 502，但这是小说台词。"), [])

    def test_explicit_stdout_api_error_remains_diagnostic_evidence(self):
        result = runner._classify_failure({
            "ok": False, "exit_code": 1, "stderr": "",
            "stdout": "Error: API Error: 502 bad gateway"
        })
        self.assertEqual(result, runner.ErrorCode.UPSTREAM_SERVER)

    def test_structured_claude_error_is_still_classified(self):
        result = runner._classify_failure(
            {"ok": False, "exit_code": 1, "stderr": "", "stdout": ""},
            parsed={"is_error": True,
                    "text": "API Error: 502 bad gateway"}, kind="claude")
        self.assertEqual(result, runner.ErrorCode.UPSTREAM_SERVER)

    def test_qwen_api_quota_error_inside_startup_warning_is_classified(self):
        result = runner._classify_failure({
            "ok": False, "exit_code": 1, "stderr": "",
            "stdout": ("Warning: MCP server(s) failed to start: git, sqlite. "
                       "Continuing with built-in tools. "
                       "[API Error: 403 用户额度不足, 剩余额度: ¥0.000000]")
        })
        self.assertEqual(result, runner.ErrorCode.QUOTA)

    def test_codex_completed_error_item_is_extracted(self):
        event = {
            "type": "item.completed",
            "item": {"type": "error", "message":
                     "API Error: 403 用户额度不足, 剩余额度: ¥0.000000"},
        }
        result = runner._codex_fail_msg(json.dumps(event, ensure_ascii=False))
        self.assertIn("用户额度不足", result)

    def test_codex_config_warning_is_not_treated_as_failure_reason(self):
        """config.toml 旧键告警是启动噪音，不能顶替真实失败原因。

        2026-10-23 实案：用户只看到 "disable_response_storage is ignored"，
        真正的退出原因被这条 error item 覆盖，分类与换 CLI 都跟着走偏。
        """
        noise = json.dumps({
            "type": "item.completed",
            "item": {"type": "error", "message":
                     "Codex is ignoring 6 unrecognized configuration settings.\n"
                     "  user (C:\\Users\\HP\\.codex\\config.toml): "
                     "`disable_response_storage` is ignored."},
        }, ensure_ascii=False)
        self.assertEqual(runner._codex_fail_msg(noise), "")

        quota = json.dumps({
            "type": "item.completed",
            "item": {"type": "error", "message":
                     "API Error: 403 用户额度不足, 剩余额度: ¥0.000000"},
        }, ensure_ascii=False)
        self.assertIn("用户额度不足",
                      runner._codex_fail_msg(quota + "\n" + noise))
        self.assertIn("用户额度不足",
                      runner._codex_fail_msg(noise + "\n" + quota))

        # 自建 provider 的模型不在 codex 内置元数据表里也会播报一条 error
        # item（"Model metadata for `x` not found. Defaulting to fallback"）。
        # 它是性能提示，不是失败原因；当失败处理会把整步判死。
        meta = json.dumps({
            "type": "item.completed",
            "item": {"type": "error", "message":
                     "codex: Model metadata for `u2-flash` not found. "
                     "Defaulting to fallback metadata; this can degrade "
                     "performance and cause unexpected behavior."},
        }, ensure_ascii=False)
        self.assertEqual(runner._codex_fail_msg(meta), "")

    def test_codex_error_event_with_nested_message_is_extracted(self):
        event = {"type": "error", "error": {"message": "API Error: 502 bad gateway"}}
        self.assertIn("502", runner._codex_fail_msg(json.dumps(event)))

    def test_codex_completed_error_item_classifies_full_run_failure(self):
        stdout = json.dumps({
            "type": "item.completed",
            "item": {"type": "error", "message":
                     "API Error: 403 用户额度不足, 剩余额度: ¥0.000000"},
        }, ensure_ascii=False)
        with mock.patch.object(runner, "_build_call",
                               return_value=(["codex"], "p", "p", [])), \
             mock.patch.object(runner, "run_process", return_value={
                 "ok": False, "exit_code": 1, "stdout": stdout,
                 "stderr": "Reading prompt from stdin...", "duration": 1.0,
                 "cancelled": False, "timed_out": False, "stalled": False,
                 "deadline_exceeded": False, "output_truncated": False,
                 "abort_marker": None,
             }):
            result = runner.run_agent(
                {"id": "codex", "kind": "codex", "mode": "real",
                 "command": "codex", "model": "m1"}, "prompt", timeout=2,
                command_context={"run_id": "r-codex-quota", "role": "draft"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "QUOTA")


    def test_gbk_windows_command_error_is_local_tool_launch(self):
        message = "'powershell.exe' 不是内部或外部命令，也不是可运行的程序或批处理文件。"
        self.assertEqual(runner._classify_failure({
            "ok": False, "exit_code": 1, "stderr": message,
            "stdout": "小说里写到额度开春作废。",
        }), runner.ErrorCode.TOOL_LAUNCH)


class SingleCliReviewFallbackTests(unittest.TestCase):
    def test_unknown_vendor_exit_tries_one_configured_cli(self):
        primary = {"id": "primary", "mode": "real"}
        backup = {"id": "backup", "mode": "real"}
        failed = {"ok": False, "error": "退出码 1", "error_code": "VENDOR_ERROR",
                  "raw": {"exit_code": 1}}
        success = {"ok": True, "text": "draft", "raw": {"exit_code": 0}}
        with mock.patch.object(pipeline, "_run_step", return_value=success) as run_step, \
             mock.patch.object(pipeline.modelhub, "bind_agent", side_effect=lambda a, _d: a), \
             mock.patch.object(pipeline.router, "agent_upstreams", return_value=set()), \
             mock.patch.object(pipeline.router, "pick_switch_candidate",
                               return_value=(backup, "fallback")):
            result, actual, _note = pipeline._retry_content_draft_with_cli(
                "run", {"type": "doc"}, [primary, backup], primary, "default",
                "auto", {}, "prompt", "workdir", "step", None, None, "", failed)
        self.assertTrue(result["ok"])
        self.assertIs(actual, backup)
        self.assertEqual(run_step.call_count, 1)

    def test_upstream_502_gets_conservative_local_review(self):
        agent = {"id": "claude", "mode": "real", "kind": "claude"}
        failed = {
            "ok": False,
            "text": "",
            "error": "claude 返回 is_error: API Error: 502 bad gateway",
            "error_code": "UPSTREAM_SERVER",
            "raw": {"exit_code": 1},
        }
        with mock.patch.object(pipeline, "_run_step", return_value=failed), \
                mock.patch.object(pipeline.router, "pick_reviewer",
                                  return_value=(None, "无其他候选")), \
                mock.patch.object(pipeline.router, "pick_switch_candidate",
                                  return_value=(None, "无其他候选")), \
                mock.patch.object(pipeline.time, "sleep", lambda *_a, **_k: None), \
                mock.patch("app.core.bench.benched_ids", return_value=set()):
            cj, result, actual, note = pipeline._critique_step_with_cli_fallback(
                "run", "critique-r1", agent, "prompt", "workdir", None,
                [agent], None, "default", "novel", {}, "auto", False,
                dims=["质量"])

        self.assertEqual(actual["id"], "claude")
        self.assertEqual(cj["scores"], {})
        self.assertTrue(cj["review_degraded"])
        self.assertIn("评审未完成", cj["summary"])
        self.assertEqual(result["review_fallback"], "local")
        self.assertIn("评审本地兜底", note)

    def test_stalled_review_uses_local_fallback_after_one_attempt(self):
        agent = {"id": "claude", "mode": "real", "kind": "claude"}
        failed = {
            "ok": False, "text": "",
            "error": "输出停滞 600s（stall timed out，疑似卡死已提前终止）",
            "error_code": "TIMEOUT",
            "raw": {"exit_code": -9, "timed_out": True, "stalled": True},
        }
        calls = []

        def run_step(*_args, **_kwargs):
            calls.append(1)
            return failed

        with mock.patch.object(pipeline, "_run_step", side_effect=run_step), \
             mock.patch.object(pipeline.router, "pick_reviewer",
                               return_value=(None, "无其他候选")), \
             mock.patch.object(pipeline.router, "pick_switch_candidate",
                               return_value=(None, "无其他候选")), \
             mock.patch.object(pipeline.time, "sleep", lambda *_a, **_k: None), \
             mock.patch("app.core.bench.benched_ids", return_value=set()):
            cj, result, _actual, _note = pipeline._critique_step_with_cli_fallback(
                "run", "critique-r1", agent, "prompt", "workdir", None,
                [agent], None, "default", "novel", {}, "auto", False,
                dims=["质量"])

        self.assertEqual(calls, [1])
        self.assertEqual(result["review_fallback"], "local")
        self.assertTrue(cj["review_degraded"])

    def test_decode_only_failure_does_not_poison_health(self):
        with tempfile.TemporaryDirectory() as data_dir:
            health.init(data_dir, start_probe=False)
            health.report_failure(
                "OpenCode 原生免费模型",
                "本地 CLI 输出编码已降级（保留可读文本）；退出码 1",
                model="m1", provider_id="p1", error_code="LOCAL_STATE")
            self.assertEqual(health.snapshot()["providers"], [])

    def test_single_cli_502_run_reaches_revision_and_terminal_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = os.path.join(tmp, "workspace")
            os.makedirs(workdir)
            env = dict(os.environ)
            env["TUTTI_DATA"] = os.path.join(tmp, "data")
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.run(
                [sys.executable, "-c", SINGLE_CLI_PROBE, workdir],
                cwd=os.path.dirname(os.path.dirname(__file__)), env=env,
                timeout=120, capture_output=True, text=True)
            if proc.returncode != 0:
                raise AssertionError("probe failed: %s" % proc.stderr[-2000:])
            result_line = [line for line in proc.stdout.splitlines()
                           if line.startswith("@@RESULT@@")][-1]
            result = json.loads(result_line[len("@@RESULT@@"):])

        # 2026-10-23 起：评审 CLI 全灭不再停摆在"评审未完成"。稿件在，就由
        # 本地确定性算法真实评审一遍；不过线就进修订循环，再不过线就按真实
        # 分数收口——探针的稿件只有 12 字，本地兜底按"信息量不足"一票否决给
        # 3.0，这是质量判定，不是停摆。用户拿到的是带分数、带原因、带修订
        # 记录的完整报告，而不是一句"评审未完成"。
        self.assertEqual(result["status"], "failed", result)
        self.assertFalse(result["draft_degraded"], result)
        self.assertFalse(result["publishable"], result)
        self.assertEqual(result["overall"], 3.0, result)
        self.assertEqual(result["scores"], {"质量": 3.0}, result)
        self.assertEqual(result["rounds_used"], 2, result)
        # 修订循环真的跑起来了（不是评审失败就躺平）
        self.assertIn("draft", result["calls"], result)
        self.assertIn("revise-r1", result["calls"], result)
        self.assertIn("critique-r2", result["calls"], result)
        self.assertIn("评审本地兜底", result["report"], result)
        self.assertIn("确定性算法", result["report"], result)
        self.assertIn("评审来源", result["report"], result)

    def test_stalled_draft_with_saved_text_continues_review_and_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = os.path.join(tmp, "workspace")
            os.makedirs(workdir)
            env = dict(os.environ)
            env["TUTTI_DATA"] = os.path.join(tmp, "data")
            env["PYTHONIOENCODING"] = "utf-8"
            env["STALL_DRAFT"] = "1"
            proc = subprocess.run(
                [sys.executable, "-c", SINGLE_CLI_PROBE, workdir],
                cwd=os.path.dirname(os.path.dirname(__file__)), env=env,
                timeout=120, capture_output=True, text=True)
            if proc.returncode != 0:
                raise AssertionError("probe failed: %s" % proc.stderr[-2000:])
            result_line = [line for line in proc.stdout.splitlines()
                           if line.startswith("@@RESULT@@")][-1]
            result = json.loads(result_line[len("@@RESULT@@"):])

        self.assertEqual(result["status"], "failed", result)
        self.assertTrue(result["draft_degraded"], result)
        self.assertFalse(result["publishable"], result)
        self.assertEqual(result["rounds_used"], 2, result)
        self.assertIn("revise-r1", result["calls"], result)
        self.assertIn("critique-r2", result["calls"], result)
        self.assertEqual(result["overall"], 8.0, result)
        self.assertIn("起草中断", result["report"], result)
        self.assertIn("Reading prompt from stdin", result["report"], result)


if __name__ == "__main__":
    unittest.main()
