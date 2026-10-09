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
    """run_mgmt_command 的 uv 缺失人话收口：不碰 run_process、带 uv_missing 标记。"""

    def test_install_fails_fast_with_hint(self):
        from app.core import manager
        entry = {"id": "aider", "name": "Aider",
                 "install": "uv tool install --python 3.12 aider-chat",
                 "upgrade": "uv tool upgrade aider-chat"}
        called = []
        orig = (manager.runner.run_process, manager.detect_all)
        with mock.patch("app.core.manager.shutil.which", lambda c: None), \
             mock.patch("app.core.runner._fallback_probe_cli", lambda n: None), \
             mock.patch.object(manager.runner, "run_process",
                               lambda **kw: called.append(kw) or {}):
            manager.detect_all = lambda force=False: {}
            try:
                res = manager.run_mgmt_command(entry, "install")
            finally:
                manager.runner.run_process, manager.detect_all = orig
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("uv_missing"))
        self.assertIn("uv", res["error"])
        self.assertEqual(called, [], "uv 缺失必须命令前短路，不进 shell 烧 127")

    def test_darwin_hint_brew(self):
        """Mac 提示 brew 渠道。"""
        from app.core import manager
        entry = {"id": "aider", "name": "Aider",
                 "install": "uv tool install --python 3.12 aider-chat",
                 "upgrade": "uv tool upgrade aider-chat"}
        orig = (manager.runner.run_process, manager.detect_all)
        with mock.patch("sys.platform", "darwin"), \
             mock.patch("app.core.manager.shutil.which", lambda c: None), \
             mock.patch("app.core.runner._fallback_probe_cli", lambda n: None):
            manager.detect_all = lambda force=False: {}
            try:
                res = manager.run_mgmt_command(entry, "install")
            finally:
                manager.runner.run_process, manager.detect_all = orig
        self.assertIn("brew install uv", res["error"])


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
