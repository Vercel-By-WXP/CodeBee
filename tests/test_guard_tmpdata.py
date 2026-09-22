# -*- coding: utf-8 -*-
"""测试实例防毒闸：TUTTI_DATA 在临时目录且主目录是真实主目录时，
一切对真实 CLI 配置的写入（write_model / _sync_settings_env）必须拦截。

背景（2026-09-22 实案）：忘设假 HOME 的测试服务触发 launch/自愈/autobind
同步，把 a.test/sk-test 夹具毒进真实 ~/.claude/settings.json，导致
claude 步骤全体 ENOTFOUND；下一次测试又覆盖回来，「越测越坏」。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest.mock as mock
from pathlib import Path

from base import BaseTest

_REPO_TESTS = Path(__file__).resolve().parent


def _fake_expanduser(target):
    real = os.path.expanduser

    def _exp(p):
        if p == "~":
            return str(target)
        if p.startswith("~/") or p.startswith("~\\"):
            return os.path.join(str(target), p[2:])
        return real(p)
    return _exp


class TestTmpDataGuard(BaseTest):
    def runTest(self):
        from app.core import manager

        # 假主目录刻意放在仓库 tests/ 下（不在系统临时目录）：既不碰真实 ~，
        # 又能让「真实主目录」分支（应拦截）成立；拦截失效时写入也只落在
        # 测试夹具目录内，tearDown 清场。
        fake_home = _REPO_TESTS / "_guard_home_fixture"
        if fake_home.exists():
            shutil.rmtree(fake_home)
        fake_home.mkdir()
        self.addCleanup(shutil.rmtree, fake_home, ignore_errors=True)

        entry = {"config": {"path": "~/.claude/settings.json", "format": "json"}}
        td_under_tmp = Path(tempfile.mkdtemp(prefix="orch-tddata-"))
        self.addCleanup(shutil.rmtree, td_under_tmp, ignore_errors=True)

        old_td = os.environ.get("TUTTI_DATA")
        os.environ["TUTTI_DATA"] = str(td_under_tmp)
        try:
            with mock.patch("app.core.manager.os.path.expanduser",
                            _fake_expanduser(fake_home)):
                # 1) write_model：夹具模型名绝不落进「真实」主目录
                r = manager.write_model(entry, "test-pro")
                self.assertFalse(r.get("ok"), r)
                self.assertIn("拦截", r.get("error", ""))
                # 2) env 注入：a.test 毒源路径同样拦截
                err = manager._sync_settings_env(
                    str(fake_home / ".claude" / "settings.json"),
                    {"ANTHROPIC_BASE_URL": "https://a.test/v1"})
                self.assertIsNotNone(err)
                self.assertIn("拦截", err)
            self.assertFalse((fake_home / ".claude" / "settings.json").exists())
        finally:
            if old_td is None:
                os.environ.pop("TUTTI_DATA", None)
            else:
                os.environ["TUTTI_DATA"] = old_td

        # 3) 假 HOME 也在临时目录（规范的隔离测试形态）→ 不拦截，写进假 HOME
        fake_home_tmp = Path(tempfile.mkdtemp(prefix="orch-fakehome-"))
        self.addCleanup(shutil.rmtree, fake_home_tmp, ignore_errors=True)
        os.environ["TUTTI_DATA"] = str(td_under_tmp)
        try:
            with mock.patch("app.core.manager.os.path.expanduser",
                            _fake_expanduser(fake_home_tmp)):
                r2 = manager.write_model(entry, "test-pro")
                self.assertTrue(r2.get("ok"), r2)
                written = json.loads(
                    (fake_home_tmp / ".claude" / "settings.json").read_text("utf-8"))
                self.assertEqual(written.get("model"), "test-pro")
        finally:
            if old_td is None:
                os.environ.pop("TUTTI_DATA", None)
            else:
                os.environ["TUTTI_DATA"] = old_td

        # 4) 生产形态（无 TUTTI_DATA）→ 正常写入，行为不变
        #    （BaseTest 自身会设 TUTTI_DATA，这里显式摘掉模拟生产进程）
        os.environ.pop("TUTTI_DATA", None)
        try:
            with mock.patch("app.core.manager.os.path.expanduser",
                            _fake_expanduser(fake_home)):
                r3 = manager.write_model(entry, "glm-ok")
                self.assertTrue(r3.get("ok"), r3)
                written = json.loads(
                    (fake_home / ".claude" / "settings.json").read_text("utf-8"))
                self.assertEqual(written.get("model"), "glm-ok")
        finally:
            os.environ["TUTTI_DATA"] = str(td_under_tmp)
