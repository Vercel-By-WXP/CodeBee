# -*- coding: utf-8 -*-
"""沙箱闸回归（2026-10-05 实案：create_task 强制落默认策略后，89—96 批
连载起草被「没有经验证的 OS 文件隔离」整批拒死）。

锁定 _external_sandbox_block_reason 的四格语义：
1. codex 后端：维持原 fail-closed（策略必须恰好等于 workdir+允许网络）；
2. 非 codex 后端：默认策略（workdir 白名单+允许网络+无禁用）放行；
3. 非 codex 后端：策略收窄（禁网/禁工具/环境白名单/白名单不含工作目录）拒绝；
4. mock 永远放行。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))


class TestExternalSandboxGate(unittest.TestCase):

    def _reason(self, kind, sandbox, workdir=None):
        from app.core import pipeline
        wd = workdir or os.path.normpath(r"E:\book")
        return pipeline._external_sandbox_block_reason(
            {"kind": kind, "mode": "real"}, wd, sandbox)

    def test_non_codex_default_policy_passes(self):
        # create_task 强制落下的默认策略：workdir 白名单+允许网络+无禁用
        default = {"allowed_roots": [os.path.normpath(r"E:\book")],
                   "env_allowlist": [], "network": True,
                   "disabled_tools": []}
        self.assertEqual(self._reason("claude-code", default), "")
        self.assertEqual(self._reason("generic", default), "")

    def test_non_codex_narrowed_policy_rejected(self):
        # 白名单必须收窄到工作目录之内（如子目录）才能通过归一化并触发拒绝；
        # 指向工作目录之外的 allowed_roots 会被 normalize_sandbox 抛 ValueError
        sub = os.path.normpath(r"E:\book\sub")
        cases = [
            ({"allowed_roots": [sub], "network": True}, "白名单"),
            ({"allowed_roots": [], "network": False}, "禁网"),
            ({"disabled_tools": ["write"]}, "工具禁用"),
            ({"env_allowlist": ["PATH"]}, "环境变量"),
        ]
        for sandbox, expect in cases:
            reason = self._reason("claude-code", sandbox)
            self.assertIn(expect, reason, "收窄策略必须拒绝: %s" % sandbox)

    def test_codex_keeps_fail_closed(self):
        default = {"allowed_roots": [os.path.normpath(r"E:\book")],
                   "network": True, "disabled_tools": []}
        self.assertEqual(self._reason("codex", default), "")
        # codex 分支的校验逻辑本批未改动（原样保留 fail-closed 语义），
        # 收窄拒绝的构造依赖真实目录存在性，不在单测里展开。

    def test_mock_passes_anything(self):
        from app.core import pipeline
        self.assertEqual(pipeline._external_sandbox_block_reason(
            {"kind": "generic", "mode": "mock"},
            os.path.normpath(r"E:\book"), {"network": False}), "")


if __name__ == "__main__":
    unittest.main()
