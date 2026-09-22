# -*- coding: utf-8 -*-
"""市场装后冒烟验证（SkillForge 证据驱动借鉴）单测。

跑法：python -m unittest discover -s tests -p "test_market_smoke.py" -v
契约：install_files 返回带 smoke 字段——ok:N字（解析成功）/ 人话失败原因；
market.json 记录同步落 smoke。
"""
from __future__ import annotations

import json

from base import BaseTest


class SmokeTests(BaseTest):
    def _reset(self):
        from app.core import skills
        with skills._LOCK:
            skills._user_pack_cache.clear()
            skills._user_dir_mtime["ts"] = 0.0
            skills._user_dir_mtime["ids"] = None

    def test_install_returns_smoke_ok(self):
        from app.core import market
        res, err = market.install("git-workflow")
        self.assertIsNone(err)
        self.assertTrue(res["smoke"].startswith("ok:"), res["smoke"])
        self.assertIn("字", res["smoke"])
        # 记账同步落 smoke
        reg = json.loads((self.data_dir / "market.json").read_text(encoding="utf-8"))
        self.assertTrue(reg["installed"]["git-workflow"]["smoke"].startswith("ok:"))

    def test_bad_frontmatter_smoke_reports(self):
        """正文缺 frontmatter（名字对不上）→ smoke 人话失败而非静默。"""
        from app.core import market
        res, err = market.install_files(
            "smoke-bad", "期望名字",
            {"market-smoke-bad.md": "（无 frontmatter，名字解析不到期望值）" * 5})
        self.assertIsNone(err)
        self.assertTrue(res["ok"])
        self.assertIn("冒烟", res["smoke"])          # 有具体人话原因
        self.assertFalse(res["smoke"].startswith("ok:"))

    def test_empty_body_smoke_reports(self):
        from app.core import market
        res, err = market.install_files(
            "smoke-empty", "空包",
            {"market-smoke-empty.md": "---\nname: 空包\n---\n"})
        self.assertIsNone(err)
        self.assertIn("冒烟", res["smoke"])
        self.assertFalse(res["smoke"].startswith("ok:"))

    def test_smoke_does_not_block_install(self):
        """冒烟失败只记账提示，不拦截安装（提示不拦阻，与危险扫描同纪律）。"""
        from app.core import market
        res, err = market.install_files(
            "smoke-nokey", "又个坏包",
            {"market-smoke-nokey.md": "---\nname: 别的名字\n---\n正文" * 3})
        self.assertIsNone(err)
        self.assertTrue(res["ok"])                    # 安装本身成功
        self.assertTrue((self.data_dir / "skillpacks" / "market-smoke-nokey.md").is_file())


if __name__ == "__main__":
    import unittest
    unittest.main()
