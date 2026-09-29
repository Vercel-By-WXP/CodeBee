# -*- coding: utf-8 -*-
"""市场安检二波（AST10 #2 供应链对账）：内容指纹突变警示 + 近似名仿冒对账。

跑法：python -m unittest discover -s tests -p "test_market_supply.py" -v
"""
from __future__ import annotations

import unittest

from base import BaseTest

from app.core import market


def _files(body="这是一个普通的写作规范技能包。"):
    return {"market-testpack.md":
            "---\nname: 测试包\nsource: market\nmarket_id: testpack\n---\n\n" + body}


class TestPackDigest(BaseTest):

    def test_digest_stable_and_distinct(self):
        a = market._pack_digest(_files())
        b = market._pack_digest(_files())
        c = market._pack_digest(_files("完全不同的内容，混入了可疑指令。"))
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


class TestSupplyChainWarnings(BaseTest):

    def test_first_install_no_warning(self):
        res, err = market.install_files("testpack", "测试包", _files())
        self.assertIsNone(err, err)
        self.assertFalse(res["changed"])
        self.assertEqual(res["warnings"], [])
        self.assertTrue(res["digest"])

    def test_reinstall_changed_content_warns(self):
        market.install_files("testpack", "测试包", _files())
        res, err = market.install_files("testpack", "测试包", _files("升级后偷偷换了内容。"))
        self.assertIsNone(err, err)
        self.assertTrue(res["changed"])
        self.assertTrue(any("发生变化" in w for w in res["warnings"]), res["warnings"])

    def test_reinstall_same_content_no_warning(self):
        market.install_files("testpack", "测试包", _files())
        market.remove("testpack")                       # 卸载也骗不过 seen 对账
        res, err = market.install_files("testpack", "测试包", _files())
        self.assertIsNone(err, err)
        self.assertFalse(res["changed"])
        self.assertEqual(res["warnings"], [])

    def test_similar_name_flagged(self):
        market.install_files("trust", "ClawTrust 官方", _files())
        res, err = market.install_files("trust2", "ClawTrust 官方l", _files())
        self.assertIsNone(err, err)
        self.assertTrue(any("过近" in w and "ClawTrust" in w for w in res["warnings"]),
                        res["warnings"])

    def test_different_name_not_flagged(self):
        market.install_files("pack-a", "小说写作规范", _files())
        res, err = market.install_files("pack-b", "投资组合分析", _files())
        self.assertIsNone(err, err)
        self.assertEqual([w for w in res["warnings"] if "过近" in w], [])


if __name__ == "__main__":
    unittest.main()
