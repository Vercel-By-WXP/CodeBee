# -*- coding: utf-8 -*-
"""mimo 注入器回归（2026-09-24 禅道双单实案）：Key 唯一通道=配置内 provider 块。
此前 mimo 绑定后仍裸奔（env 全不认 + 配置无 key），评审连续 20+ 轮
Invalid API Key 而根因不在 Key。路径一律 pathlib resolve+parents 守卫。"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from base import BaseTest


class TestMimoInjector(BaseTest):

    def _entry(self, home: Path):
        cfg = home / ".config" / "mimocode" / "mimocode.jsonc"
        return {"id": "mimo-code", "config": {"path": str(cfg)}}, cfg

    def test_writes_provider_block_and_qualified_model(self):
        import os
        from app.core import manager
        saved_td = os.environ.pop("TUTTI_DATA", None)   # 生产形态（假家在临时目录下）
        self.addCleanup(lambda: os.environ.__setitem__("TUTTI_DATA", saved_td)
                        if saved_td is not None else None)
        home = (self.tmp / "home").resolve()
        entry, cfg = self._entry(home)
        cfg.parent.mkdir(parents=True, exist_ok=True)
        # 预置用户配置（含注释与既有键）：注入必须只动目标片段
        cfg.write_text('{\n  // 用户注释：别丢\n'
                       '  "$schema": "https://mimo.xiaomi.com/mimocode/config.json",\n'
                       '  "model": "mimo-v2.5"\n}\n', encoding="utf-8")
        prov = {"id": "p1", "name": "维云", "protocol": "openai",
                "base_url": "https://vsllm.cc/v1", "api_key": "sk-test-123"}
        err = manager._sync_mimo_settings(entry, "deepseek-v4", prov)
        self.assertIsNone(err, err)
        text = cfg.read_text(encoding="utf-8")
        self.assertIn("// 用户注释：别丢", text, "用户注释必须保留")
        stripped = []
        for ln in text.splitlines():
            stripped.append("" if ln.lstrip().startswith("//") else ln)
        data = json.loads("\n".join(stripped))   # 只剥整行注释（URL 里有 //）
        blk = data["provider"]["codebee"]
        self.assertEqual(blk["npm"], "@ai-sdk/openai-compatible")
        self.assertEqual(blk["options"]["baseURL"], "https://vsllm.cc/v1")
        self.assertEqual(blk["options"]["apiKey"], "sk-test-123")
        self.assertIn("deepseek-v4", blk["models"])
        self.assertEqual(data["model"], "codebee/deepseek-v4")
        # 幂等：再写一次不重复不报错
        self.assertIsNone(manager._sync_mimo_settings(entry, "deepseek-v4", prov))
        self.assertEqual(cfg.read_text(encoding="utf-8").count('"codebee"'), 1)

    def test_anthropic_provider_rejected(self):
        import os
        from app.core import manager
        saved_td = os.environ.pop("TUTTI_DATA", None)
        self.addCleanup(lambda: os.environ.__setitem__("TUTTI_DATA", saved_td)
                        if saved_td is not None else None)
        home = (self.tmp / "home2").resolve()
        entry, _cfg = self._entry(home)
        prov = {"id": "p2", "name": "Bigmodel", "protocol": "anthropic",
                "base_url": "https://x/api/anthropic", "api_key": "k"}
        err = manager._sync_mimo_settings(entry, "glm", prov)
        self.assertIn("OpenAI 兼容", err)

    def test_tmpdata_guard_refuses(self):
        from app.core import manager
        home = (self.tmp / "home3").resolve()
        entry, _cfg = self._entry(home)
        # BaseTest 已设 TUTTI_DATA 在临时目录 + 真实主目录 → 防毒闸拦截
        err = manager._sync_mimo_settings(
            entry, "m", {"id": "p", "protocol": "openai",
                         "base_url": "https://x/v1", "api_key": "k"})
        self.assertIn("已拦截", err)
