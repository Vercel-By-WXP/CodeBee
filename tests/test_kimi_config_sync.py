# -*- coding: utf-8 -*-
"""kimi-code 配置注入回归（schema 反推自官方 bundle，2026-09-17）。

要点：顶层 camelCase 键（defaultProvider/defaultModel/yolo）必须落在任何
[表] 之前（TOML 语义），托管块幂等重写不重复，用户自有内容保留。
"""
from __future__ import annotations

import os

from base import BaseTest


class TestKimiConfigSync(BaseTest):
    def runTest(self):
        from app.core import manager
        prov = {"id": "prov-x", "name": "讯飞星火", "protocol": "openai",
                "base_url": "https://x.example/v2", "api_key": "sk-9"}
        entry = {"id": "kimi-code"}
        cfg = os.path.expanduser("~/.kimi-code/config.toml")

        # 不动真实用户文件：临时换 HOME 级路径不可行，改直接测文本组装逻辑
        text1 = manager._kimi_render(prov, "qwen36")
        self.assertIn('defaultProvider = "orch"', text1)
        self.assertIn("yolo = true", text1)
        self.assertIn("[providers.orch]", text1)
        self.assertIn('[models."qwen36"]', text1)
        self.assertIn("maxContextSize = 131072", text1)
        # 顶层键必须出现在任何 [表] 之前
        self.assertLess(text1.index("defaultProvider"),
                        text1.index("[providers.orch]"))
        # 幂等：同一内容再渲染无差异；anthropic 协议映射 type
        self.assertEqual(manager._kimi_render(prov, "qwen36"), text1)
        pa = dict(prov, protocol="anthropic")
        text2 = manager._kimi_render(pa, "m2")
        self.assertIn('type = "anthropic"', text2)
