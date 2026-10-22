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
        chain = [{"model": "mimo/mimo-auto",
                  "provider": {"id": "p1", "name": "P1"}, "provider_id": "p1"}]
        result, rep = self._run([_res(stderr=MIMO_SERVICE_ENDED)], chain)
        self.assertFalse(result["ok"])
        self.assertIn("free API service has ended", result["error"])
        self.assertEqual(result["error_code"], ErrorCode.MODEL_UNAVAILABLE.value)
        self.assertEqual(rep.call_count, 1)


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
