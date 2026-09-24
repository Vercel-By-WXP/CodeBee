# -*- coding: utf-8 -*-
"""DeepSeek Harness（dsh）接入：catalog 条目 / 新条目补入 / yaml 模型配置 / DEEPSEEK_* 注入。

实测事实（2026-09，本机 dsh 0.1.5-rc.2，来源 E:/GoOut/_dsh_ref 源码 + 真机跑通）：
- 无头调用是 `dsh --profile headless "<task>"`；任务只走位置参数（不支持 stdin），
  headless 应用只认 [task...] 与 --help，`--model` 这类 flag 会被拒。
- stdout 是最终答案纯文本；退出码 0=完成 / 1=错误；失败时 stderr 为 `dsh: <code>: <msg>`。
- 模型改不动 env/flag，只能由配置层决定（~/.dsh/settings.yaml 的
  agent-default-model.model）。
- 密钥走 DEEPSEEK_API_KEY，是 dsh 凭据解析的最高优先级（不写用户凭据文件）；
  端点走 DEEPSEEK_BASE_URL，但**settings.yaml 的 llm-deepseek.baseURL 优先级更高**
  （实测：settings 固定网关时，env 指到不存在的域名请求仍成功）。
"""
from __future__ import annotations

import json
from unittest import mock

from base import BaseTest

FAKE_KEY = "sk-test-" + "dddddddd" * 3
FAKE_KEY2 = "sk-test-" + "eeeeeeee" * 3


class TestCatalogEntry(BaseTest):

    def test_entry_shape(self):
        from app.core import catalog
        e = next(x for x in catalog.DEFAULT_CATALOG if x["id"] == "deepseek-harness")
        self.assertEqual(e["detect"], {"cli": "dsh"})
        self.assertEqual(e["orch"]["kind"], "generic")
        self.assertEqual(e["orch"]["command"], "dsh")
        # 无头入口：profile + 任务位置参数
        self.assertEqual(e["orch"]["argv_template"],
                         ["--profile", "headless", "{prompt}"])
        # headless profile 是一次性任务，没有会话恢复 → 不能声明 resume 模板
        self.assertNotIn("resume_argv_template", e["orch"])
        # 模型落在 dsh 自己的 settings.yaml 用户层
        self.assertEqual(e["config"]["format"], "yaml-line")
        self.assertEqual(e["config"]["model_key"], "agent-default-model.model")
        self.assertEqual(e["config"]["path"], "~/.dsh/settings.yaml")
        self.assertIn("@deepseek-ai/dsh", e["install"])
        self.assertFalse(e["default_enabled"])

    def test_merge_new_defaults_keeps_user_edits(self):
        """老用户的 catalog.json 已存在 → 按 id 幂等补入新智能体，不动已有条目。"""
        from app.core import catalog
        entries = [{"id": "codex-cli", "name": "用户改过的名字", "custom": True}]
        catalog._merge_new_defaults(entries)
        self.assertIn("deepseek-harness", [x["id"] for x in entries])
        # 已有条目原样保留（不覆盖用户手改字段）
        self.assertEqual(entries[0],
                         {"id": "codex-cli", "name": "用户改过的名字", "custom": True})
        # 幂等：重复合并不产生重复条目
        n = len(entries)
        catalog._merge_new_defaults(entries)
        self.assertEqual(len(entries), n)

    def test_merged_entry_does_not_alias_defaults(self):
        """补进去的条目必须深拷贝：_apply_resume_patch 会就地改 orch，
        浅拷贝会污染 DEFAULT_CATALOG，进而污染 reset_to_default() 的产物。"""
        from app.core import catalog
        entries = []
        catalog._merge_new_defaults(entries)
        merged = next(x for x in entries if x["id"] == "deepseek-harness")
        merged["orch"]["argv_template"].append("TAMPERED")
        merged["config"]["format"] = "TAMPERED"
        pristine = next(d for d in catalog.DEFAULT_CATALOG
                        if d["id"] == "deepseek-harness")
        self.assertNotIn("TAMPERED", pristine["orch"]["argv_template"])
        self.assertEqual(pristine["config"]["format"], "yaml-line")

    def test_load_patches_existing_file_without_rewriting_it(self):
        from app.core import catalog
        self._paths.CATALOG_FILE.write_text(
            json.dumps([{"id": "codex-cli"}]), encoding="utf-8")
        catalog._CACHE["entries"] = None
        try:
            ids = [x["id"] for x in catalog.load(force=True)]
            self.assertIn("deepseek-harness", ids)
            # 沿用 _apply_resume_patch 的约定：只在内存里补齐，不回写用户文件
            on_disk = json.loads(self._paths.CATALOG_FILE.read_text(encoding="utf-8"))
            self.assertEqual([x["id"] for x in on_disk], ["codex-cli"])
        finally:
            catalog._CACHE["entries"] = None


class TestYamlModelConfig(BaseTest):
    """~/.dsh/settings.yaml 的读写：嵌套键、引号、保留其它段与注释。"""

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
        self.settings = self.home / ".dsh" / "settings.yaml"

    def _entry(self):
        return {"id": "deepseek-harness",
                "config": {"path": "~/.dsh/settings.yaml", "format": "yaml-line",
                           "model_key": "agent-default-model.model"}}

    def test_key_path(self):
        from app.core import manager
        self.assertEqual(manager._yaml_model_path({"model_key": "a.b"}), ("a", "b"))
        self.assertEqual(manager._yaml_model_path({"model_key": "model"}), (None, "model"))
        self.assertEqual(manager._yaml_model_path({}), (None, "model"))

    def test_write_creates_file_and_section_when_missing(self):
        """dsh 自己不建 settings.yaml；写入必须能按需创建。"""
        from app.core import manager
        self.assertFalse(self.settings.exists())
        out = manager.write_model(self._entry(), "deepseek-flash")
        self.assertTrue(out["ok"], msg=out)
        self.assertEqual(out["model"], "deepseek-flash")
        text = self.settings.read_text(encoding="utf-8")
        self.assertIn("agent-default-model:", text)
        self.assertIn('model: "deepseek-flash"', text)
        self.assertFalse((self.home / ".dsh" / "settings.yaml.bak").exists())

    def test_write_quotes_bracket_model_name(self):
        """`[opencode]xxx` 以 [ 开头，未加引号在 YAML 里是非法的流序列。"""
        from app.core import manager
        out = manager.write_model(self._entry(), "[opencode]deepseek-v4-flash")
        self.assertTrue(out["ok"], msg=out)
        text = self.settings.read_text(encoding="utf-8")
        self.assertIn('model: "[opencode]deepseek-v4-flash"', text)
        # 回读必须拿回原值（不能带上引号）
        self.assertEqual(manager.read_model(self._entry()),
                         "[opencode]deepseek-v4-flash")

    def test_write_replaces_value_and_preserves_rest(self):
        from app.core import manager
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(
            "# 手写的注释\n"
            "agent-default-model:\n"
            "  provider: deepseek-official\n"
            '  model: "old-model"  # 行尾注释\n'
            "\n"
            "other-section:\n"
            "  model: \"untouched\"\n",
            encoding="utf-8")
        out = manager.write_model(self._entry(), "new-model")
        self.assertTrue(out["ok"], msg=out)
        text = self.settings.read_text(encoding="utf-8")
        self.assertIn('model: "new-model"', text)
        self.assertNotIn("old-model", text)
        # 其它段与注释不受影响
        self.assertIn("# 手写的注释", text)
        self.assertIn("provider: deepseek-official", text)
        self.assertIn("other-section:", text)
        self.assertIn('model: "untouched"', text)
        # 改动前留 .bak
        self.assertTrue((self.home / ".dsh" / "settings.yaml.bak").exists())
        self.assertEqual(manager.read_model(self._entry()), "new-model")

    def test_write_inserts_key_into_existing_section(self):
        from app.core import manager
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(
            "agent-default-model:\n"
            "  provider: deepseek-official\n"
            "zzz-last:\n"
            "  k: v\n",
            encoding="utf-8")
        out = manager.write_model(self._entry(), "m1")
        self.assertTrue(out["ok"], msg=out)
        text = self.settings.read_text(encoding="utf-8")
        self.assertEqual(manager.read_model(self._entry()), "m1")
        # 插在本段内（provider 之后），不能跑到别的段里去
        self.assertLess(text.index("provider: deepseek-official"), text.index('model: "m1"'))
        self.assertLess(text.index('model: "m1"'), text.index("zzz-last:"))

    def test_read_missing_file_is_none(self):
        from app.core import manager
        self.assertIsNone(manager.read_model(self._entry()))

    def test_read_strips_comments_and_single_quotes(self):
        from app.core import manager
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(
            "agent-default-model:\n"
            "  model: 'single-quoted' # 注释\n",
            encoding="utf-8")
        self.assertEqual(manager.read_model(self._entry()), "single-quoted")

    def test_write_preserves_crlf(self):
        from app.core import manager
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_bytes(
            b"agent-default-model:\r\n  model: \"old\"\r\nother:\r\n  k: v\r\n")
        out = manager.write_model(self._entry(), "new")
        self.assertTrue(out["ok"], msg=out)
        raw = self.settings.read_bytes()
        self.assertIn(b'  model: "new"\r\n', raw)
        self.assertTrue(raw.endswith(b"\r\n"))
        self.assertEqual(manager.read_model(self._entry()), "new")

    def test_config_writable_flag(self):
        """UI 靠 catalog_view().config_writable 决定是否给出「写入模型」入口。"""
        from app.core import manager, catalog
        entry = next(x for x in catalog.DEFAULT_CATALOG if x["id"] == "deepseek-harness")
        self.assertIn("yaml-line", manager._WRITABLE_FORMATS)
        # catalog_view 读的是磁盘清单，这里直接验证判定表达式对 dsh 行为 True
        self.assertIn(entry["config"]["format"], manager._WRITABLE_FORMATS)


class TestDeepseekBinding(BaseTest):
    """绑定解析：dsh 走 DEEPSEEK_*，且只接受 OpenAI 兼容端点。"""

    def _providers(self, modelhub):
        modelhub.upsert_provider({"name": "网关", "protocol": "openai",
                                  "base_url": "https://gw.test/v1", "api_key": FAKE_KEY})
        modelhub.upsert_provider({"name": "Anthropic网关", "protocol": "anthropic",
                                  "base_url": "https://a.test/v1", "api_key": FAKE_KEY2})
        return {p["name"]: p["id"] for p in modelhub.providers()}

    def test_openai_provider_injects_deepseek_env(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pids = self._providers(modelhub)
        modelhub.set_binding("deepseek-harness",
                             chain=[{"provider_id": pids["网关"], "model": "deepseek-flash"}])
        r = modelhub.resolve_binding("deepseek-harness")
        self.assertIsNotNone(r, msg="openai 协议应可绑定到 dsh")
        entry = r["call_chain"][0]
        self.assertEqual(entry["env"]["DEEPSEEK_API_KEY"], FAKE_KEY)
        self.assertEqual(entry["env"]["DEEPSEEK_BASE_URL"], "https://gw.test/v1")
        # dsh 不认 codex 的 -c provider 覆盖，也不能塞 ANTHROPIC_*
        self.assertNotIn("codex_provider", entry)
        self.assertNotIn("ANTHROPIC_BASE_URL", entry["env"])

    def test_anthropic_only_chain_is_not_bindable(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pids = self._providers(modelhub)
        modelhub.set_binding("deepseek-harness",
                             chain=[{"provider_id": pids["Anthropic网关"],
                                     "model": "claude-x"}])
        # anthropic 端点不是 OpenAI 兼容 /chat/completions → 判为不可绑定，回落 CLI 默认
        self.assertIsNone(modelhub.resolve_binding("deepseek-harness"))

    def test_chain_skips_anthropic_and_keeps_openai(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pids = self._providers(modelhub)
        modelhub.set_binding("deepseek-harness", chain=[
            {"provider_id": pids["Anthropic网关"], "model": "claude-x"},
            {"provider_id": pids["网关"], "model": "deepseek-flash"},
        ])
        r = modelhub.resolve_binding("deepseek-harness")
        self.assertIsNotNone(r)
        self.assertEqual(len(r["call_chain"]), 1)
        self.assertEqual(r["call_chain"][0]["env"]["DEEPSEEK_API_KEY"], FAKE_KEY)

    def test_other_cli_untouched(self):
        """回归护栏：claude/codex 的注入语义不能被 dsh 改动带偏。"""
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        pids = self._providers(modelhub)
        modelhub.set_binding("claude-code",
                             chain=[{"provider_id": pids["Anthropic网关"], "model": "claude-x"}])
        r = modelhub.resolve_binding("claude-code")
        self.assertEqual(r["call_chain"][0]["env"]["ANTHROPIC_BASE_URL"], "https://a.test/v1")
        self.assertEqual(r["call_chain"][0]["env"]["ANTHROPIC_MODEL"], "claude-x")
        self.assertNotIn("DEEPSEEK_API_KEY", r["call_chain"][0]["env"])

        modelhub.set_binding("codex-cli",
                             chain=[{"provider_id": pids["网关"], "model": "gpt-y"}])
        r2 = modelhub.resolve_binding("codex-cli")
        self.assertEqual(r2["call_chain"][0]["env"]["ORCH_API_KEY"], FAKE_KEY)
        self.assertEqual(r2["call_chain"][0]["codex_provider"]["base_url"],
                         "https://gw.test/v1")
        self.assertNotIn("DEEPSEEK_API_KEY", r2["call_chain"][0]["env"])


class TestDshArgv(BaseTest):
    """命令行构造：--profile headless + 提示词位置参数，且不塞 --model。"""

    def _agent(self, env=None):
        return {"id": "deepseek-harness", "kind": "generic", "mode": "real",
                "command": "dsh",
                "argv_template": ["--profile", "headless", "{prompt}"],
                "env": env or {}}

    def test_build_call_shape(self):
        from app.core import runner
        argv, stdin_text, _, _ = runner._build_call(
            self._agent(), "generic", "", False, "deepseek-flash", "写一段开场")
        # 命令段：Windows 上 dsh 是 .cmd 垫片 → resolve_command 包一层 cmd /c；
        # 未安装时退化为裸命令名。两种都接受，只锁「模板展开」的结果。
        self.assertTrue(any("dsh" in a.lower() for a in argv), msg=argv)
        self.assertEqual(argv[-3:], ["--profile", "headless", "写一段开场"])
        # generic 分支不追加 --model（dsh headless 会拒绝未知 flag）
        self.assertNotIn("--model", argv)
        # 模板带 {prompt} → 不改走 stdin
        self.assertIsNone(stdin_text)

    def test_resume_is_rejected_not_silently_ignored(self):
        """headless 无会话恢复：带 resume 必须明确报错，不能静默丢上下文。"""
        from app.core import runner
        out = runner.run_agent(self._agent(), "hi", resume="session-123",
                               readonly=True, timeout=30)
        self.assertFalse(out["ok"])
        self.assertIn("会话恢复", out["error"])

    def test_bind_agent_attaches_deepseek_env(self):
        """流水线实际入口：bind_agent 把 DEEPSEEK_* 挂到 agent 副本上。"""
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        modelhub.upsert_provider({"name": "网关", "protocol": "openai",
                                  "base_url": "https://gw.test/v1", "api_key": FAKE_KEY})
        pid = {p["name"]: p["id"] for p in modelhub.providers()}["网关"]
        modelhub.set_binding("deepseek-harness",
                             chain=[{"provider_id": pid, "model": "deepseek-flash"}])
        ba = modelhub.bind_agent(self._agent())
        self.assertEqual(ba["env"]["DEEPSEEK_API_KEY"], FAKE_KEY)
        self.assertEqual(ba["env"]["DEEPSEEK_BASE_URL"], "https://gw.test/v1")
        # 原 agent 不被就地改动
        self.assertEqual(self._agent()["env"], {})

    def test_env_reaches_child_process(self):
        """端到端：绑定来的 env 必须真的进到子进程环境里。"""
        import sys
        from pathlib import Path
        from app.core import modelhub, runner
        modelhub._FILE = self.data_dir / "models.json"
        modelhub.upsert_provider({"name": "网关", "protocol": "openai",
                                  "base_url": "https://gw.test/v1", "api_key": FAKE_KEY})
        pid = {p["name"]: p["id"] for p in modelhub.providers()}["网关"]
        modelhub.set_binding("deepseek-harness",
                             chain=[{"provider_id": pid, "model": "deepseek-flash"}])
        fixture = Path(__file__).parent / "fixtures_env_cli.py"
        agent = {"id": "deepseek-harness", "kind": "generic", "mode": "real",
                 "command": sys.executable,
                 "argv_template": [str(fixture), "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"]}
        # 本测试对象是 env 注入管道，不是配置同步闸：测试环境禁止写真实
        # ~/.dsh/settings.yaml（tmp_data_no_home_write 防毒闸），同步按新契约
        # 判 ENV_BLOCK 属预期行为，此处按同步成功放行（闸门本身由
        # test_upstream_identity.py 锁定）。
        with mock.patch("app.core.manager.sync_runtime_config", return_value=True):
            out = runner.run_agent(modelhub.bind_agent(agent), "", readonly=True, timeout=60)
        self.assertTrue(out["ok"], msg=out)
        self.assertIn("DEEPSEEK_API_KEY=%s" % FAKE_KEY, out["text"])
        self.assertIn("DEEPSEEK_BASE_URL=https://gw.test/v1", out["text"])


if __name__ == "__main__":
    import unittest
    unittest.main()
