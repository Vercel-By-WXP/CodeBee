# -*- coding: utf-8 -*-
"""一键打开（catalog.launch）回归：web 类起服务+开浏览器，console 类新终端窗口。

锁定的不变量：
1. catalog.load() 幂等补 launch：老 data/catalog.json 没有的条目自动补齐，
   用户手写的 launch 不覆盖；全部 11 个内置条目都有 launch；
2. console：新终端窗口 argv 形如 cmd /c start <title> /D <root> cmd /k <cli>，
   绑定密钥注入子进程 env（交互进程脱离编排链路，全靠这层注入）；
3. web：端口已监听 → 直接开浏览器不重复起服务；未监听 → 后台起服务 +
   就绪后开浏览器；open_browser=False 时绝不开浏览器（测试/远控静默路径）；
4. launch_env 与编排同源（resolve_binding）：dsh 注 DEEPSEEK_*，无绑定返回 {}。
"""
from __future__ import annotations

import json
import socket
import threading
import time
import unittest
import unittest.mock as mock

from base import BaseTest


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _entry(**over):
    from app.core import catalog
    e = {"id": "fake-cli", "name": "Fake CLI", "detect": {"cli": "fake"},
         "orch": None, "install": "", "upgrade": ""}
    e.update(over)
    return e


def _strip_jsonc_comments(s):
    """剥掉 // 与 /* */ 注释（跳过字符串字面量），供 JSONC patch 结果校验。"""
    out, i, n, instr = [], 0, len(s), False
    while i < n:
        c = s[i]
        if instr:
            out.append(c)
            if c == "\\":
                out.append(s[i + 1])
                i += 2
                continue
            if c == '"':
                instr = False
            i += 1
            continue
        if c == '"':
            instr = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "/":
            i = s.find("\n", i)
            i = n if i < 0 else i
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "*":
            e = s.find("*/", i + 2)
            i = n if e < 0 else e + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


class TestLaunchModelSync(BaseTest):
    """打开前同步「模型+端点+密钥」：dsh web 打开即选中可用模型的三项保证。
    1) 绑定存在 → agent-default-model.model + llm-deepseek.baseURL + models
       列表条目全部落 settings.yaml，密钥 env 注入（dsh web 下拉只列 models，
       缺条目=选不中；settings 端点优先级高于 env，不同步=密钥发给旧端点）；
    2) 无绑定 → 自检：默认模型不在端点列表（glm-5.3-flash 实况）自动改选
       列表第一个，并提示密钥缺失；
    3) models 条目幂等：已存在不重复追加。"""

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.home.mkdir()
        # 动态空闲端口：不撞真机 dsh web 的 18790（否则走「已在运行」分支）
        self.port = _free_port()

        def fake_expanduser(p):
            if p == "~":
                return str(self.home)
            if p.startswith("~/"):
                return str(self.home / p[2:])
            return p

        patcher = mock.patch("app.core.manager.os.path.expanduser", fake_expanduser)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.settings = self.home / ".dsh" / "settings.yaml"
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(
            "permission:\n"
            "  defaultPreset: danger-full-access\n"
            "llm-deepseek:\n"
            "  baseURL: https://old.endpoint/v1\n"
            "  models:\n"
            "    - id: DeepSeek-V4-Flash\n"
            "      name: DeepSeek-V4-Flash\n"
            "      contextWindow: 1000000\n"
            "agent-default-model:\n"
            '  provider: deepseek-official\n'
            '  model: "glm-5.3-flash"\n'
            "  reasoningEffort: high\n",
            encoding="utf-8")

    def _entry(self):
        return {"id": "deepseek-harness", "name": "DeepSeek Harness",
                "detect": {"cli": "dsh"},
                "config": {"path": "~/.dsh/settings.yaml", "format": "yaml-line",
                           "model_key": "agent-default-model.model"},
                "launch": {"kind": "web",
                           "command": "dsh web --port %d --no-open" % self.port,
                           "port": self.port}}

    def test_ensure_model_entry_append_and_idempotent(self):
        from app.core import manager
        text = self.settings.read_text(encoding="utf-8")
        out, added = manager._yaml_ensure_model_entry(text, "llm-deepseek", "glm-x")
        self.assertTrue(added)
        self.assertIn("- id: \"glm-x\"", out)
        self.assertIn("agent-default-model:", out)  # 其它段完好
        out2, added2 = manager._yaml_ensure_model_entry(out, "llm-deepseek", "glm-x")
        self.assertFalse(added2)
        self.assertEqual(out, out2)
        # 无 models 键的段：保守不动
        out3, added3 = manager._yaml_ensure_model_entry(
            "llm-deepseek:\n  baseURL: https://x/v1\n", "llm-deepseek", "glm-x")
        self.assertFalse(added3)
        self.assertEqual(out3, "llm-deepseek:\n  baseURL: https://x/v1\n")

    def test_sync_dsh_settings_writes_triple(self):
        from app.core import manager
        err = manager._sync_dsh_settings(self._entry(), "glm-x", "https://new.ep/v1")
        self.assertIsNone(err, err)
        text = self.settings.read_text(encoding="utf-8")
        self.assertIn("baseURL: \"https://new.ep/v1\"", text)
        self.assertIn("- id: \"glm-x\"", text)
        self.assertIn("- id: DeepSeek-V4-Flash", text)  # 原条目保留
        self.assertTrue((self.home / ".dsh" / "settings.yaml.bak").exists())
        # write_model 补齐 agent-default-model.model 后自检应通过
        manager.write_model(self._entry(), "glm-x")
        fixed, note = manager._dsh_selfcheck_model(self._entry())
        self.assertEqual(fixed, "glm-x")
        self.assertEqual(note, "")

    def test_dsh_selfcheck_fixes_stale_model(self):
        """实况复现：默认模型 glm-5.3-flash 不在端点列表 → 自动改选第一个。"""
        from app.core import manager
        fixed, note = manager._dsh_selfcheck_model(self._entry())
        self.assertEqual(fixed, "DeepSeek-V4-Flash")
        self.assertIn("glm-5.3-flash", note)
        self.assertIn("DeepSeek-V4-Flash", note)
        self.assertIn('model: "DeepSeek-V4-Flash"',
                      self.settings.read_text(encoding="utf-8"))

    def test_launch_web_with_binding_syncs_triple(self):
        """有绑定：settings.yaml 三元组落盘 + key env 注入 + message 带同步笔记。"""
        from app.core import manager, modelhub
        err = modelhub.upsert_provider({
            "name": "DSH厂商", "protocol": "openai",
            "base_url": "https://dsh.test/v1", "api_key": "sk-dsh-test"})
        self.assertIsNone(err, err)
        pid = modelhub.providers()[0]["id"]
        modelhub.set_binding("deepseek-harness", provider_id=pid, model="glm-x")
        spawned = []
        with mock.patch.object(manager, "detect_entry", return_value={"installed": True}), \
             mock.patch.object(manager.subprocess, "Popen",
                               lambda *a, **k: spawned.append((a, k)) or mock.MagicMock()):
            res = manager.launch(self._entry(), open_browser=False)
        self.assertTrue(res["ok"], res)
        text = self.settings.read_text(encoding="utf-8")
        self.assertIn("baseURL: \"https://dsh.test/v1\"", text)
        self.assertIn("- id: \"glm-x\"", text)
        self.assertIn("model: \"glm-x\"", text)
        argv, kw = spawned[0][0][0], spawned[0][1]
        self.assertEqual(argv[:2], ["cmd", "/c"])
        self.assertIn("dsh web --port %d --no-open" % self.port, argv[2])
        # 密钥走 Popen env（不进命令串，避免泄漏到进程列表）
        self.assertEqual(kw["env"].get("DEEPSEEK_API_KEY"), "sk-dsh-test")
        self.assertIn("glm-x", res["message"])
        self.assertIn("https://dsh.test/v1", res["message"])

    def test_launch_web_without_binding_selfchecks(self):
        """无绑定（当前真机状态）：过期默认模型被改选 + 密钥缺失有明确提示。"""
        from app.core import manager
        with mock.patch.object(manager, "detect_entry", return_value={"installed": True}), \
             mock.patch.object(manager.subprocess, "Popen",
                               lambda *a, **k: mock.MagicMock()):
            res = manager.launch(self._entry(), open_browser=False)
        self.assertTrue(res["ok"], res)
        self.assertIn("DeepSeek-V4-Flash", res["message"])
        self.assertIn("未发现 dsh 密钥", res["message"])
        self.assertIn('model: "DeepSeek-V4-Flash"',
                      self.settings.read_text(encoding="utf-8"))


class TestCodexSync(BaseTest):
    """codex 交互 TUI 打开即用：config.toml 必须同时有 provider 段与 model
    （交互模式不认编排的 -c 覆盖，也不认 ORCH_API_KEY env——只写 model 会把
    请求发到 codex 自带官方端点上）。"""

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.home.mkdir()

        def fake_expanduser(p):
            if p == "~":
                return str(self.home)
            if p.startswith("~/"):
                return str(self.home / p[2:])
            return p

        patcher = mock.patch("app.core.manager.os.path.expanduser", fake_expanduser)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.toml = self.home / ".codex" / "config.toml"
        self.toml.parent.mkdir(parents=True, exist_ok=True)
        self.toml.write_text(
            'model = "gpt-5.5"\n'
            "\n"
            "[model_providers.custom]\n"
            'name = "custom"\n'
            'base_url = "https://old/v1"\n'
            'env_key = "CUSTOM_KEY"\n'
            'wire_api = "responses"\n',
            encoding="utf-8")

    def _entry(self):
        return {"id": "codex-cli", "name": "Codex CLI", "detect": {"cli": "codex"},
                "config": {"path": "~/.codex/config.toml", "format": "toml-line",
                           "model_key": "model"}}

    def test_sync_writes_provider_and_model(self):
        from app.core import manager
        cp = {"name": "orch", "base_url": "https://vsllm.cc/v1",
              "env_key": "ORCH_API_KEY", "wire_api": "responses"}
        err = manager._sync_codex_settings(self._entry(), "deepseek-v4-flash", cp)
        self.assertIsNone(err, err)
        text = self.toml.read_text(encoding="utf-8")
        self.assertIn("[model_providers.orch]", text)
        self.assertIn('base_url = "https://vsllm.cc/v1"', text)
        self.assertIn('env_key = "ORCH_API_KEY"', text)
        self.assertIn('model_provider = "orch"', text)
        self.assertIn('model = "deepseek-v4-flash"', text)
        # 用户原有段原样保留（.bak 兜底）
        self.assertIn("[model_providers.custom]", text)
        self.assertIn('base_url = "https://old/v1"', text)
        self.assertTrue((self.home / ".codex" / "config.toml.bak").exists())
        # 幂等：重复同步内容不变
        err2 = manager._sync_codex_settings(self._entry(), "deepseek-v4-flash", cp)
        self.assertIsNone(err2)
        self.assertEqual(manager._config_path(self._entry()) and
                         open(self.toml, encoding="utf-8").read(), text)

    def test_sync_creates_file_when_missing(self):
        from app.core import manager
        self.toml.unlink()
        cp = {"name": "orch", "base_url": "https://x/v1"}
        err = manager._sync_codex_settings(self._entry(), "m1", cp)
        self.assertIsNone(err, err)
        text = self.toml.read_text(encoding="utf-8")
        self.assertIn("[model_providers.orch]", text)
        self.assertIn('model = "m1"', text)


class TestLaunch(BaseTest):

    def test_catalog_launch_patch_idempotent(self):
        """内置条目全部补上 launch；用户手写的 launch 不被覆盖。"""
        from app.core import catalog
        entries = catalog.load()
        self.assertEqual(len(entries), 11)
        for e in entries:
            self.assertTrue((e.get("launch") or {}).get("command"), e["id"])
        dsh = catalog.by_id("deepseek-harness")
        self.assertEqual(dsh["launch"]["kind"], "web")
        self.assertEqual(dsh["launch"]["port"], 18790)
        self.assertIn("--port 18790", dsh["launch"]["command"])
        # 用户手写过的 launch 原样保留
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._paths.CATALOG_FILE.write_text(json.dumps([
            {"id": "mine", "name": "Mine", "detect": {"cli": "x"},
             "launch": {"kind": "console", "command": "my-way"}}],
            ensure_ascii=False), encoding="utf-8")
        mine = catalog.by_id("mine") if any(e["id"] == "mine" for e in catalog.load(force=True)) else None
        self.assertIsNotNone(mine)
        self.assertEqual(mine["launch"]["command"], "my-way")

    def test_launch_rejects_not_installed_and_unconfigured(self):
        from app.core import manager
        with mock.patch.object(manager, "detect_entry", return_value={"installed": False}):
            res = manager.launch(_entry(launch={"kind": "console", "command": "x"}))
            self.assertFalse(res["ok"])
            self.assertIn("未安装", res["error"])
        with mock.patch.object(manager, "detect_entry", return_value={"installed": True}):
            res = manager.launch(_entry())
            self.assertFalse(res["ok"])
            self.assertIn("launch", res["error"])

    def test_launch_console_argv_and_env(self):
        """console：start 新终端 + cmd /k 保留窗口；绑定密钥进子进程 env。"""
        from app.core import manager
        spawned = []

        def fake_popen(argv, **kw):
            spawned.append((argv, kw))
            return mock.MagicMock()

        with mock.patch.object(manager, "detect_entry", return_value={"installed": True}), \
             mock.patch("app.core.modelhub.resolve_binding",
                        return_value={"model": "m1", "env": {"DEEPSEEK_API_KEY": "sk-x"}}), \
             mock.patch.object(manager.subprocess, "Popen", fake_popen):
            res = manager.launch(_entry(name="Codex CLI",
                                        launch={"kind": "console", "command": "codex"}))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["kind"], "console")
        argv, kw = spawned[0]
        self.assertEqual(argv[:4], ["cmd", "/c", "start", "CodeBee Codex CLI"])
        self.assertEqual(argv[-3:], ["cmd", "/k", "codex"])
        self.assertEqual(kw["env"]["DEEPSEEK_API_KEY"], "sk-x")
        self.assertEqual(kw["creationflags"], manager.CREATE_NO_WINDOW)

    def test_launch_web_already_running(self):
        """端口已监听：只开浏览器，不再起第二个服务。"""
        from app.core import manager
        port = _free_port()
        srv = socket.socket()
        srv.bind(("127.0.0.1", port))
        srv.listen(1)
        try:
            opened = []
            spawned = []

            def fake_popen(*a, **kw):
                spawned.append(a)
                raise AssertionError("不应重复起服务")

            with mock.patch.object(manager, "detect_entry", return_value={"installed": True}), \
                 mock.patch.object(manager.webbrowser, "open",
                                   lambda u: opened.append(u)), \
                 mock.patch.object(manager.subprocess, "Popen", fake_popen):
                res = manager.launch(_entry(launch={
                    "kind": "web", "command": "whatever", "port": port}))
            self.assertTrue(res["ok"], res)
            self.assertIn("已在运行", res["message"])
            self.assertEqual(opened, ["http://127.0.0.1:%d" % port])
            self.assertEqual(spawned, [])
        finally:
            srv.close()

    def test_launch_web_spawns_and_opens_when_ready(self):
        """端口空闲：后台起服务（输出重定向到启动日志）；就绪后开 token URL。"""
        from app.core import manager
        port = _free_port()
        opened = []
        spawned = []

        def fake_popen(argv, **kw):
            spawned.append((argv, kw))
            return mock.MagicMock()

        waits = []
        with mock.patch.object(manager, "detect_entry", return_value={"installed": True}), \
             mock.patch.object(manager.webbrowser, "open",
                               lambda u: opened.append(u)), \
             mock.patch.object(manager.subprocess, "Popen", fake_popen), \
             mock.patch.object(manager, "_open_when_ready",
                               lambda p, u, lp, timeout=30: waits.append((p, u))):
            res = manager.launch(_entry(id="fake-cli", launch={
                "kind": "web", "command": "serve --port %d" % port, "port": port}))
            self.assertTrue(res["ok"], res)
            self.assertEqual(waits, [(port, "http://127.0.0.1:%d" % port)])
        argv, kw = spawned[0]
        # 命令串重定向到启动日志（无空格路径不加引号，避开 cmd /c 引号剥离）
        log_tail = argv[2].split(" > ", 1)[1]
        self.assertTrue(log_tail.endswith(("2>&1", '2>&1"')), argv)
        self.assertIn("serve --port %d" % port, argv[2])
        self.assertEqual(kw["creationflags"], manager.CREATE_NO_WINDOW)
        # 就绪探测真实跑一遍：绑定端口 + 日志里有带 token 的 URL → 应开 token URL
        logp = manager._launch_log_path(_entry(id="fake-cli"))
        logp.parent.mkdir(parents=True, exist_ok=True)
        tok = "http://127.0.0.1:%d/?token=abc123" % port
        logp.write_text("dsh web: %s\n" % tok, encoding="utf-8")
        srv = socket.socket()
        srv.bind(("127.0.0.1", port))
        srv.listen(1)
        try:
            with mock.patch.object(manager.webbrowser, "open",
                                   lambda u: opened.append(u)):
                manager._open_when_ready(port, "http://127.0.0.1:%d" % port, logp, timeout=3)
            self.assertEqual(opened, [tok])
        finally:
            srv.close()

    def test_launch_web_open_browser_false_never_opens(self):
        """open_browser=False：两条路径（已运行/新起）都不碰浏览器。"""
        from app.core import manager
        port = _free_port()
        srv = socket.socket()
        srv.bind(("127.0.0.1", port))
        srv.listen(1)
        try:
            with mock.patch.object(manager, "detect_entry", return_value={"installed": True}), \
                 mock.patch.object(manager.webbrowser, "open",
                                   lambda u: self.fail("不应开浏览器")):
                res = manager.launch(_entry(launch={
                    "kind": "web", "command": "whatever", "port": port}),
                    open_browser=False)
            self.assertTrue(res["ok"], res)
        finally:
            srv.close()

    def test_launch_env_follows_binding(self):
        """launch_env 与编排同源：dsh 绑定注入 DEEPSEEK_*；无绑定返回 {}。"""
        from app.core import manager, modelhub
        self.assertEqual(manager.launch_env(_entry(id="deepseek-harness")), {})
        err = modelhub.upsert_provider({
            "name": "DSH厂商", "protocol": "openai",
            "base_url": "https://dsh.test/v1", "api_key": "sk-dsh-test"})
        self.assertIsNone(err, err)
        pid = modelhub.providers()[0]["id"]
        modelhub.set_binding("deepseek-harness", provider_id=pid, model="deepseek-chat")
        env = manager.launch_env(_entry(id="deepseek-harness"))
        self.assertEqual(env.get("DEEPSEEK_API_KEY"), "sk-dsh-test")
        self.assertEqual(env.get("DEEPSEEK_BASE_URL"), "https://dsh.test/v1")

    def test_open_when_ready_timeout_still_opens(self):
        """服务一直起不来：超时兜底也开浏览器（服务可能只是慢）。"""
        from app.core import manager
        port = _free_port()  # 没人监听
        opened = []
        with mock.patch.object(manager.webbrowser, "open", lambda u: opened.append(u)):
            t0 = time.time()
            manager._open_when_ready(port, "http://x", self.data_dir / "no.log", timeout=1.5)
            self.assertLess(time.time() - t0, 10)
        self.assertEqual(opened, ["http://x"])

    def test_best_url_prefers_token(self):
        """信任 URL 提取：无 token 行返回 None→裸 URL 兜底；有 token 优先取。"""
        from app.core import manager
        entry = _entry(id="fake-cli")
        logp = manager._launch_log_path(entry)
        logp.parent.mkdir(parents=True, exist_ok=True)
        # 只有别的端口的行 → None
        logp.write_text("dsh web: http://127.0.0.1:1/?token=x\n", encoding="utf-8")
        self.assertIsNone(manager._best_url(logp, 18790))
        # 同端口无 token → 取裸 URL
        logp.write_text("listening on http://127.0.0.1:18790/\n", encoding="utf-8")
        self.assertEqual(manager._best_url(logp, 18790), "http://127.0.0.1:18790/")
        # 同端口有 token → 优先 token URL
        logp.write_text("dsh web: http://127.0.0.1:18790/?token=tok123\n", encoding="utf-8")
        self.assertEqual(manager._best_url(logp, 18790),
                         "http://127.0.0.1:18790/?token=tok123")
        # 日志不存在 → None
        self.assertIsNone(manager._best_url(logp.parent / "nope.log", 18790))

    def test_launch_log_path_whitelist_and_stable(self):
        """id 白名单直接用作文件名；越界形态走 crc32 稳定代称，绝不落出数据目录。"""
        from app.core import manager
        good = manager._launch_log_path(_entry(id="codex-cli"))
        self.assertEqual(good.name, "codex-cli.log")
        self.assertTrue(str(good).startswith(str(self.data_dir)))
        # 恶意 id：不含有毒字符，且两次生成一致（跨重启可回读）
        evil = manager._launch_log_path(_entry(id="../../evil"))
        self.assertNotIn("..", str(evil))
        self.assertEqual(evil, manager._launch_log_path(_entry(id="../../evil")))
        self.assertTrue(str(evil).startswith(str(self.data_dir)))
        self.assertTrue(evil.name.endswith(".log"))

    def test_catalog_view_exposes_launch(self):
        from app.core import manager
        # catalog_view 会逐条目真实探测版本（cmd /c <cli> --version），个别 CLI
        # 的 shim 持有输出管道会让 py3.8 的 communicate 永久阻塞——测试里跳过
        with mock.patch.object(manager, "version_of", lambda e: "-"):
            view = {v["id"]: v for v in manager.catalog_view()}
        self.assertEqual(view["deepseek-harness"]["launch"]["kind"], "web")
        self.assertEqual(view["codex-cli"]["launch"]["command"], "codex")


class TestLaunchPick(BaseTest):
    """launch_pick：打开专属选链——协议不匹配不降级（claude 拿 openai 的 key
    等于没 key），宁可明确提示也不静默错注入。"""

    def _prov(self, modelhub, name, protocol, base_url="https://x.test/v1", **over):
        err = modelhub.upsert_provider({"name": name, "protocol": protocol,
                                        "base_url": base_url, "api_key": "sk-" + name})
        self.assertIsNone(err, err)
        prov = [p for p in modelhub.providers() if p["name"] == name][0]
        prov.update(over)
        return prov

    def test_pick_first_enabled_matching_protocol(self):
        from app.core import modelhub
        anth = self._prov(modelhub, "Z.ai", "anthropic")
        oai = self._prov(modelhub, "维云", "openai")
        modelhub.set_binding("claude-code", chain=[
            {"provider_id": anth["id"], "model": "glm-5.3-flash"},
            {"provider_id": oai["id"], "model": "deepseek-v4"}])
        pick, note = modelhub.launch_pick("claude-code", ("anthropic",))
        self.assertIsNone(note, note)
        self.assertEqual(pick["provider"]["id"], anth["id"])
        self.assertEqual(pick["model"], "glm-5.3-flash")

    def test_skips_disabled_then_reports_names(self):
        """链首 anthropic 停用、后面只有 openai 可用：不降级，点名停用供应商。"""
        from app.core import modelhub
        anth = self._prov(modelhub, "Z.ai", "anthropic")
        oai = self._prov(modelhub, "维云", "openai")
        modelhub.providers_op([anth["id"]], "disable")
        modelhub.set_binding("claude-code", chain=[
            {"provider_id": anth["id"], "model": "glm-5.3-flash"},
            {"provider_id": oai["id"], "model": "deepseek-v4"}])
        pick, note = modelhub.launch_pick("claude-code", ("anthropic",))
        self.assertIsNone(pick)
        self.assertIn("Z.ai", note)
        self.assertIn("停用", note)
        self.assertIn("模型调度", note)  # 可操作指引

    def test_protocol_mismatch_reported(self):
        """只有协议不匹配的可用供应商：明确说不匹配而非静默。"""
        from app.core import modelhub
        oai = self._prov(modelhub, "维云", "openai")
        modelhub.set_binding("claude-code", provider_id=oai["id"], model="deepseek-v4")
        pick, note = modelhub.launch_pick("claude-code", ("anthropic",))
        self.assertIsNone(pick)
        self.assertIn("不匹配", note)

    def test_no_binding_reported(self):
        from app.core import modelhub
        pick, note = modelhub.launch_pick("claude-code", ("anthropic",))
        self.assertIsNone(pick)
        self.assertIn("未指定", note)


class TestClaudeSync(BaseTest):
    """claude-code 打开即用：anthropic 供应商三元组落 ~/.claude/settings.json
    的 env（交互 TUI 与无头共用该文件；只认 ANTHROPIC_*）。"""

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.home.mkdir()

        def fake_expanduser(p):
            if p == "~":
                return str(self.home)
            if p.startswith("~/"):
                return str(self.home / p[2:])
            return p

        patcher = mock.patch("app.core.manager.os.path.expanduser", fake_expanduser)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.settings = self.home / ".claude" / "settings.json"
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(json.dumps({
            "env": {"ANTHROPIC_BASE_URL": "https://old/v1",
                    "ANTHROPIC_AUTH_TOKEN": "sk-old",
                    "ANTHROPIC_API_KEY": "sk-xapi"},
            "permissions": {"allow": ["Bash(git:*)"]}}, ensure_ascii=False, indent=2),
            encoding="utf-8")

    def _entry(self):
        return {"id": "claude-code", "name": "Claude Code", "detect": {"cli": "claude"},
                "config": {"path": "~/.claude/settings.json", "format": "json",
                           "model_key": "model"}}

    def test_sync_writes_env_and_keeps_rest(self):
        from app.core import manager
        prov = {"name": "Z.ai", "protocol": "anthropic",
                "base_url": "https://api.z.ai/api/anthropic", "api_key": "cb-new"}
        err = manager._sync_claude_settings(self._entry(), "glm-5.3-flash", prov)
        self.assertIsNone(err, err)
        data = json.loads(self.settings.read_text(encoding="utf-8"))
        env = data["env"]
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://api.z.ai/api/anthropic")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "cb-new")
        self.assertEqual(env["ANTHROPIC_MODEL"], "glm-5.3-flash")
        # AUTH_TOKEN 与 x-api-key 互斥：换 AUTH 时清残留 API_KEY，防止带错头
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        # 用户的其他配置原样保留
        self.assertEqual(data["permissions"], {"allow": ["Bash(git:*)"]})
        self.assertTrue((self.home / ".claude" / "settings.json.bak").exists())
        # 幂等：重复同步内容不变
        err2 = manager._sync_claude_settings(self._entry(), "glm-5.3-flash", prov)
        self.assertIsNone(err2)
        self.assertEqual(json.loads(self.settings.read_text(encoding="utf-8")), data)

    def test_sync_aborts_on_invalid_json(self):
        from app.core import manager
        self.settings.write_text("{not json", encoding="utf-8")
        prov = {"name": "Z.ai", "protocol": "anthropic",
                "base_url": "https://api.z.ai/api/anthropic", "api_key": "cb-new"}
        err = manager._sync_claude_settings(self._entry(), "glm-x", prov)
        self.assertIn("中止", err)
        self.assertEqual(self.settings.read_text(encoding="utf-8"), "{not json")

    def test_sync_creates_file_when_missing(self):
        from app.core import manager
        self.settings.unlink()
        prov = {"name": "Z.ai", "protocol": "anthropic",
                "base_url": "https://api.z.ai/api/anthropic", "api_key": "cb-new"}
        err = manager._sync_claude_settings(self._entry(), "", prov)
        self.assertIsNone(err, err)
        data = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(data["env"]["ANTHROPIC_AUTH_TOKEN"], "cb-new")
        self.assertNotIn("ANTHROPIC_MODEL", data["env"])  # 无模型不写该键


class TestOpencodeSync(BaseTest):
    """opencode 打开即用：绑定供应商落 provider.orch 段 + 顶层 model。
    纯 JSON 走整体读改写；带注释 JSONC 走文本级 patch（注释保留）。"""

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.home.mkdir()

        def fake_expanduser(p):
            if p == "~":
                return str(self.home)
            if p.startswith("~/"):
                return str(self.home / p[2:])
            return p

        patcher = mock.patch("app.core.manager.os.path.expanduser", fake_expanduser)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.oc_dir = self.home / ".config" / "opencode"
        self.oc_dir.mkdir(parents=True, exist_ok=True)

    def _entry(self, path="~/.config/opencode/opencode.jsonc"):
        return {"id": "opencode", "name": "OpenCode CLI", "detect": {"cli": "opencode"},
                "config": {"path": path, "format": "jsonc", "model_key": "model"}}

    _PROV = {"name": "维云", "protocol": "openai",
             "base_url": "https://vsllm.cc/v1", "api_key": "sk-vy"}

    def test_plain_json_patch_keeps_mcp(self):
        """真机形态：纯 JSON + 已有 mcp/provider 空表。"""
        from app.core import manager
        cfg = self.oc_dir / "opencode.json"
        cfg.write_text(json.dumps({
            "$schema": "https://opencode.ai/config.json",
            "mcp": {"fetch": {"enabled": True}}, "provider": {}}), encoding="utf-8")
        err = manager._sync_opencode_settings(self._entry("~/.config/opencode/opencode.json"),
                                              "deepseek-v4", self._PROV)
        self.assertIsNone(err, err)
        data = json.loads(cfg.read_text(encoding="utf-8"))
        orch = data["provider"]["orch"]
        self.assertEqual(orch["npm"], "@ai-sdk/openai-compatible")
        self.assertEqual(orch["options"]["baseURL"], "https://vsllm.cc/v1")
        self.assertEqual(orch["options"]["apiKey"], "sk-vy")
        self.assertIn("deepseek-v4", orch["models"])
        self.assertEqual(data["model"], "orch/deepseek-v4")
        self.assertEqual(data["mcp"], {"fetch": {"enabled": True}})
        self.assertTrue((self.oc_dir / "opencode.json.bak").exists())

    def test_jsonc_with_comments_preserved(self):
        from app.core import manager
        cfg = self.oc_dir / "opencode.jsonc"
        cfg.write_text(
            "// 全局配置\n{\n  // 我的注释\n  \"mcp\": { \"fetch\": { \"enabled\": true } }\n}\n",
            encoding="utf-8")
        err = manager._sync_opencode_settings(self._entry(), "glm-5.3-flash",
                                              {"name": "Z.ai", "protocol": "anthropic",
                                               "base_url": "https://api.z.ai/api/anthropic",
                                               "api_key": "cb-1"})
        self.assertIsNone(err, err)
        text = cfg.read_text(encoding="utf-8")
        self.assertIn("// 我的注释", text)
        self.assertIn("// 全局配置", text)
        data = json.loads(_strip_jsonc_comments(text))
        self.assertEqual(data["provider"]["orch"]["npm"], "@ai-sdk/anthropic")
        self.assertEqual(data["model"], "orch/glm-5.3-flash")

    def test_creates_configc_when_missing(self):
        from app.core import manager
        err = manager._sync_opencode_settings(self._entry(), "m1", self._PROV)
        self.assertIsNone(err, err)
        data = json.loads((self.oc_dir / "opencode.jsonc").read_text(encoding="utf-8"))
        self.assertEqual(data["provider"]["orch"]["npm"], "@ai-sdk/openai-compatible")

    def test_launch_injection_end_to_end(self):
        """launch(claude-code)：链首 anthropic 可用 → 注入 + message 报喜；
        全停用 → 配置不动 + message 点名停用供应商（不静默废）。"""
        from app.core import manager, modelhub
        settings = self.home / ".claude" / "settings.json"
        anth = {"name": "Z.ai", "protocol": "anthropic",
                "base_url": "https://api.z.ai/api/anthropic", "api_key": "cb-ok"}
        err = modelhub.upsert_provider(anth)
        self.assertIsNone(err, err)
        pid = modelhub.providers()[0]["id"]
        modelhub.set_binding("claude-code", provider_id=pid, model="glm-5.3-flash")
        ce = {"id": "claude-code", "name": "Claude Code", "detect": {"cli": "claude"},
              "config": {"path": "~/.claude/settings.json", "format": "json",
                         "model_key": "model"},
              "launch": {"kind": "console", "command": "claude"}}
        spawned = []
        with mock.patch.object(manager, "detect_entry", return_value={"installed": True}), \
             mock.patch.object(manager.subprocess, "Popen",
                               lambda *a, **k: spawned.append(a) or mock.MagicMock()):
            res = manager.launch(ce, open_browser=False)
        self.assertTrue(res["ok"], res)
        self.assertIn("已注入", res["message"])
        self.assertIn("Z.ai", res["message"])
        data = json.loads(settings.read_text(encoding="utf-8"))
        self.assertEqual(data["env"]["ANTHROPIC_BASE_URL"], "https://api.z.ai/api/anthropic")
        # 反转：供应商停用 → 配置不动 + 点名提示
        modelhub.providers_op([pid], "disable")
        with mock.patch.object(manager, "detect_entry", return_value={"installed": True}), \
             mock.patch.object(manager.subprocess, "Popen",
                               lambda *a, **k: mock.MagicMock()):
            res2 = manager.launch(ce, open_browser=False)
        self.assertTrue(res2["ok"], res2)
        self.assertIn("停用", res2["message"])
        self.assertIn("Z.ai", res2["message"])
        data2 = json.loads(settings.read_text(encoding="utf-8"))
        self.assertEqual(data2, data)  # 未降级错注入：配置保持上次正确状态

    def test_grok_reports_missing_channel(self):
        """grok（无专属注入通道）无绑定时明确提示需登录，不静默废。"""
        from app.core import manager
        ge = {"id": "grok-build", "name": "Grok Build", "detect": {"cli": "grok"},
              "launch": {"kind": "console", "command": "grok"}}
        with mock.patch.object(manager, "detect_entry", return_value={"installed": True}), \
             mock.patch.object(manager.subprocess, "Popen",
                               lambda *a, **k: mock.MagicMock()):
            res = manager.launch(ge, open_browser=False)
        self.assertTrue(res["ok"], res)
        self.assertIn("未指定可用供应商", res["message"])


class TestQwenSync(BaseTest):
    """qwencode 打开即用：openai 兼容供应商写 ~/.qwen/settings.json 的 env 段
    （真机实测 OPENAI_* 存在即优先走 openai 兼容通道）；用户已有 env 键保留。"""

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.home.mkdir()

        def fake_expanduser(p):
            if p == "~":
                return str(self.home)
            if p.startswith("~/"):
                return str(self.home / p[2:])
            return p

        patcher = mock.patch("app.core.manager.os.path.expanduser", fake_expanduser)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.settings = self.home / ".qwen" / "settings.json"
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(json.dumps({
            "$version": 4,
            "env": {"BAILIAN_PLAN_FLAG": "user-own-bailian-entry"},
            "mcpServers": {"filesystem": {"command": "npx"}}}, ensure_ascii=False, indent=2),
            encoding="utf-8")

    def _entry(self):
        return {"id": "qwencode", "name": "QwenCode", "detect": {"cli": "qwen"},
                "config": {"path": "~/.qwen/settings.json", "format": None,
                           "model_key": None}}

    def test_sync_writes_openai_env_and_keeps_user_keys(self):
        from app.core import manager
        vy_key = "vy-" + "test-credential"
        prov = {"name": "维云", "protocol": "openai",
                "base_url": "https://vsllm.cc/v1", "api_key": vy_key}
        err = manager._sync_qwen_settings(self._entry(), "[opencode]deepseek-v4-flash", prov)
        self.assertIsNone(err, err)
        data = json.loads(self.settings.read_text(encoding="utf-8"))
        env = data["env"]
        self.assertEqual(env["OPENAI_BASE_URL"], "https://vsllm.cc/v1")
        self.assertEqual(env["OPENAI_API_KEY"], vy_key)
        self.assertEqual(env["OPENAI_MODEL"], "[opencode]deepseek-v4-flash")
        # 用户自己的 env 键与 mcp 配置原样保留
        self.assertEqual(env["BAILIAN_PLAN_FLAG"], "user-own-bailian-entry")
        self.assertEqual(data["mcpServers"], {"filesystem": {"command": "npx"}})
        # 幂等
        err2 = manager._sync_qwen_settings(self._entry(), "[opencode]deepseek-v4-flash", prov)
        self.assertIsNone(err2)
        self.assertEqual(json.loads(self.settings.read_text(encoding="utf-8")), data)

    def test_qwencode_launch_injection_and_mismatch(self):
        """qwencode 打开：链里 openai 可用 → 注入 env 段 + message 报喜；
        只有 anthropic 可用（未实证不开）→ 明确提示协议不匹配而非错注入。"""
        from app.core import manager, modelhub
        anth = {"name": "Z.ai", "protocol": "anthropic",
                "base_url": "https://api.z.ai/api/anthropic", "api_key": "cb-ok"}
        oai = {"name": "维云", "protocol": "openai",
               "base_url": "https://vsllm.cc/v1", "api_key": "vy-test"}
        for p in (anth, oai):
            err = modelhub.upsert_provider(p)
            self.assertIsNone(err, err)
        byname = {p["name"]: p["id"] for p in modelhub.providers()}
        modelhub.set_binding("qwencode", chain=[
            {"provider_id": byname["Z.ai"], "model": "glm-5.3-flash"},
            {"provider_id": byname["维云"], "model": "deepseek-v4"}])
        qe = {"id": "qwencode", "name": "QwenCode", "detect": {"cli": "qwen"},
              "config": {"path": "~/.qwen/settings.json", "format": None,
                         "model_key": None},
              "launch": {"kind": "console", "command": "qwen"}}
        with mock.patch.object(manager, "detect_entry", return_value={"installed": True}), \
             mock.patch.object(manager.subprocess, "Popen",
                               lambda *a, **k: mock.MagicMock()):
            res = manager.launch(qe, open_browser=False)
        self.assertTrue(res["ok"], res)
        self.assertIn("已注入", res["message"])
        self.assertIn("维云", res["message"])  # 链首 anthropic 被跳过，注入链上首个 openai
        data = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(data["env"]["OPENAI_BASE_URL"], "https://vsllm.cc/v1")
        # 反转：openai 供应商停用 → 停用即出调度（4e89532），链上维云条目
        # 被剔除；剩余 Z.ai 与 openai 协议不匹配 → 明确提示而非错注入
        modelhub.providers_op([byname["维云"]], "disable")
        with mock.patch.object(manager, "detect_entry", return_value={"installed": True}), \
             mock.patch.object(manager.subprocess, "Popen",
                               lambda *a, **k: mock.MagicMock()):
            res2 = manager.launch(qe, open_browser=False)
        self.assertIn("不匹配", res2["message"])
        self.assertNotIn("已注入", res2["message"])
        self.assertNotIn("维云", res2["message"], "停用即出调度：链上条目应已被剔除")

    def test_catalog_config_patch_fixes_qwencode(self):
        """老 data/catalog.json 里 qwencode 的 format=json（legacy model 无效）：
        CONFIG_PATCH 幂等修正为 None，用户手写过的其他条目不受影响。"""
        from app.core import catalog
        e = catalog.by_id("qwencode")
        self.assertIsNone((e.get("config") or {}).get("format"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._paths.CATALOG_FILE.write_text(json.dumps([
            {"id": "qwencode", "name": "QwenCode", "detect": {"cli": "qwen"},
             "config": {"path": "~/.qwen/settings.json", "format": "json",
                        "model_key": "model"}}], ensure_ascii=False),
            encoding="utf-8")
        e2 = catalog.by_id("qwencode") if any(
            x["id"] == "qwencode" for x in catalog.load(force=True)) else None
        self.assertIsNone((e2.get("config") or {}).get("format"))


if __name__ == "__main__":
    unittest.main()
