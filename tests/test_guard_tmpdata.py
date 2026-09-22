# -*- coding: utf-8 -*-
"""测试实例防毒闸：TUTTI_DATA 在临时目录且主目录是真实主目录时，
一切对真实 CLI 配置的写入（write_model / _sync_settings_env / 专属注入器）
必须拦截；且运行前防线 sync_runtime_config 在「绑定没变但文件被外部改写」
时必须重写（指纹命中不等于文件没变）。

背景（2026-09-22 两案）：①忘设假 HOME 的测试服务触发 launch/自愈/autobind
同步，把 a.test/sk-test 夹具毒进真实 ~/.claude/settings.json，导致 claude
步骤全体 ENOTFOUND；②test_binding_dead_gate 夹具 P1/x.example 经未加闸的
kimi 注入器毒进真实 ~/.kimi-code/config.toml，同日真实运行换将 kimi 时指纹
命中跳过重写，kimi 带毒配置静默退出码 1。
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


_KIMI_PROV = {"protocol": "openai", "base_url": "https://x.example/v1",
              "api_key": "k", "name": "P1"}


class TestInjectorHomeGuard(BaseTest):
    """专属注入器（kimi/opencode/dsh）与 write_model 同闸：测试实例绝不直写
    真实主目录的 CLI 配置（2026-09-22 kimi 毒化实案的堵口）。"""

    def setUp(self):
        super().setUp()
        # 假主目录放 tests/ 下（不在系统临时目录）：让「真实主目录」分支成立，
        # 拦截失效时写入也只落夹具目录，tearDown 清场
        self.fake_home = _REPO_TESTS / "_guard_home_fixture"
        if self.fake_home.exists():
            shutil.rmtree(self.fake_home)
        self.fake_home.mkdir()
        self.addCleanup(shutil.rmtree, self.fake_home, ignore_errors=True)
        self.td = Path(tempfile.mkdtemp(prefix="orch-tddata-"))
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)
        os.environ["TUTTI_DATA"] = str(self.td)
        patcher = mock.patch("app.core.manager.os.path.expanduser",
                             _fake_expanduser(self.fake_home))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _kimi_entry(self):
        return {"id": "kimi-code", "name": "Kimi Code",
                "config": {"path": "~/.kimi-code/config.toml", "format": "toml-section",
                           "model_key": "default_model"}}

    def _oc_entry(self):
        return {"id": "opencode", "name": "OpenCode",
                "config": {"path": "~/.config/opencode/opencode.json", "format": "json"}}

    def _dsh_entry(self):
        return {"id": "deepseek-harness", "name": "DeepSeek Harness",
                "config": {"path": "~/.dsh/settings.yaml", "format": "yaml-line",
                           "model_key": "agent-default-model.model"}}

    def test_kimi_injector_refused_under_tmpdata(self):
        from app.core import manager
        err = manager._sync_kimi_settings(self._kimi_entry(), "m1", _KIMI_PROV)
        self.assertIsNotNone(err, "kimi 注入器必须被防毒闸拦截")
        self.assertIn("拦截", err)
        self.assertFalse((self.fake_home / ".kimi-code" / "config.toml").exists())

    def test_opencode_injector_refused_under_tmpdata(self):
        from app.core import manager
        err = manager._sync_opencode_settings(self._oc_entry(), "m1", _KIMI_PROV)
        self.assertIsNotNone(err, "opencode 注入器必须被防毒闸拦截")
        self.assertIn("拦截", err)
        self.assertFalse((self.fake_home / ".config" / "opencode").exists())

    def test_dsh_sync_refused_under_tmpdata(self):
        from app.core import manager
        err = manager._sync_dsh_settings(self._dsh_entry(), "m1", "https://x.example/v1")
        self.assertIsNotNone(err, "dsh 端点同步必须被防毒闸拦截")
        self.assertIn("拦截", err)
        self.assertFalse((self.fake_home / ".dsh" / "settings.yaml").exists())

    def test_kimi_injector_writes_in_production_form(self):
        """生产形态（无 TUTTI_DATA）→ 闸不拦，照常写进（假）主目录。"""
        from app.core import manager
        os.environ.pop("TUTTI_DATA", None)
        err = manager._sync_kimi_settings(self._kimi_entry(), "m1", _KIMI_PROV)
        self.assertIsNone(err, err)
        text = (self.fake_home / ".kimi-code" / "config.toml").read_text("utf-8")
        self.assertIn("baseUrl = \"https://x.example/v1\"", text)
        self.assertIn('defaultModel = "m1"', text)


class TestRuntimeSyncDriftHeal(BaseTest):
    """运行前防线的防漂移：绑定没变但 CLI 配置文件被外部改写（测试毒写/
    手工编辑托管段）时，指纹命中也必须按绑定重写（2026-09-22 kimi 实案：
    毒配置在指纹缓存下永久存活，换将步骤直接带毒起跑）。"""

    def test_kimi_drift_heals_on_fingerprint_hit(self):
        import json as _json
        from app.core import manager, modelhub as MH

        home = self.tmp / "home"          # 假家在临时目录下：防毒闸放行
        (home / ".kimi-code").mkdir(parents=True)
        cfg = home / ".kimi-code" / "config.toml"

        def fake_expanduser(p):
            if p == "~":
                return str(home)
            if p.startswith("~/"):
                return str(home / p[2:])
            return p

        manager._RUNTIME_SYNC["fps"].clear()
        manager._RUNTIME_SYNC["files"].clear()
        self.addCleanup(manager._RUNTIME_SYNC["fps"].clear)
        self.addCleanup(manager._RUNTIME_SYNC["files"].clear)

        MH._FILE.write_text(_json.dumps({
            "providers": [{"id": "p1", "name": "P1", "protocol": "openai",
                           "base_url": "https://live.ep/v1", "api_key": "sk-live",
                           "enabled": True,
                           "models": [{"name": "m1", "enabled": True}]}],
            "bindings": {"kimi-code": {"provider_id": "p1", "model": "m1",
                                       "chain": [{"provider_id": "p1", "model": "m1"}],
                                       "models": ["m1"]}}}), encoding="utf-8")

        agent = {"id": "kimi-code", "label": "Kimi Code", "kind": "kimi", "mode": "real"}
        orig = manager._sync_launch_model
        calls = []

        def counting(*a, **k):
            calls.append(1)
            return orig(*a, **k)

        manager._sync_launch_model = counting
        self.addCleanup(setattr, manager, "_sync_launch_model", orig)
        with mock.patch("app.core.manager.os.path.expanduser", fake_expanduser):
            manager.sync_runtime_config(agent)
            self.assertTrue(cfg.exists(), "首同步应落盘托管块")
            self.assertIn("https://live.ep/v1", cfg.read_text("utf-8"))
            n1 = len(calls)
            manager.sync_runtime_config(agent)
            self.assertEqual(len(calls), n1, "绑定与文件都没变：指纹命中应跳过写盘")
            cfg.write_bytes("default_model = \"m1\"\n"
                            "# >>> CodeBee managed (do not edit between markers) >>>\n"
                            "defaultProvider = \"orch\"\n"
                            "# <<< CodeBee managed <<<\n".encode("utf-8"))
            manager.sync_runtime_config(agent)
            self.assertEqual(len(calls), n1 + 1, "文件被外部改写：指纹命中也必须重写")
            text = cfg.read_text("utf-8")
            self.assertIn("https://live.ep/v1", text, "重写应恢复真实绑定端点")
