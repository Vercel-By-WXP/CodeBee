# -*- coding: utf-8 -*-
"""qwen 注入器内闸回归（2026-09-24 戍边骑奴案）：anthropic 面 URL 绝不能
写进 OPENAI_BASE_URL——史上某版把 .../api/anthropic 写进去后，qwen 拿
openai 协议打 anthropic 路由，网关恒 404 被误判「网关限流」，每轮 run
起点白烧重试。外层 launch_pick 有协议闸，这里钉住注入器本体不复发。"""
from __future__ import annotations

import json
from pathlib import Path

from base import BaseTest


class TestQwenInjectorGuard(BaseTest):

    def _entry(self, home: Path):
        cfg = home / ".qwen" / "settings.json"
        return {"id": "qwencode", "config": {"path": str(cfg)}}, cfg

    def _prod_env(self):
        """生产形态：TUTTI_DATA 不重定向假家（mimo 用例同款）。"""
        import os
        saved_td = os.environ.pop("TUTTI_DATA", None)
        self.addCleanup(lambda: os.environ.__setitem__("TUTTI_DATA", saved_td)
                        if saved_td is not None else None)

    def test_anthropic_provider_refused_not_written(self):
        from app.core import manager
        self._prod_env()
        home = (self.tmp / "home-qwen-a").resolve()
        entry, cfg = self._entry(home)
        cfg.parent.mkdir(parents=True, exist_ok=True)
        before = '{"env": {"OPENAI_MODEL": "glm-5.3-flash"}}'
        cfg.write_text(before, encoding="utf-8")
        prov = {"id": "p1", "name": "Bigmodel", "protocol": "anthropic",
                "base_url": "https://open.bigmodel.cn/api/anthropic",
                "api_key": "k"}
        err = manager._sync_qwen_settings(entry, "glm-5.3-flash", prov)
        self.assertIsNotNone(err, "anthropic 面必须拒写")
        self.assertIn("anthropic", err)
        self.assertEqual(cfg.read_text(encoding="utf-8"), before,
                         "拒写时配置文件必须原样不动")

    def test_openai_provider_writes_env(self):
        from app.core import manager
        self._prod_env()
        home = (self.tmp / "home-qwen-b").resolve()
        entry, cfg = self._entry(home)
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('{"env": {"KEEP_ME": "1"}, "$version": 4}', encoding="utf-8")
        prov = {"id": "p2", "name": "维云", "protocol": "openai",
                "base_url": "https://vsllm.cc/v1", "api_key": "sk-ok"}
        err = manager._sync_qwen_settings(entry, "deepseek-v4", prov)
        self.assertIsNone(err, err)
        data = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertEqual(data["env"]["OPENAI_BASE_URL"], "https://vsllm.cc/v1")
        self.assertEqual(data["env"]["OPENAI_API_KEY"], "sk-ok")
        self.assertEqual(data["env"]["OPENAI_MODEL"], "deepseek-v4")
        self.assertEqual(data["env"]["KEEP_ME"], "1", "用户既有 env 键必须保留")


if __name__ == "__main__":
    unittest.main()
