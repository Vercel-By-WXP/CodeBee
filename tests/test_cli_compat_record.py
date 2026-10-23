"""兼容性验收脚本自身的契约：命中判定、脱敏、记录字段。

这个脚本的产出会被当成「某 CLI 通过验收」的证据，所以它的判定逻辑必须比
被测代码更保守：宁可记「未测」，也不能把「跑过一次任务」写成「目标 CLI 通过」。
"""
import importlib.util
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "cli_compat_record.py")


def _load():
    spec = importlib.util.spec_from_file_location("cli_compat_record", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


recorder = _load()


class ClassifyTest(unittest.TestCase):
    def test_target_cli_in_steps_and_done_is_pass(self):
        run = {"status": "done", "steps": [{"agent": "codex-cli"}]}
        out = recorder.classify_outcome(run, "codex-cli")
        self.assertEqual(out["result"], "通过")
        self.assertEqual(out["task_call"], "通过")

    def test_other_cli_executed_is_not_evidence_for_target(self):
        """路由用了别的 CLI，就不算目标 CLI 通过 —— 这条是防自欺的关键。"""
        run = {"status": "done", "steps": [{"agent": "claude-code"}, {"agent": "mock-a"}]}
        out = recorder.classify_outcome(run, "codex-cli")
        self.assertEqual(out["result"], "未命中目标 CLI")
        self.assertEqual(out["task_call"], "未测")
        self.assertIn("claude-code", out["reason"])

    def test_no_steps_is_not_pass(self):
        out = recorder.classify_outcome({"status": "done", "steps": []}, "codex-cli")
        self.assertEqual(out["task_call"], "未测")

    def test_failed_run_records_failure_with_code(self):
        run = {"status": "failed", "error": "TIMEOUT: 调用超时",
               "steps": [{"agent": "codex-cli"}]}
        out = recorder.classify_outcome(run, "codex-cli")
        self.assertEqual(out["result"], "失败")
        self.assertIn("TIMEOUT", out["reason"])

    def test_cancelled_is_never_pass(self):
        run = {"status": "cancelled", "steps": [{"agent": "codex-cli"}]}
        out = recorder.classify_outcome(run, "codex-cli")
        self.assertEqual(out["result"], "失败")


class SanitizeTest(unittest.TestCase):
    def test_home_and_user_paths_are_removed(self):
        text = recorder.sanitize("failed at " + os.path.expanduser("~") + "/proj")
        self.assertNotIn(os.path.expanduser("~"), text)
        self.assertIn("<home>", text)
        self.assertIn("<home>", recorder.sanitize(r"C:\Users\HP\proj"))
        self.assertIn("<home>", recorder.sanitize("/home/hp/proj"))

    def test_tokens_are_redacted(self):
        out = recorder.sanitize("Authorization: Bearer sk-ABCdef123456 api_key=xyz")
        self.assertNotIn("sk-ABCdef123456", out)
        self.assertNotIn("xyz", out)
        self.assertIn("<redacted>", out)


class RecordShapeTest(unittest.TestCase):
    def _meta(self, outcome):
        return {
            "captured_at": "2026-10-23 16:00:00",
            "codebee_version": "0.1.120", "codebee_commit": "abcdef1",
            "os": "win32 AMD64", "cli_id": "codex-cli",
            "cli_version": "codex-cli 0.160.0", "cli_source": r"C:\Users\HP\AppData\codex.cmd",
            "case": "创建 hello.py", "outcome": outcome, "duration_s": 42.0,
            "steps": ["codex-cli"], "verify_ran": True, "verify_pass": True,
            "review_pass": False, "evidence": "run r-1",
        }

    def test_unmeasured_columns_stay_unmeasured(self):
        rec = recorder.build_record(self._meta(
            {"result": "通过", "task_call": "通过", "reason": "ok"}))
        self.assertEqual(rec["timeout_exit"], "未测")
        self.assertEqual(rec["session_resume"], "未测")
        self.assertEqual(rec["model_auth"], "未记录")
        self.assertNotIn("HP", rec["cli_source"])

    def test_markdown_row_has_all_columns(self):
        rec = recorder.build_record(self._meta(
            {"result": "未命中目标 CLI", "task_call": "未测", "reason": "路由用了别的"}))
        row = recorder.markdown_row(rec)
        self.assertTrue(row.startswith("|"))
        self.assertEqual(row.count("|"), 9, row)
        self.assertIn("未测", row)


class CliContractTest(unittest.TestCase):
    def test_unknown_catalog_id_exits_2_without_starting_engine(self):
        out = subprocess.run([sys.executable, SCRIPT, "--cli", "no-such-cli-id"],
                             capture_output=True, text=True, timeout=60, cwd=ROOT)
        self.assertEqual(out.returncode, 2, out.stderr[:300])
        self.assertIn("no-such-cli-id", out.stdout)

    def test_uninstalled_cli_exits_3_and_records_why(self):
        out = subprocess.run([sys.executable, SCRIPT, "--cli", "openclaw"],
                             capture_output=True, text=True, timeout=120, cwd=ROOT)
        if out.returncode == 0:
            self.skipTest("本机装了 openclaw，跳过未安装分支")
        self.assertEqual(out.returncode, 3, out.stderr[:300] or out.stdout[:300])
        self.assertIn("未安装", out.stdout)


class CatalogSpecTest(unittest.TestCase):
    def test_specs_cover_the_whole_catalog_with_catalog_ids(self):
        """验收脚本必须按 catalog 全集探测，名字用 catalog ID。

        不传 specs 时服务端用 ``DEFAULT_CLIS``（5 条旧名），既漏 14 条、名字也
        对不上 catalog ID，验收记录里「未安装」和「根本没探」会混成一片。
        """
        from app.core import catalog
        specs = recorder.catalog_specs()
        ids = [e.get("id") for e in catalog.DEFAULT_CATALOG]
        self.assertEqual([s["name"] for s in specs], ids)
        for spec in specs:
            self.assertTrue(spec["executable"], "catalog 条目缺可执行入口：%s" % spec)

    def test_default_probe_set_now_comes_from_catalog(self):
        """默认探测集按目录生成；旧 5 条只是目录损坏时的兜底。

        这里钉住的是「服务端不传 specs 也覆盖全集」——脚本显式传 specs 仍是
        有意的冗余，不依赖这条。
        """
        from app.core import catalog, cli_discovery
        ids = [entry.get("id") for entry in catalog.DEFAULT_CATALOG]
        self.assertEqual([s["name"] for s in cli_discovery.default_specs()], ids)
        self.assertLess(len(cli_discovery.DEFAULT_CLIS), len(ids),
                        "旧手写集合应当只是兜底，条目数少于目录")


if __name__ == "__main__":
    unittest.main()
