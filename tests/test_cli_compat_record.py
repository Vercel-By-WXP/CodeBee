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


class RecordLocationTest(unittest.TestCase):
    """记录必须能入库，且入得了库的内容不能带隐私。

    记录本身就是「某 CLI 验收过」的证据。放在被 .gitignore 排除的 docs/ 下，
    等于只有跑过的那台机器看得到，复核的人拿不到 —— 这和「没记录」差别不大。
    """

    def test_default_out_dir_is_not_gitignored(self):
        default = "compat-records"
        # git check-ignore: 退出码 1 表示未被忽略
        proc = subprocess.run(["git", "check-ignore", "-q", default], cwd=ROOT)
        self.assertEqual(proc.returncode, 1,
                         "默认记录目录被 .gitignore 排除，记录入不了库：%s" % default)

    def test_records_exist_and_stay_public_safe(self):
        folder = os.path.join(ROOT, "compat-records")
        files = [f for f in os.listdir(folder) if f.endswith(".json")]
        self.assertTrue(files, "compat-records/ 里应有已产出的记录")
        for name in files:
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotRegex(text, r"(?i)[A-Z]:[\\/]+Users[\\/]",
                                "%s 含本机用户目录" % name)
            self.assertNotRegex(text, r"\bsk-[A-Za-z0-9_\-]{8,}", "%s 含令牌" % name)
            # URL 主机必须全是公共站点；私有网关只能以 <endpoint> 出现。
            for match in recorder._URL_HOST.finditer(text):
                host = match.group(2).lower()
                if host.startswith("<endpoint>"):
                    continue
                allowed = (host in recorder._PUBLIC_URL_HOSTS
                           or host.endswith(tuple("." + p
                                                  for p in recorder._PUBLIC_URL_HOSTS)))
                self.assertTrue(allowed, "%s 含私有主机名：%s" % (name, host))


class SanitizeUrlTest(unittest.TestCase):
    def test_private_host_masked_but_path_kept(self):
        out = recorder.sanitize("url: https://api.internal-gw.cn/v1/responses")
        self.assertEqual(out, "url: https://<endpoint>/v1/responses")

    def test_public_host_kept(self):
        text = "see https://github.com/o/r and https://registry.npmjs.org/pkg"
        self.assertEqual(recorder.sanitize(text), text,
                         "公共站点被误遮，证据指向就丢了")


if __name__ == "__main__":
    unittest.main()


class TimeoutEvidenceTest(unittest.TestCase):
    """超时退出取证：预算要在墙钟上真的生效，且终态能解释成超时。"""

    def _run(self, status="failed", code=None):
        step = {"agent": "github-copilot-cli"}
        if code:
            step["error_code"] = code
        return {"status": status, "steps": [step]}

    def test_stopped_within_budget_is_pass(self):
        out = recorder.classify_timeout(self._run(code="TIMEOUT"), 18.1, 15000, True)
        self.assertTrue(out.startswith("通过"), out)

    def test_overrunning_budget_is_failure(self):
        """实测慢一倍才停，说明预算没真正生效——不能因为「最后也停了」就写过。"""
        out = recorder.classify_timeout(self._run(code="TIMEOUT"), 90.0, 15000, True)
        self.assertIn("未通过", out)
        self.assertIn("墙钟上限没生效", out)

    def test_completed_normally_is_not_timeout_evidence(self):
        out = recorder.classify_timeout(self._run(status="done"), 12.0, 15000, True)
        self.assertIn("未通过", out)

    def test_unrelated_error_code_is_not_timeout_evidence(self):
        out = recorder.classify_timeout(self._run(code="RATE_LIMIT"), 18.0, 15000, True)
        self.assertIn("未通过", out)
        self.assertIn("RATE_LIMIT", out)

    def test_timeout_wording_counts_when_no_code(self):
        # 2026-10-23 实测：运行记录没落错误码，终态文本是「[超时 15.0s，已终止进程树]」。
        out = recorder.classify_timeout(self._run(), 18.1, 15000, True,
                                        "[超时 15.0s，已终止进程树]")
        self.assertTrue(out.startswith("通过"), out)
        self.assertIn("终态文本含超时措辞", out)

    def test_no_wording_and_no_code_is_failure(self):
        out = recorder.classify_timeout(self._run(), 18.1, 15000, True, "失败了")
        self.assertIn("未通过", out)

    def test_uninvoked_cli_is_unmeasured(self):
        out = recorder.classify_timeout(self._run(code="TIMEOUT"), 18.0, 15000, False)
        self.assertTrue(out.startswith("未测"), out)


class ResumeEvidenceTest(unittest.TestCase):
    def test_secret_found_is_pass(self):
        self.assertTrue(recorder.classify_resume(True, "42", True).startswith("通过"))

    def test_secret_absent_is_failure(self):
        self.assertIn("未通过", recorder.classify_resume(True, "42", False))

    def test_without_secret_is_unmeasured(self):
        """没有暗号就只能证明「跑过」，证不了「上下文接上了」，一律记未测。"""
        self.assertIn("未测", recorder.classify_resume(True, "", True))

    def test_uninvoked_cli_is_unmeasured(self):
        self.assertIn("未测", recorder.classify_resume(False, "42", True))


class PatchCatalogTimeoutTest(unittest.TestCase):
    def _write(self, tmpdir, entries):
        import json
        path = os.path.join(tmpdir, "catalog.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(entries, fh)
        return path

    def test_only_target_entry_changes(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, [
                {"id": "a", "orch": {"kind": "generic", "timeout_ms": 999}},
                {"id": "b", "orch": {"kind": "generic", "timeout_ms": 999}},
            ])
            self.assertTrue(recorder.patch_catalog_timeout(tmp, "a", 15000))
            with open(path, encoding="utf-8") as fh:
                entries = json.load(fh)
            self.assertEqual(entries[0]["orch"]["timeout_ms"], 15000)
            self.assertEqual(entries[1]["orch"]["timeout_ms"], 999)

    def test_entry_without_orch_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, [{"id": "goose", "orch": None}])
            self.assertFalse(recorder.patch_catalog_timeout(tmp, "goose", 15000))

    def test_missing_catalog_file_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(recorder.patch_catalog_timeout(tmp, "a", 15000))


class CopilotCatalogContractTest(unittest.TestCase):
    """GitHub Copilot CLI 从「只检测」升级为「可编排」时的契约。

    这条锁两件事：没有可执行 command 的条目不能被当成可编排（历史上 orch 为 None
    时探测拿条目 id 当可执行名，`github-copilot-cli` 永远「未检测到」）；无头模板
    必须带 --allow-all-tools，否则非交互态所有工具调用都等确认、任务空转。
    """

    def test_entry_is_runnable_with_headless_flags(self):
        from app.core import catalog
        entry = catalog.by_id("github-copilot-cli")
        orch = entry.get("orch") or {}
        self.assertEqual(entry["detect"]["cli"], "copilot")
        self.assertEqual(orch.get("command"), "copilot")
        self.assertEqual(orch.get("kind"), "generic")
        argv = orch.get("argv_template") or []
        self.assertIn("{prompt}", argv)
        self.assertIn("--allow-all-tools", argv)
        self.assertIn("-p", argv)
        self.assertGreater(int(orch.get("timeout_ms") or 0), 0)

    def test_resume_template_uses_session(self):
        from app.core import catalog
        orch = catalog.by_id("github-copilot-cli")["orch"]
        tmpl = orch.get("resume_argv_template") or []
        self.assertIn("{session}", tmpl)
        self.assertIn("-r", tmpl)

    def test_discovery_probes_real_executable(self):
        from app.core import cli_discovery
        spec = {s["name"]: s for s in cli_discovery.default_specs()}["github-copilot-cli"]
        self.assertEqual(spec["executable"], "copilot")

    def test_entries_without_orch_have_no_guessed_command(self):
        """没有 orch 的条目（如 goose）必须留空，不能拿条目 id 冒充可执行文件。"""
        from app.core import catalog
        for entry in catalog.DEFAULT_CATALOG:
            if entry.get("orch") is None:
                self.assertFalse(recorder.executable_of(entry) and
                                 (entry.get("orch") or {}).get("command"),
                                 entry.get("id"))
