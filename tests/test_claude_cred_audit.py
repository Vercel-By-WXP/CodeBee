# -*- coding: utf-8 -*-
"""凭据模式审计行与登录指引回归（2026-09-25 Mac 实案，通用化版本）。

纯模型绑定条目零凭据注入，claude -p 死在登录闸门（apiKeySource=none），
事后只能靠 init JSON 反推死因。加固三层且不限 claude：
① resolve_binding 给纯模型条目打 no_cred 标记（modelhub）；
② 起跑把凭据模式（绑定链注入/无注入/本机默认）钉进步骤日志审计头
   ——_cred_audit_note 按 no_cred/env/--settings 真值推导，密钥值绝不入日志；
③ 缺凭据+登录闸门措辞时按 CLI 附修复指引（_login_hint）。
"""
from base import BaseTest

FAKE_KEY = "sk-fake-" + "aaaaaaaa" * 3


class TestCredAuditNote(BaseTest):
    """_cred_audit_note 与 _build_call 注入行为的一致性（各 kind 通用）。"""

    def _agent(self, **kw):
        agent = {"id": "claude-cli", "kind": "claude", "mode": "real",
                 "command": "claude"}
        agent.update(kw)
        return agent

    def _cleanup(self, tmp_files):
        import os
        for tf in tmp_files or []:
            try:
                os.remove(tf)
            except OSError:
                pass

    def test_chain_env_lands_settings_and_chain_note(self):
        from app.core import runner as R
        att = self._agent(env={"ANTHROPIC_BASE_URL": "https://gw.example",
                               "ANTHROPIC_AUTH_TOKEN": FAKE_KEY})
        argv, _, _, tmp_files = R._build_call(
            att, "claude", "", False, "glm-5.3", "p")
        try:
            self.assertIn("--settings", argv)
            note = R._cred_audit_note("claude", att, argv)
            self.assertIn("绑定链", note)
            self.assertIn("--settings", note)
            # 密钥值绝不入审计行
            self.assertNotIn(FAKE_KEY, note)
        finally:
            self._cleanup(tmp_files)

    def test_local_mode_when_env_has_no_anthropic_keys(self):
        from app.core import runner as R
        # 完全无 env（无链回落）
        argv, _, _, _ = R._build_call(
            self._agent(), "claude", "", False, "glm-5.3", "p")
        self.assertNotIn("--settings", argv)
        note = R._cred_audit_note("claude", self._agent(), argv)
        self.assertIn("本机默认", note)
        self.assertIn("登录闸门", note)
        # env 只有非 ANTHROPIC 前缀键（别家 CLI 的注入约定）对 claude 不算注入
        att = self._agent(env={"ORCH_API_KEY": FAKE_KEY})
        argv2, _, _, _ = R._build_call(
            att, "claude", "", False, "glm-5.3", "p")
        self.assertNotIn("--settings", argv2)
        self.assertIn("本机默认", R._cred_audit_note("claude", att, argv2))

    def test_no_cred_entry_flagged(self):
        """纯模型条目（no_cred）单独点名：零注入、走本机登录态、必死预警。"""
        from app.core import runner as R
        att = {"env": {}, "no_cred": True, "model": "GLM-5.3"}
        note = R._cred_audit_note("claude", att, ["claude", "-p", "-m", "GLM-5.3"])
        self.assertIn("无注入", note)
        self.assertIn("纯模型条目", note)
        self.assertIn("登录闸门", note)
        # 有 env 的条目即使误带 no_cred，no_cred 语义优先（真值来自链解析层，
        # 两者同时出现说明条目构造有误——按更醒目的零凭据点名）
        att2 = {"env": {"OPENAI_API_KEY": FAKE_KEY}, "no_cred": True}
        self.assertIn("无注入", R._cred_audit_note("codex", att2, ["x"]))

    def test_generic_kind_env_note(self):
        """generic CLI（kimi 等）的链条目凭据注入也进审计行：只列变量名不列值。"""
        from app.core import runner as R
        att = {"env": {"ORCH_API_KEY": FAKE_KEY,
                       "OPENAI_BASE_URL": "https://gw.example/v1"}}
        note = R._cred_audit_note("kimi", att, ["kimi", "-p", "hi"])
        self.assertIn("绑定链注入", note)
        self.assertIn("OPENAI_BASE_URL", note)
        self.assertIn("ORCH_API_KEY", note)
        self.assertNotIn(FAKE_KEY, note)
        self.assertNotIn("gw.example", note)

    def test_run_process_writes_audit_head(self):
        import sys
        import tempfile
        from pathlib import Path
        from app.core import runner as R
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "step.log"
            res = R.run_process(argv=[sys.executable, "-c", "print('ok')"],
                                timeout=60, log_path=str(log),
                                audit_notes=["凭据=测试形态"])
            self.assertTrue(res["ok"])
            text = log.read_text(encoding="utf-8")
            self.assertIn("$ ", text)
            self.assertIn("· 凭据=测试形态", text)
            # 标记在审计头内（输出分隔线之前），不是混进 CLI 输出
            self.assertLess(text.index("· 凭据=测试形态"),
                            text.index("--- 输出 ---"))

    def test_no_notes_keeps_head_shape(self):
        import sys
        import tempfile
        from pathlib import Path
        from app.core import runner as R
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "step.log"
            res = R.run_process(argv=[sys.executable, "-c", "print('ok')"],
                                timeout=60, log_path=str(log))
            self.assertTrue(res["ok"])
            text = log.read_text(encoding="utf-8")
            self.assertNotIn("· 凭据=", text)


class TestNoCredPropagation(BaseTest):
    """no_cred 从 resolve_binding 到 _resolve_attempts 的透传链。"""

    def test_resolve_binding_marks_model_only_entry(self):
        from app.core import modelhub
        modelhub._FILE = self.data_dir / "models.json"
        # 纯模型条目（无 provider_id）——Mac claude 实案的链形态
        modelhub.set_binding("claude-code", chain=[{"model": "GLM-5.3"}])
        r = modelhub.resolve_binding("claude-code")
        self.assertTrue(r and r.get("call_chain"))
        entry = r["call_chain"][0]
        self.assertEqual(entry.get("model"), "GLM-5.3")
        self.assertTrue(entry.get("no_cred"))
        self.assertEqual(entry.get("env") or {}, {})
        # 对照：带供应商的条目不打 no_cred，纯模型条目照旧打标
        modelhub.upsert_provider({"name": "厂商A", "protocol": "anthropic",
                                  "base_url": "https://a.test/v1",
                                  "api_key": FAKE_KEY})
        pa = {p["name"]: p["id"] for p in modelhub.providers()}["厂商A"]
        modelhub.set_binding("claude-code", chain=[
            {"provider_id": pa, "model": "claude-x"},
            {"model": "GLM-5.3"}])
        r2 = modelhub.resolve_binding("claude-code")
        flags = [bool(e.get("no_cred")) for e in r2["call_chain"]]
        self.assertEqual(flags, [False, True])

    def test_resolve_attempts_passes_no_cred(self):
        from app.core import runner as R
        atts = R._resolve_attempts({"call_chain": [
            {"model": "GLM-5.3", "env": {}, "provider": None,
             "no_cred": True},
            {"model": "m2", "env": {"OPENAI_API_KEY": FAKE_KEY},
             "provider": {"id": "p"}, "provider_id": "p"}]})
        self.assertTrue(atts[0]["no_cred"])
        self.assertFalse(atts[1]["no_cred"])
        # 非链回落条目（本机默认）不误标
        atts2 = R._resolve_attempts({"model": "gpt", "provider": {}})
        self.assertFalse(atts2[0].get("no_cred"))
