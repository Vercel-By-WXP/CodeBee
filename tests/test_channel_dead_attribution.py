# -*- coding: utf-8 -*-
"""通道停摆/模型级死错三连回归（2026-10-22 短故事起草三截图实案）。

· claude「the selected model (glm-5.3). It may not exist or you may not have
  access」：模型级死错（MODEL_UNAVAILABLE + 点名模型提取），不再漏分类成
  供应商级失败连坐整条链；
· mimo「MiMo free API service has ended」exit 0 假成功：runner 拦截停摆
  横幅判失败并走换将，不再把横幅当成果传给字数闸白烧扩写轮；
· gemini-cli 0.62+ settings schema：model 必须是对象（{name: ...}），目录
  条目走 json-path 写 model.name，旧版裸字符串就地迁移——否则 CLI 连
  --version 都退出码 41 拒起。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import catalog, health, manager, runner
from app.core.error_codes import (
    ErrorCode, classify_error_text, model_name_from_error,
)

CLAUDE_MODEL_NOT_FOUND = (
    "claude 返回 is_error: There's an issue with the selected model (glm-5.3). "
    "It may not exist or you may not have access to it. Run /model to pick a "
    "different model.")

MIMO_SERVICE_ENDED = (
    "error: MiMo free API service has ended. "
    "Sign in or configure a third-party API.")

# aider/litellm 打满重试梯后 exit 0 收场的报错墙（2026-10-24 每日巡检实案：
# 本机 python 解析链分钟级抖动，九连 10061 全被记成 done）
LITELLM_10061_LINE = (
    "litellm.InternalServerError: AnthropicException - [WinError 10061] ")

AIDER_LITELLM_WALL = (
    "Aider v0.86.2\n"
    "Model: anthropic/claude-opus-5-5 with whole edit format\n"
    "Git repo: none\n"
    "Repo-map: disabled\n"
    "Added tutti_prompt_mjdlnfsq.md to the chat (read-only).\n"
    "\n"
    + (LITELLM_10061_LINE + "\n"
       "由于目标计算机积极拒绝，无法连接。. Handle with "
       "`litellm.InternalServerError`.\n"
       "The API provider's servers are down or overloaded.\n"
       "Retrying in 0.2 seconds...\n") * 2)

CODEX_RECONNECT = (
    "Reconnecting... waiting for network "
    "(Connection failed: error sending request)")


def _res(**over):
    base = {"ok": True, "exit_code": 0, "stdout": "", "stderr": "",
            "duration": 8.0, "cancelled": False, "timed_out": False,
            "stalled": False, "deadline_exceeded": False,
            "output_truncated": False, "abort_marker": None}
    base.update(over)
    return base


class ClassifierTests(unittest.TestCase):
    def test_claude_selected_model_wording_is_model_scoped(self):
        self.assertEqual(classify_error_text(CLAUDE_MODEL_NOT_FOUND),
                         ErrorCode.MODEL_UNAVAILABLE)

    def test_claude_wording_names_the_model(self):
        self.assertEqual(model_name_from_error(CLAUDE_MODEL_NOT_FOUND),
                         "glm-5.3")

    def test_mimo_dead_service_banner_is_model_unavailable(self):
        self.assertEqual(classify_error_text(MIMO_SERVICE_ENDED),
                         ErrorCode.MODEL_UNAVAILABLE)

    def test_winerror_10061_maps_to_network(self):
        # 旧签名表一个都不命中：连 9 次全 None → 退出码兜底 fatal VENDOR_ERROR
        # → 换将闸当终态收工。必须归可重试的 NETWORK。
        self.assertEqual(classify_error_text(
            LITELLM_10061_LINE + "\n由于目标计算机积极拒绝，无法连接。"),
            ErrorCode.NETWORK)

    def test_codex_reconnect_banner_maps_to_network(self):
        # reqwest 裸短形态（不带 "for url" 后缀）也要命中，不能 fatal 收场
        self.assertEqual(classify_error_text(CODEX_RECONNECT),
                         ErrorCode.NETWORK)


class GenericDeadBannerTests(unittest.TestCase):
    def test_banner_in_stderr_is_caught(self):
        line = runner._generic_dead_banner(_res(stderr=MIMO_SERVICE_ENDED))
        self.assertIsNotNone(line)
        self.assertIn("free API service has ended", line)

    def test_banner_in_stdout_transcript_is_caught(self):
        stdout = "> build · mimo-auto\n" + MIMO_SERVICE_ENDED + "\n[检查点] 尾行"
        self.assertIsNotNone(runner._generic_dead_banner(_res(stdout=stdout)))

    def test_healthy_run_has_no_banner(self):
        self.assertIsNone(runner._generic_dead_banner(
            _res(stdout="正文写完了，共 9123 字。")))

    def test_failed_or_cancelled_results_skip_banner_check(self):
        self.assertIsNone(runner._generic_dead_banner(
            _res(ok=False, exit_code=1, stderr=MIMO_SERVICE_ENDED)))
        self.assertIsNone(runner._generic_dead_banner(
            _res(cancelled=True, stderr=MIMO_SERVICE_ENDED)))


class PureNoticeResultTests(unittest.TestCase):
    """整段答复只是模型下线提示/拒答＝假成功（2026-10-24 头七实案）。"""

    def test_model_deprecation_notice_is_model_unavailable(self):
        code, _ = runner._result_is_pure_notice(
            "Claude Sonnet 4.6 is no longer available. "
            "Please switch to Claude Sonnet 5.5.")
        self.assertEqual(code, ErrorCode.MODEL_UNAVAILABLE)

    def test_safety_refusal_is_vendor_refusal(self):
        code, _ = runner._result_is_pure_notice(
            "**I'm sorry, but I must decline this request.** If you'd like "
            "help with a version that complies with the guidelines, "
            "I'm happy to help — let me know how else I can support you!")
        self.assertEqual(code, ErrorCode.VENDOR_REFUSAL)

    def test_real_delivery_is_untouched(self):
        review = ("评审意见：本章开场钩子明确，情节推进节奏良好，"
                  "建议第二幕增加冲突升级。" * 5)
        self.assertEqual(runner._result_is_pure_notice(review), ("", ""))

    def test_long_text_with_marker_words_is_untouched(self):
        # 正文里顺带提到 refuse/decline 不会误杀：长度闸顶住
        long_text = ("章节正文持续输出。" * 60
                     + " 有一处角色对话提到 I must decline the offer。")
        self.assertEqual(runner._result_is_pure_notice(long_text), ("", ""))


class GenericFatalTranscriptTests(unittest.TestCase):
    def test_litellm_retry_wall_is_detected(self):
        line = runner._generic_fatal_transcript(AIDER_LITELLM_WALL)
        self.assertIsNotNone(line)
        self.assertIn("litellm.InternalServerError", line)

    def test_single_transient_error_is_not_fatal(self):
        # 偶发一次报错后继续出正文：不是报错墙
        text = ("Aider v0.86.2\n"
                + LITELLM_10061_LINE + "\nRetrying in 0.2 seconds...\n"
                + "评审完成，以下为意见：" + "正文内容持续输出。" * 12)
        self.assertIsNone(runner._generic_fatal_transcript(text))

    def test_substantive_answer_beats_error_wall(self):
        # ≥2 行报错但后面跟着 ≥80 字符的实质正文行：回显污染闸顶住，不判死
        answer = ("本章评审意见如下：情节推进节奏良好，人物弧线完整，"
                  "建议在第二幕增加一次冲突升级以强化张力，结尾悬念收束自然，"
                  "对话语言可再口语化以贴近人设。" * 2)
        self.assertGreaterEqual(len(answer), 80)
        text = AIDER_LITELLM_WALL + "\n" + answer
        self.assertIsNone(runner._generic_fatal_transcript(text))

    def test_healthy_transcript_is_untouched(self):
        self.assertIsNone(runner._generic_fatal_transcript(
            "正文写完了，共 9123 字。"))


class RunnerChainWalkTests(unittest.TestCase):
    def setUp(self):
        with runner._DSH_LAUNCH_LOCK:
            runner._DSH_LAUNCH_HISTORY.clear()

    def tearDown(self):
        with runner._DSH_LAUNCH_LOCK:
            runner._DSH_LAUNCH_HISTORY.clear()

    def _run(self, results, chain):
        with mock.patch.object(runner, "_build_call",
                               return_value=(["dsh"], "prompt", "prompt", [])), \
             mock.patch.object(runner, "run_process",
                               side_effect=results), \
             mock.patch.object(health, "report_failure") as rep:
            agent = {"id": "dsh", "kind": "generic", "mode": "real",
                     "command": "dsh", "call_chain": chain,
                     "orch": {"timeout_ms": 1000}}
            result = runner.run_agent(
                agent, "prompt", timeout=5,
                command_context={"run_id": "run-dead", "role": "draft"},
                checkpoint_run_id="run-dead")
        return result, rep

    def test_exit0_dead_banner_fails_step_and_walks_chain(self):
        chain = [
            {"model": "mimo/mimo-auto", "provider": {"id": "p1", "name": "P1"},
             "provider_id": "p1"},
            {"model": "m2", "provider": {"id": "p2", "name": "P2"},
             "provider_id": "p2"},
        ]
        result, rep = self._run(
            [_res(stdout="> build · mimo-auto\n" + MIMO_SERVICE_ENDED),
             _res(stdout="扩写完成，全文约 9300 字")], chain)
        self.assertTrue(result["ok"])
        # 假成功被拦成模型级死错并上报健康台账
        self.assertEqual(rep.call_count, 1)
        self.assertEqual(rep.call_args.kwargs.get("error_code"),
                         ErrorCode.MODEL_UNAVAILABLE)

    def test_exit0_dead_banner_at_chain_tail_fails_honestly(self):
        # 横幅放 stdout：并行引入的只读空输出闸会在 stdout 为空时先接管，
        # stderr-only 横幅走不到停摆检测；本用例验的是链尾诚实失败
        chain = [{"model": "mimo/mimo-auto",
                  "provider": {"id": "p1", "name": "P1"}, "provider_id": "p1"}]
        result, rep = self._run(
            [_res(stdout="> build · mimo-auto\n" + MIMO_SERVICE_ENDED)], chain)
        self.assertFalse(result["ok"])
        self.assertIn("free API service has ended", result["error"])
        self.assertEqual(result["error_code"], ErrorCode.MODEL_UNAVAILABLE.value)
        self.assertEqual(rep.call_count, 1)

    def test_exit0_litellm_wall_fails_step_and_walks_chain(self):
        """aider 九连 10061 后 exit 0 假完成：转录全是报错墙必须判失败并
        换将（2026-10-24 实案：三席全被记成 done，评审人数不足整轮判败）。
        NETWORK 类失败的健康记账在 pipeline 层（run_agent 内只有
        MODEL_UNAVAILABLE 分支直接上报），这里只验换将行为。"""
        chain = [
            {"model": "claude-opus-5-5", "provider": {"id": "p1", "name": "P1"},
             "provider_id": "p1"},
            {"model": "m2", "provider": {"id": "p2", "name": "P2"},
             "provider_id": "p2"},
        ]
        result, _rep = self._run(
            [_res(stdout=AIDER_LITELLM_WALL),
             _res(stdout="评审完成：均分 7.4，意见 6 条。")], chain)
        self.assertTrue(result["ok"])
        self.assertIn("7.4", result["text"])

    def test_exit0_litellm_wall_at_chain_tail_fails_with_network(self):
        chain = [{"model": "claude-opus-5-5",
                  "provider": {"id": "p1", "name": "P1"}, "provider_id": "p1"}]
        result, _rep = self._run([_res(stdout=AIDER_LITELLM_WALL)], chain)
        self.assertFalse(result["ok"])
        self.assertIn("假完成", result["error"])
        # 兜底必须是可换将的 NETWORK，不能是 fatal 的 VENDOR_ERROR
        self.assertEqual(result["error_code"], ErrorCode.NETWORK.value)


class GeminiConfigSchemaTests(unittest.TestCase):
    """gemini-cli 0.62+ 把 settings.model 定义为对象（model.name 才是模型名）。"""

    def test_catalog_entry_uses_json_path_model_name(self):
        entry = catalog.by_id("gemini-cli")
        self.assertIsNotNone(entry)
        cfg = entry.get("config") or {}
        self.assertEqual(cfg.get("format"), "json-path")
        self.assertEqual(cfg.get("model_key"), "model.name")

    def test_write_model_migrates_legacy_string_to_object(self):
        # 假 HOME 落在临时目录：防毒闸（TUTTI_DATA 在临时目录且 HOME 为真）
        # 不触发，写入只落在夹具里
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            gdir = fake_home / ".gemini"
            gdir.mkdir()
            (gdir / "settings.json").write_text(json.dumps({
                "security": {"auth": {"selectedType": "gemini-api-key"}},
                "model": "gpt-6.1-sol",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            entry = dict(catalog.by_id("gemini-cli"))
            entry["config"] = dict(entry["config"])
            entry["config"]["path"] = str(gdir / "settings.json")
            with mock.patch.object(manager, "_canonical", lambda p: os.path.normcase(
                    os.path.realpath(os.path.abspath(
                        fake_home if str(p) == "~" else os.path.expanduser(p))))):
                res = manager.write_model(entry, "gemini-3-flash-preview")
            self.assertTrue(res.get("ok"), res)
            data = json.loads((gdir / "settings.json").read_text(encoding="utf-8"))
            # 旧版裸字符串就地升级为对象，其余键原样保留
            self.assertEqual(data.get("model"),
                             {"name": "gemini-3-flash-preview"})
            self.assertEqual(
                data["security"]["auth"]["selectedType"], "gemini-api-key")
            self.assertEqual(manager.read_model(entry),
                             "gemini-3-flash-preview")


if __name__ == "__main__":
    unittest.main()
