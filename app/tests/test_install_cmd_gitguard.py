# -*- coding: utf-8 -*-
"""安装命令统一执行面（manager.run_install_cmd）的 git 传输层兜底回归。

背景：Mac 上 trae-agent 的 uv tool install / AI 修复 pip 命令先后死于
git clone 的 "Error in the HTTP2 framing layer"（GitHub 对国内链路的
HTTP/2 常被中间设备掐断）——执行面强制 HTTP/1.1 并对抖动原地重试一次。
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import manager


def _res(ok=True, stderr="", stdout=""):
    return {"ok": ok, "exit_code": 0 if ok else 128, "stdout": stdout,
            "stderr": stderr, "cancelled": False, "timed_out": False}


class GitSafeEnvTests(unittest.TestCase):
    def test_git_plus_command_forces_http11(self):
        env = manager._git_safe_env(
            "uv tool install --python 3.12 git+https://github.com/bytedance/trae-agent")
        self.assertEqual(env["GIT_CONFIG_KEY_0"], "http.version")
        self.assertEqual(env["GIT_CONFIG_VALUE_0"], "HTTP/1.1")
        self.assertEqual(env["GIT_CONFIG_COUNT"], "1")

    def test_plain_command_gets_no_env(self):
        self.assertEqual(manager._git_safe_env("npm install -g @anything"), {})
        self.assertEqual(manager._git_safe_env(""), {})


class GitFlakeTests(unittest.TestCase):
    def test_framing_error_matches(self):
        self.assertTrue(manager._git_transport_flake(_res(
            ok=False,
            stderr="fatal: unable to access 'https://github.com/x/y/': "
                   "Error in the HTTP2 framing layer")))

    def test_ordinary_build_failure_does_not_match(self):
        self.assertFalse(manager._git_transport_flake(_res(
            ok=False, stderr="error: Failed to build openssl-sys, exit code: 101")))


class RunInstallCmdTests(unittest.TestCase):
    def test_git_flake_retries_once_then_succeeds(self):
        calls = []

        def fake_run_process(**kw):
            calls.append(kw)
            if len(calls) == 1:
                return _res(ok=False, stderr="fatal: unable to access "
                                             "'https://github.com/': Error in the "
                                             "HTTP2 framing layer")
            return _res(ok=True)

        with patch.object(manager.runner, "run_process", side_effect=fake_run_process):
            res = manager.run_install_cmd(
                "uv tool install --python 3.12 git+https://github.com/bytedance/trae-agent")
        self.assertTrue(res["ok"])
        self.assertTrue(res.get("retried"))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["env"]["GIT_CONFIG_VALUE_0"], "HTTP/1.1")
        # uv 头命令同时挂镜像兜底（只补缺）
        self.assertIn("UV_PYTHON_INSTALL_MIRROR", calls[0]["env"])
        # 重试在审计头留痕
        self.assertIn("自动重试", calls[1]["audit_notes"][0])

    def test_ordinary_failure_stays_single_attempt(self):
        calls = []

        def fake_run_process(**kw):
            calls.append(kw)
            return _res(ok=False, stderr="error: Failed to build: openssl-sys")

        with patch.object(manager.runner, "run_process", side_effect=fake_run_process):
            res = manager.run_install_cmd(
                "python3 -m pip install git+https://github.com/bytedance/trae-agent")
        self.assertFalse(res["ok"])
        self.assertNotIn("retried", res)
        self.assertEqual(len(calls), 1)
        # 非 uv 头的 git+ 命令只挂 git 兜底，不挂 uv 镜像
        self.assertIn("GIT_CONFIG_VALUE_0", calls[0]["env"])
        self.assertNotIn("UV_PYTHON_INSTALL_MIRROR", calls[0]["env"])

    def test_no_env_for_plain_command(self):
        calls = []

        def fake_run_process(**kw):
            calls.append(kw)
            return _res(ok=True)

        with patch.object(manager.runner, "run_process", side_effect=fake_run_process):
            manager.run_install_cmd("winget install -e --id astral-sh.uv --silent")
        self.assertIsNone(calls[0]["env"])


class RepairPromptTests(unittest.TestCase):
    def test_prompt_pins_original_package_manager(self):
        from core import jobs
        self.assertIn("沿用失败命令的包管理器", jobs.AI_REPAIR_PROMPT)


class FlakeStrTests(unittest.TestCase):
    def test_accepts_plain_text_blob(self):
        self.assertTrue(manager._git_transport_flake("fatal: HTTP2 framing layer"))
        self.assertFalse(manager._git_transport_flake("error: EBUSY resource busy"))


class GitmodHttp11Tests(unittest.TestCase):
    def test_git_calls_carry_http11_env(self):
        from core import gitmod
        with patch.object(gitmod.runner, "run_process",
                          return_value={"ok": True}) as rp:
            gitmod._git("some/workdir", "fetch", "--all")
        kwargs = rp.call_args.kwargs
        self.assertEqual(kwargs["env"]["GIT_CONFIG_VALUE_0"], "HTTP/1.1")
        self.assertEqual(kwargs["argv"][:2], ["git", "fetch"])


class MarketGitDownloadTests(unittest.TestCase):
    def _entry(self):
        return {"install": {"url": "https://gitlab.com/acme/pkg.git", "path": "sub"}}

    def test_clone_forces_http11_and_retries_on_flake(self):
        from core import market_remote
        calls = []

        class Proc:
            def __init__(self, code, err=""):
                self.returncode = code
                self.stderr = err

        def fake_run(cmd, **kw):
            calls.append(kw)
            if len(calls) == 1:
                return Proc(128, "fatal: unable to access: "
                                 "Error in the HTTP2 framing layer")
            return Proc(0)

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "git" / "sub").mkdir(parents=True)   # 假装 clone 已落盘
            with patch.object(market_remote, "assert_public_url"), \
                 patch.object(market_remote.shutil, "which", return_value="git"), \
                 patch.object(market_remote.subprocess, "run", side_effect=fake_run):
                root = market_remote._download_git(self._entry(), tmp)
            self.assertEqual(root.name, "sub")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["env"]["GIT_CONFIG_VALUE_0"], "HTTP/1.1")

    def test_ordinary_clone_failure_no_retry(self):
        from core import market_remote
        calls = []

        class Proc:
            returncode = 1
            stderr = "error: Failed to build openssl-sys"

        def fake_run(cmd, **kw):
            calls.append(kw)
            return Proc()

        with tempfile.TemporaryDirectory() as td:
            with patch.object(market_remote, "assert_public_url"), \
                 patch.object(market_remote.shutil, "which", return_value="git"), \
                 patch.object(market_remote.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(ValueError):
                    market_remote._download_git(self._entry(), Path(td))
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
