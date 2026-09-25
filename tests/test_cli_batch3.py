# -*- coding: utf-8 -*-
"""第三批 CLI 接入（gemini-cli / codebuddy / trae-agent）：
catalog 条目形态、新条目补入、卸载命令推导、绑定协议闸（本机凭据模式）。

实测事实（2026-09，来源官方文档/源码，本机未装、无头形态为文档实证）：
- Gemini CLI：npm @google/gemini-cli，命令 gemini，无头 `gemini -p "<prompt>"`；
  默认模型 ~/.gemini/settings.json 顶层 model；凭据走个人 Google 账号 OAuth
  或 GOOGLE_API_KEY/GEMINI_API_KEY——Tutti 两种 wire env 它都不认。
- CodeBuddy Code：npm @tencent-ai/codebuddy-code，命令 cbc（codebuddy 别名），
  无头 `cbc -p "<prompt>"`（官方 codebuddy.ai/docs/cli）；配置 ~/.codebuddy/settings.json。
- trae-agent：字节开源，可执行名 trae-cli，PyPI 无包（pypi 404 实测），
  无头 `trae-cli run "<task>"`；配置 trae_config.yaml 默认找 cwd（TRAE_CONFIG_FILE
  可指定），模型是 agents.trae_agent.model 指向 models 表别名的两层引用。
- 三者第一版均为「本机凭据」模式：bindable_protocols 返回空协议集，绑定链/
  推荐链对它们不可建立（resolve_binding/recommend_binding 均回落 None），
  防止注入 CLI 不认的 env 造成链活着、模型走默认的静默假绑定。
"""
from __future__ import annotations

import json

from base import BaseTest

NEW_IDS = ("gemini-cli", "codebuddy", "trae-agent")


class TestCatalogEntries(BaseTest):

    def _entry(self, cid):
        from app.core import catalog
        return next(x for x in catalog.DEFAULT_CATALOG if x["id"] == cid)

    def test_gemini_entry_shape(self):
        e = self._entry("gemini-cli")
        self.assertEqual(e["detect"], {"cli": "gemini"})
        self.assertEqual(e["orch"]["kind"], "generic")
        self.assertEqual(e["orch"]["argv_template"], ["-p", "{prompt}"])
        # 默认模型：settings.json 顶层 model（官方支持的配置键）
        self.assertEqual(e["config"], {"path": "~/.gemini/settings.json",
                                       "format": "json", "model_key": "model"})
        self.assertIn("@google/gemini-cli", e["install"])
        self.assertFalse(e["default_enabled"])

    def test_codebuddy_entry_shape(self):
        e = self._entry("codebuddy")
        # 可执行名是 cbc（官方简写），不是 codebuddy
        self.assertEqual(e["detect"], {"cli": "cbc"})
        self.assertEqual(e["orch"]["command"], "cbc")
        self.assertEqual(e["orch"]["argv_template"], ["-p", "{prompt}"])
        self.assertEqual(e["config"]["path"], "~/.codebuddy/settings.json")
        self.assertIn("@tencent-ai/codebuddy-code", e["install"])

    def test_trae_entry_shape(self):
        e = self._entry("trae-agent")
        # 可执行名是 trae-cli，无头是 run 子命令 + 任务位置参数
        self.assertEqual(e["detect"], {"cli": "trae-cli"})
        self.assertEqual(e["orch"]["command"], "trae-cli")
        self.assertEqual(e["orch"]["argv_template"], ["run", "{prompt}"])
        # 模型是两层引用（agents.trae_agent.model → models 表别名）且真源在
        # cwd 的 trae_config.yaml——CodeBee 不托管落盘（qwencode 同款 format=None）
        self.assertIsNone(e["config"]["format"])
        self.assertIsNone(e["config"]["model_key"])
        # PyPI 无包：安装走 uv tool + git 直装；显式 uninstall 防推导成 git+URL
        self.assertIn("uv tool install", e["install"])
        self.assertIn("git+https://github.com/bytedance/trae-agent", e["install"])
        self.assertEqual(e["uninstall"], "uv tool uninstall trae-agent")

    def test_all_declare_no_resume(self):
        """三者无头形态均未实证会话恢复 → 不得声明 resume 模板。"""
        for cid in NEW_IDS:
            e = self._entry(cid)
            self.assertNotIn("resume_argv_template", e["orch"], msg=cid)

    def test_launch_patch_applies(self):
        from app.core import catalog
        entries = catalog.load()
        launch = {x["id"]: x.get("launch") for x in entries}
        for cid, cmd in (("gemini-cli", "gemini"), ("codebuddy", "cbc"),
                         ("trae-agent", "trae-cli")):
            self.assertEqual(launch.get(cid), {"kind": "console", "command": cmd}, msg=cid)


class TestMergeNewDefaults(BaseTest):

    def test_new_ids_merge_into_existing_catalog(self):
        from app.core import catalog
        entries = [{"id": "codex-cli", "custom": True}]
        catalog._merge_new_defaults(entries)
        ids = [x["id"] for x in entries]
        for cid in NEW_IDS:
            self.assertIn(cid, ids)
        # 用户条目原样保留 + 幂等
        self.assertEqual(entries[0], {"id": "codex-cli", "custom": True})
        n = len(entries)
        catalog._merge_new_defaults(entries)
        self.assertEqual(len(entries), n)

    def test_merged_entries_not_aliased(self):
        from app.core import catalog
        entries = []
        catalog._merge_new_defaults(entries)
        merged = next(x for x in entries if x["id"] == "gemini-cli")
        merged["orch"]["argv_template"].append("TAMPERED")
        pristine = next(d for d in catalog.DEFAULT_CATALOG if d["id"] == "gemini-cli")
        self.assertNotIn("TAMPERED", pristine["orch"]["argv_template"])


class TestUninstallDerive(BaseTest):

    def test_npm_scoped_packages(self):
        from app.core import catalog
        for cid, pkg in (("gemini-cli", "@google/gemini-cli"),
                         ("codebuddy", "@tencent-ai/codebuddy-code")):
            e = next(x for x in catalog.DEFAULT_CATALOG if x["id"] == cid)
            self.assertEqual(catalog.uninstall_command(e),
                             "npm uninstall -g %s" % pkg, msg=cid)

    def test_trae_explicit_uninstall_wins(self):
        """git+URL 形态 derive_uninstall 会取末尾 token 当包名（推出 git+https://…），
        trae 的显式 uninstall 字段必须优先于推导。"""
        from app.core import catalog
        e = next(x for x in catalog.DEFAULT_CATALOG if x["id"] == "trae-agent")
        self.assertEqual(catalog.uninstall_command(e), "uv tool uninstall trae-agent")
        # 护栏：推导结果确实是错的那个形态（这条不成立时上面断言就没意义了）
        self.assertNotEqual(catalog.derive_uninstall(e["install"]),
                            "uv tool uninstall trae-agent")


class TestLocalCredOnlyBinding(BaseTest):
    """本机凭据模式：三新 CLI 不吃 Tutti 的两种 wire env，绑定链/推荐链必须
    建不起来（None → 回落 CLI 登录态），而不是注入无效 env 假装绑上了。"""

    def setUp(self):
        super().setUp()
        from app.core import modelhub
        modelhub.upsert_provider({"name": "网关", "protocol": "openai",
                                  "base_url": "https://gw.test/v1", "api_key": "sk-t"})
        modelhub.upsert_provider({"name": "A网关", "protocol": "anthropic",
                                  "base_url": "https://a.test/v1", "api_key": "sk-a"})

    def test_bindable_protocols_empty(self):
        from app.core import modelhub
        for cid in NEW_IDS:
            self.assertEqual(modelhub.bindable_protocols(cid), (), msg=cid)
        # 既有 CLI 不受牵连
        self.assertEqual(modelhub.bindable_protocols("codex-cli"), ("openai",))
        self.assertEqual(modelhub.bindable_protocols("claude-code"), ("anthropic",))

    def test_resolve_binding_falls_back_to_none(self):
        from app.core import modelhub
        pid = {p["name"]: p["id"] for p in modelhub.providers()}["网关"]
        modelhub.set_binding("gemini-cli",
                             chain=[{"provider_id": pid, "model": "gpt-x"}])
        self.assertIsNone(modelhub.resolve_binding("gemini-cli"))

    def test_recommend_binding_returns_none(self):
        from app.core import modelhub
        for cid in NEW_IDS:
            self.assertIsNone(modelhub.recommend_binding(cid), msg=cid)

    def test_dead_msg_names_local_cred_mode(self):
        from app.core import modelhub
        msg = modelhub.binding_dead_msg("codebuddy")
        self.assertIn("本机登录态", msg)
        # 不能再出现「仅接受  协议」的空话（空协议集会渲染成残缺句子）
        self.assertNotIn("仅接受", msg)


class TestArgvShape(BaseTest):
    """generic 模板展开：-p / run 子命令形态，且不追加 --model、不走 stdin。"""

    def _agent(self, cid, command, tmpl):
        return {"id": cid, "kind": "generic", "mode": "real",
                "command": command, "argv_template": tmpl, "env": {}}

    def test_gemini_prompt_flag(self):
        from app.core import runner
        argv, stdin_text, _, _ = runner._build_call(
            self._agent("gemini-cli", "gemini", ["-p", "{prompt}"]),
            "generic", "", False, "gemini-2.5-pro", "写一段开场")
        self.assertTrue(any(a.lower() == "gemini" for a in argv), msg=argv)
        self.assertEqual(argv[-2:], ["-p", "写一段开场"])
        self.assertNotIn("--model", argv)
        self.assertIsNone(stdin_text)

    def test_codebuddy_prompt_flag(self):
        from app.core import runner
        argv, stdin_text, _, _ = runner._build_call(
            self._agent("codebuddy", "cbc", ["-p", "{prompt}"]),
            "generic", "", False, "claude-x", "评审这一章")
        self.assertEqual(argv[-2:], ["-p", "评审这一章"])
        self.assertIsNone(stdin_text)

    def test_trae_run_subcommand(self):
        from app.core import runner
        argv, stdin_text, _, _ = runner._build_call(
            self._agent("trae-agent", "trae-cli", ["run", "{prompt}"]),
            "generic", "", False, "claude-sonnet", "修一个 bug")
        self.assertEqual(argv[-2:], ["run", "修一个 bug"])
        self.assertNotIn("--model", argv)
        self.assertIsNone(stdin_text)
