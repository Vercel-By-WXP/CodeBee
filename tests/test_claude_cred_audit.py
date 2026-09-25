# -*- coding: utf-8 -*-
"""claude 凭据模式审计行回归（2026-09-25 Mac 实案）。

纯模型绑定条目零凭据注入，claude -p 死在登录闸门（apiKeySource=none），
事后只能靠 init JSON 反推死因。加固：起跑就把凭据模式（绑定链注入/
本机默认）钉进步骤日志审计头——argv 里有没有 --settings 是注入与否的
真值，密钥值绝不入日志。
"""
from base import BaseTest


class TestClaudeCredNote(BaseTest):
    """_claude_cred_note 与 _build_call 注入行为的一致性。"""

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
                               "ANTHROPIC_AUTH_TOKEN": "sk-test"})
        argv, _, _, tmp_files = R._build_call(
            att, "claude", "", False, "glm-5.3", "p")
        try:
            self.assertIn("--settings", argv)
            note = R._claude_cred_note(argv)
            self.assertIn("绑定链", note)
            self.assertIn("--settings", note)
        finally:
            self._cleanup(tmp_files)

    def test_local_mode_when_env_has_no_anthropic_keys(self):
        from app.core import runner as R
        # 完全无 env（无链回落/纯模型条目）
        argv, _, _, _ = R._build_call(
            self._agent(), "claude", "", False, "glm-5.3", "p")
        self.assertNotIn("--settings", argv)
        note = R._claude_cred_note(argv)
        self.assertIn("本机默认", note)
        self.assertIn("登录闸门", note)
        # env 只有非 ANTHROPIC 前缀键（别家 CLI 的注入约定）同样不算注入
        att = self._agent(env={"ORCH_API_KEY": "sk-x"})
        argv2, _, _, _ = R._build_call(
            att, "claude", "", False, "glm-5.3", "p")
        self.assertNotIn("--settings", argv2)
        self.assertIn("本机默认", R._claude_cred_note(argv2))

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
