# -*- coding: utf-8 -*-
"""CLI 版本探测：CLI 启动即崩溃时不得把 traceback 首行当版本号。

实测来源（2026-10-23，Windows 11）：trae-cli --version 直接抛 ImportError，
probe() 把首行 "Traceback (most recent call last):" 写进 version，智能体管理页
于是显示「版本：Traceback (most recent call last):」——崩溃被伪装成版本号。
"""
import unittest
from unittest import mock

from app.core import cli_discovery


def _proc(stdout="", stderr="", rc=0, timed_out=False):
    """对齐 probe 内部真正执行的 `_run_capture` 返回形状。

    2026-10-23 起 probe 不再用 subprocess.run：它在 Windows 上对 .CMD 包装器
    不构成墙钟上限（只杀 cmd.exe，继承管道的 node.exe 会拖住 communicate）。
    所以打桩点从 subprocess.run 换成 _run_capture。
    """
    return (rc, stdout, stderr, timed_out)


class ProbeVersionTextTests(unittest.TestCase):
    def test_normal_version_is_kept_verbatim(self):
        self.assertEqual(
            cli_discovery._version_text("codex-cli 0.160.0\n"),
            ("codex-cli 0.160.0", ""))

    def test_empty_output_is_not_an_error(self):
        self.assertEqual(cli_discovery._version_text(""), ("", ""))

    def test_traceback_becomes_error_not_version(self):
        text = (
            "Traceback (most recent call last):\n"
            "  File \"<frozen runpy>\", line 198, in _run_module_as_main\n"
            "  File \"trae_agent/__init__.py\", line 8, in <module>\n"
            "ImportError: cannot import name 'BaseAgent'\n"
        )
        version, error = cli_discovery._version_text(text)
        self.assertEqual(version, "")
        self.assertIn("版本识别失败", error)
        self.assertIn("ImportError", error)
        self.assertNotIn("Traceback", version)


class ProbeResultTests(unittest.TestCase):
    def _probe(self, payload):
        with mock.patch.object(cli_discovery.shutil, "which",
                               return_value="C:/fake/cli.exe"), \
             mock.patch.object(cli_discovery, "_run_capture", return_value=payload):
            return cli_discovery.probe({"name": "demo", "executable": "cli"})

    def test_crashing_cli_reports_error_and_no_version(self):
        row = self._probe(_proc(
            stderr="Traceback (most recent call last):\n  File \"x\", line 1\n"
                   "OSError: broken install\n", rc=1))
        self.assertTrue(row["available"])
        self.assertEqual(row["version"], "")
        self.assertIn("OSError", row["error"])

    def test_healthy_cli_has_version_and_no_error(self):
        row = self._probe(_proc(stdout="opencode 1.18.34\n"))
        self.assertEqual(row["version"], "opencode 1.18.34")
        self.assertEqual(row["error"], "")

    def test_missing_executable_keeps_install_hint(self):
        with mock.patch.object(cli_discovery.shutil, "which", return_value=None):
            row = cli_discovery.probe({"name": "ghost", "executable": "ghost"})
        self.assertFalse(row["available"])
        self.assertEqual(row["version"], "")
        self.assertIn("install_hint", row)


class DiscoveryUiContractTests(unittest.TestCase):
    """后端把 traceback 转成 error 后，界面必须把它显示出来，否则用户只看到空版本。"""

    def setUp(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        self.app_js = (root / "app" / "ui" / "app.js").read_text(encoding="utf-8")
        self.i18n = (root / "app" / "ui" / "i18n.js").read_text(encoding="utf-8")

    def test_cli_discovery_row_renders_error(self):
        block = self.app_js.split("function renderCliDiscovery", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("row.error", block)
        self.assertIn("探测异常", block)

    def test_probe_failed_label_is_translated(self):
        self.assertIn('"探测异常"', self.i18n)


if __name__ == "__main__":
    unittest.main()
