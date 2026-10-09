# -*- coding: utf-8 -*-
"""mgmt 安装链 uv 依赖防线（2026-10-09 Mac aider/trae 双装失败实案）：

① PATH 快照过旧（Mac 服务吃不到 shell rc / 服务起跑后才装 uv）时，安装命令
   首 token 原地换成磁盘已知落点的绝对路径——装完即用，不必重启服务。
② 连磁盘落点都探不到的 uv 系安装命令 → uv_missing 人话报错 + 跳过 AI 修复
   （修复链就算装上 uv，复检目标 CLI 仍未装，结论还是失败，白烧一轮诊断）。
③ POSIX 补 ~/.local/bin 与 brew bin 兜底探测（此前 darwin 只有裸 which，
   curl 脚本装的 uv/aider 在 GUI 启动的服务里恒假「未安装」/127）。
"""
from __future__ import annotations

import os
import tempfile
import unittest.mock as mock
from pathlib import Path

from base import BaseTest


class TestResolveMgmtTool(BaseTest):

    def _patch_lost(self, probe=None):
        """模拟 PATH 快照落空：which 恒 None；probe 可注入落点。返回上下文。"""
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            with mock.patch("app.core.manager.shutil.which", lambda c: None), \
                 mock.patch("app.core.runner._fallback_probe_cli",
                            probe or (lambda name: None)):
                yield
        return _ctx()

    def test_which_hit_unchanged(self):
        """PATH 直接命中：命令原样，零改写。"""
        from app.core import manager
        cmd, missing = manager._resolve_mgmt_tool(
            "uv tool install --python 3.12 aider-chat", "install")
        self.assertFalse(missing)
        self.assertEqual(cmd, "uv tool install --python 3.12 aider-chat")

    def test_probe_hit_substitutes_first_token(self):
        """PATH 落空 + 磁盘落点命中：首 token 换绝对路径，其余原样保留。"""
        from app.core import manager
        with self._patch_lost(probe=lambda name: "/Users/pp/.local/bin/uv"):
            cmd, missing = manager._resolve_mgmt_tool(
                "uv tool install --python 3.12 aider-chat", "install")
        self.assertFalse(missing)
        self.assertTrue(cmd.startswith("/Users/pp/.local/bin/uv "))
        self.assertIn("aider-chat", cmd)

    def test_uv_missing_flagged(self):
        """PATH 与磁盘落点双落空 + uv 系安装命令 → uv_missing=True。"""
        from app.core import manager
        with self._patch_lost():
            _, missing = manager._resolve_mgmt_tool(
                "uv tool install --python 3.12 aider-chat", "install")
            self.assertTrue(missing)
            _, missing = manager._resolve_mgmt_tool(
                "uv tool upgrade aider-chat", "upgrade")
            self.assertTrue(missing)

    def test_non_uv_tool_missing_not_flagged(self):
        """非 uv 命令（npm 等）落空不标 uv_missing，照原样跑给真实报错。"""
        from app.core import manager
        with self._patch_lost():
            cmd, missing = manager._resolve_mgmt_tool(
                "npm install -g @anthropic-ai/claude-code", "install")
        self.assertFalse(missing)
        self.assertEqual(cmd, "npm install -g @anthropic-ai/claude-code")

    def test_uninstall_op_never_flagged(self):
        """卸载不标 uv_missing（uv 没了也无从卸起，留原始报错）。"""
        from app.core import manager
        with self._patch_lost():
            _, missing = manager._resolve_mgmt_tool(
                "uv tool uninstall trae-agent", "uninstall")
        self.assertFalse(missing)

    def test_absolute_command_untouched(self):
        """已是绝对路径的命令不探不换。"""
        from app.core import manager
        cmd, missing = manager._resolve_mgmt_tool(
            "/opt/homebrew/bin/uv tool install aider-chat", "install")
        self.assertFalse(missing)
        self.assertEqual(cmd, "/opt/homebrew/bin/uv tool install aider-chat")


class TestUvMissingGate(BaseTest):
    """run_mgmt_command 的 uv 链：先自动补装 uv，失败才人话收口（带 uv_missing 标记，
    jobs 侧凭标记跳过 AI 修复）；uv 命令挂镜像兜底 env。"""

    def _entry(self):
        return {"id": "aider", "name": "Aider",
                "install": "uv tool install --python 3.12 aider-chat",
                "upgrade": "uv tool upgrade aider-chat"}

    def test_bootstrap_success_continues_install(self):
        """uv 缺失 → 自动补装成功 → 重解析换绝对路径 → 目标安装照常执行。"""
        from app.core import manager
        called = []
        probe_hits = [None, "/Users/pp/.local/bin/uv"]   # 首查落空，补装后命中

        def _probe(name):
            return probe_hits.pop(0) if probe_hits else None
        orig = (manager.runner.run_process, manager.detect_all)
        with mock.patch("app.core.manager.shutil.which", lambda c: None), \
             mock.patch("app.core.runner._fallback_probe_cli", _probe), \
             mock.patch.object(manager, "_bootstrap_uv",
                               lambda cancel_event=None, log_path=None:
                               {"ok": True, "detail": "已自动补装 uv（curl）"}), \
             mock.patch.object(manager.runner, "run_process",
                               lambda **kw: called.append(kw) or
                               {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}):
            manager.detect_all = lambda force=False: {}
            try:
                res = manager.run_mgmt_command(self._entry(), "install")
            finally:
                manager.runner.run_process, manager.detect_all = orig
        self.assertTrue(res["ok"])
        self.assertEqual(len(called), 1, "补装成功后只应跑目标安装这一条命令")
        self.assertTrue(called[0]["shell_cmd"].startswith("/Users/pp/.local/bin/uv "))

    def test_install_fails_after_bootstrap_with_hint(self):
        """自动补装也失败 → uv_missing 人话收口，错误带尝试明细与手动指引。"""
        from app.core import manager
        called = []
        orig = (manager.runner.run_process, manager.detect_all)
        with mock.patch("app.core.manager.shutil.which", lambda c: None), \
             mock.patch("app.core.runner._fallback_probe_cli", lambda n: None), \
             mock.patch.object(manager, "_bootstrap_uv",
                               lambda cancel_event=None, log_path=None:
                               {"ok": False, "detail": "brew 退出码 1"}), \
             mock.patch.object(manager.runner, "run_process",
                               lambda **kw: called.append(kw) or {}):
            manager.detect_all = lambda force=False: {}
            try:
                res = manager.run_mgmt_command(self._entry(), "install")
            finally:
                manager.runner.run_process, manager.detect_all = orig
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("uv_missing"))
        self.assertIn("自动安装 uv 未成功", res["error"])
        self.assertIn("brew 退出码 1", res["error"])
        self.assertIn("手动安装", res["error"])
        self.assertEqual(called, [], "uv 缺失必须命令前短路，不进 shell 烧 127")

    def test_darwin_hint_brew(self):
        """Mac 的手动指引指 brew 渠道。"""
        from app.core import manager
        orig = (manager.runner.run_process, manager.detect_all)
        with mock.patch("sys.platform", "darwin"), \
             mock.patch("app.core.manager.shutil.which", lambda c: None), \
             mock.patch("app.core.runner._fallback_probe_cli", lambda n: None), \
             mock.patch.object(manager, "_bootstrap_uv",
                               lambda cancel_event=None, log_path=None:
                               {"ok": False, "detail": "无渠道"}):
            manager.detect_all = lambda force=False: {}
            try:
                res = manager.run_mgmt_command(self._entry(), "install")
            finally:
                manager.runner.run_process, manager.detect_all = orig
        self.assertIn("brew install uv", res["error"])


class TestMirrorEnv(BaseTest):
    """uv 系命令挂镜像兜底 env；非 uv 命令不挂；用户自配的值不覆盖。"""

    def _entry(self):
        return {"id": "aider", "name": "Aider",
                "install": "uv tool install --python 3.12 aider-chat",
                "upgrade": "uv tool upgrade aider-chat"}

    def test_uv_cmd_gets_mirrors(self):
        from app.core import manager
        called = []
        orig = (manager.runner.run_process, manager.detect_all)
        saved = {k: os.environ.get(k) for k in
                 ("UV_PYTHON_INSTALL_MIRROR", "UV_DEFAULT_INDEX")}
        for k in saved:
            os.environ.pop(k, None)
        try:
            with mock.patch("app.core.manager.shutil.which",
                            lambda c: "C:/tools/uv.exe"), \
                 mock.patch.object(manager.runner, "run_process",
                                   lambda **kw: called.append(kw) or
                                   {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}):
                manager.detect_all = lambda force=False: {}
                manager.run_mgmt_command(self._entry(), "install")
        finally:
            manager.runner.run_process, manager.detect_all = orig
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(len(called), 1)
        env = called[0].get("env") or {}
        self.assertIn("npmmirror", env.get("UV_PYTHON_INSTALL_MIRROR", ""))
        self.assertIn("tuna", env.get("UV_DEFAULT_INDEX", ""))

    def test_user_mirror_preserved(self):
        """用户已自配镜像时不覆盖（只补缺）。"""
        from app.core import manager
        called = []
        orig = (manager.runner.run_process, manager.detect_all)
        saved = os.environ.get("UV_DEFAULT_INDEX")
        os.environ["UV_DEFAULT_INDEX"] = "https://mirrors.example.com/pypi"
        try:
            with mock.patch("app.core.manager.shutil.which",
                            lambda c: "C:/tools/uv.exe"), \
                 mock.patch.object(manager.runner, "run_process",
                                   lambda **kw: called.append(kw) or
                                   {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}):
                manager.detect_all = lambda force=False: {}
                manager.run_mgmt_command(self._entry(), "install")
        finally:
            manager.runner.run_process, manager.detect_all = orig
            if saved is None:
                os.environ.pop("UV_DEFAULT_INDEX", None)
            else:
                os.environ["UV_DEFAULT_INDEX"] = saved
        env = called[0].get("env") or {}
        # 用户自配值不进注入表（走进程 env 继承，不覆盖）；缺的镜像照补
        self.assertIsNone(env.get("UV_DEFAULT_INDEX"))
        self.assertIn("npmmirror", env.get("UV_PYTHON_INSTALL_MIRROR", ""))

    def test_non_uv_cmd_no_env(self):
        from app.core import manager
        called = []
        orig = (manager.runner.run_process, manager.detect_all)
        entry = {"id": "opencode", "name": "OpenCode",
                 "install": "npm install -g opencode-ai",
                 "upgrade": "npm install -g opencode-ai@latest"}
        try:
            with mock.patch.object(manager.runner, "run_process",
                                   lambda **kw: called.append(kw) or
                                   {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}):
                manager.detect_all = lambda force=False: {}
                manager.run_mgmt_command(entry, "install")
        finally:
            manager.runner.run_process, manager.detect_all = orig
        self.assertIsNone(called[0].get("env"), "非 uv 命令不该带镜像 env")

    def test_absolute_uv_head_recognized(self):
        """首 token 被换成绝对路径后仍认出 uv（挂镜像 env 的判定按 basename）。"""
        from app.core import manager
        self.assertTrue(manager._cmd_head_is_uv(
            "/Users/pp/.local/bin/uv tool install aider-chat"))
        self.assertTrue(manager._cmd_head_is_uv(r"C:\links\uv.exe tool install x"))
        self.assertFalse(manager._cmd_head_is_uv("npm install -g x"))
        self.assertFalse(manager._cmd_head_is_uv(""))


class TestBootstrapUv(BaseTest):
    """_bootstrap_uv 渠道选择与失败聚合（全部 stub run_process，不真装）。"""

    def test_win_winget_branch(self):
        from app.core import manager
        cmds = []
        with mock.patch("app.core.manager.shutil.which",
                        lambda c: r"C:\wg\winget.exe" if c == "winget" else None), \
             mock.patch("sys.platform", "win32"), \
             mock.patch.object(manager.runner, "run_process",
                               lambda **kw: cmds.append(kw["shell_cmd"]) or
                               {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}):
            boot = manager._bootstrap_uv()
        self.assertTrue(boot["ok"])
        self.assertIn("astral-sh.uv", cmds[0])

    def test_win_pip_fallback(self):
        from app.core import manager
        cmds = []

        def _which(c):
            return r"C:\py\py.exe" if c == "py" else None
        with mock.patch("app.core.manager.shutil.which", _which), \
             mock.patch("sys.platform", "win32"), \
             mock.patch.object(manager.runner, "run_process",
                               lambda **kw: cmds.append(kw["shell_cmd"]) or
                               {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}):
            boot = manager._bootstrap_uv()
        self.assertTrue(boot["ok"])
        self.assertEqual(cmds[0], "py -3 -m pip install uv")

    def test_posix_brew_then_curl(self):
        from app.core import manager
        cmds = []

        def _which(c):
            return "/opt/homebrew/bin/brew" if c == "brew" else "/usr/bin/curl"
        with mock.patch("app.core.manager.shutil.which", _which), \
             mock.patch("sys.platform", "darwin"), \
             mock.patch.object(manager.runner, "run_process",
                               lambda **kw: cmds.append(kw["shell_cmd"]) or
                               {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}):
            boot = manager._bootstrap_uv()
        self.assertTrue(boot["ok"])
        self.assertEqual(cmds[0], "brew install uv")

    def test_failure_aggregates_attempts(self):
        from app.core import manager
        with mock.patch("app.core.manager.shutil.which", lambda c: None), \
             mock.patch("sys.platform", "win32"):
            boot = manager._bootstrap_uv()
        self.assertFalse(boot["ok"])
        self.assertIn("渠道", boot["detail"])


class TestPosixProbe(BaseTest):
    """POSIX 磁盘落点探测：~/.local/bin 命中、落空返回 None。"""

    def test_local_bin_hit(self):
        from app.core import runner
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / ".local" / "bin" / "aider"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"#!/bin/sh\n")
            with mock.patch("os.name", "posix"), \
                 mock.patch("os.path.expanduser", lambda p: td):
                self.assertEqual(runner._fallback_probe_cli("aider"), str(exe))

    def test_miss_returns_none(self):
        from app.core import runner
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("os.name", "posix"), \
                 mock.patch("os.path.expanduser", lambda p: td):
                self.assertIsNone(runner._fallback_probe_cli("aider"))

    def test_non_bare_name_rejected(self):
        from app.core import runner
        with mock.patch("os.name", "posix"):
            self.assertIsNone(runner._fallback_probe_cli("./aider"))
            self.assertIsNone(runner._fallback_probe_cli("aider --help"))

    def test_which_cli_posix_fallback(self):
        """manager._which_cli 在 POSIX 也走磁盘兜底（此前 darwin 只有裸 which）。"""
        from app.core import manager
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / ".local" / "bin" / "trae-cli"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"#!/bin/sh\n")
            with mock.patch("os.name", "posix"), \
                 mock.patch("os.path.expanduser", lambda p: td), \
                 mock.patch("app.core.manager.shutil.which", lambda c: None):
                self.assertEqual(manager._which_cli("trae-cli"), str(exe))


class TestWingetHeadRelaxed(BaseTest):
    """首 token 被换成绝对路径后 _winget_already_ok 仍按 basename 认 winget。"""

    def test_absolute_winget_recognized(self):
        from app.core import manager
        res = {"ok": False, "exit_code": 2316632107, "stdout": "找不到可用的升级。",
               "stderr": ""}
        self.assertTrue(manager._winget_already_ok(
            r"C:\Users\x\AppData\Local\Microsoft\WinGet\Links\winget.exe"
            r" install -e --id Anthropic.ClaudeCode", "install", res))
        self.assertFalse(manager._winget_already_ok(
            r"C:\bin\npm.exe install -g x", "install", res))
