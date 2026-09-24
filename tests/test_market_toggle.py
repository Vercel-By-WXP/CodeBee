# -*- coding: utf-8 -*-
"""插件管理形态对齐（借鉴 ZCode 插件页）单测：已装插件 enabled 字段与启停链路。

跑法：python -m unittest discover -s tests -p "test_market_toggle.py" -v
"""
from __future__ import annotations

import json

from base import BaseTest


def _reset_skills_cache():
    from app.core import skills
    with skills._LOCK:
        skills._user_pack_cache.clear()
        skills._user_dir_mtime["ts"] = 0.0
        skills._user_dir_mtime["ids"] = None


class MarketToggleTests(BaseTest):
    def test_view_has_enabled_field(self):
        """catalog 每项带 enabled：内置默认启用；未安装项 enabled=False。"""
        from app.core import market
        v = market.view()
        for it in v["catalog"]:
            self.assertIn("enabled", it)
        builtin = [x for x in v["catalog"] if x["builtin"]]
        self.assertTrue(all(x["enabled"] for x in builtin))   # 内置默认启用
        not_installed = [x for x in v["catalog"]
                         if not x["builtin"] and not x["installed"]]
        self.assertTrue(all(not x["enabled"] for x in not_installed))

    def test_toggle_roundtrip_via_pack_op(self):
        """安装 → 停用（enabled 翻 False）→ 启用（翻回 True）。"""
        from app.core import market, skills
        res, err = market.install("git-workflow")
        self.assertIsNone(err)
        _reset_skills_cache()
        target = str(self.data_dir / "skillpacks" / res["file"])
        pack_id = next(x for x in skills.user_packs()
                       if x["file"] == target)["id"]
        self.assertIsNone(skills.pack_op(pack_id, "disable"))
        _reset_skills_cache()
        card = next(x for x in market.view()["catalog"] if x["id"] == "git-workflow")
        self.assertTrue(card["installed"])
        self.assertFalse(card["enabled"])                     # 停用态可见
        self.assertIsNone(skills.pack_op(pack_id, "enable"))
        _reset_skills_cache()
        card = next(x for x in market.view()["catalog"] if x["id"] == "git-workflow")
        self.assertTrue(card["enabled"])                      # 启用态可见

    def test_disabled_pack_not_injected(self):
        """停用后市场卡片仍显示已安装，但任务注入不再带该插件内容。"""
        from app.core import market, skills
        market.install("git-workflow")
        _reset_skills_cache()
        pack_id = next(x for x in skills.user_packs())["id"]
        skills.pack_op(pack_id, "disable")
        block, used = skills.block_for({"type": "code"})
        self.assertNotIn(pack_id, used)                       # 停用包不参与注入
        self.assertNotIn("Git 提交与分支守则", block)


if __name__ == "__main__":
    import unittest as _u
    _u.main()
