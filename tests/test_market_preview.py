# -*- coding: utf-8 -*-
"""外部目录「先看后装」两阶段安装测试：
  1) preview_remote：下载+剥离检查但不落盘安装，返回文件清单/技能/剥离项/
     完整性说明（zip 无 sha256=unverified，如实告知）；
  2) token 一次性安装：凭预览 token 安装那份已检内容不重复下载，装成功即焚；
  3) 伪 token 回落到正常下载通道；
  4) view 的 installed 过滤与 installed_total 全量计数（徽章与过滤无关）。

_fetch 整体 mock 成内存 zip，全程无网络。
"""
from __future__ import annotations

import io
import json
import zipfile
from unittest import mock

from base import BaseTest

ENTRY_ID = "remote-zcode-Demo-Skill"


def _demo_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("demo-skill/SKILL.md",
                    "---\nname: demo\ndescription: demo skill\n---\n# Demo\n正文内容。" * 40)
        zf.writestr("demo-skill/refs.md", "参考资料。" * 200)
        zf.writestr("scripts/setup.py", "print('pwn')")    # 可执行：应剥离
        zf.writestr("hooks/hook.js", "console.log(1)")     # 钩子：应剥离
        zf.writestr(".DS_Store", "junk")                   # 垃圾：忽略不计
    return buf.getvalue()


class TestMarketPreview(BaseTest):
    def _seed(self):
        """种一个 zcode 缓存目录（zip 直链、清单不带 sha256）。"""
        from app.core import market_remote
        d = market_remote._cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "zcode.json").write_text(json.dumps({
            "fetched_at": "2026-09-24 00:00:00",
            "url": "https://example.test/m.json",
            "catalog": {"name": "zcode", "description": "", "plugins": [{
                "name": "Demo Skill",
                "description": "demo skill pack",
                "source": {"type": "zip", "url": "https://example.test/demo.zip"},
            }]},
        }, ensure_ascii=False), encoding="utf-8")
        market_remote._PREVIEW_CACHE.clear()
        return market_remote

    def test_preview_then_token_install(self):
        market_remote = self._seed()
        from app.core import market
        with mock.patch.object(market_remote, "_fetch", return_value=_demo_zip()) as fx:
            p, err = market_remote.preview_remote(ENTRY_ID)
            self.assertIsNone(err, err)
            self.assertEqual(p["integrity"], "unverified")   # 清单没带哈希，如实告知
            self.assertTrue(p["token"])
            self.assertIn("Demo Skill", p["skills"])         # 主技能用条目名
            self.assertGreaterEqual(p["files_total"], 2)
            self.assertIn("scripts/setup.py", p["stripped"])
            self.assertIn("hooks/hook.js", p["stripped"])
            self.assertNotIn(".DS_Store", p["stripped"])     # 垃圾文件忽略，不算剥离
            # 预览阶段绝不落盘安装
            self.assertNotIn(ENTRY_ID, market.installed_ids())

            res, err = market_remote.install_remote(ENTRY_ID, p["token"])
            self.assertIsNone(err, err)
            self.assertEqual(fx.call_count, 1)    # token 命中：安装不再下载
            self.assertEqual(res["skills"], 1)
            self.assertIn(ENTRY_ID, market.installed_ids())
            self.assertIn("scripts/setup.py", res["stripped"])   # 安装回执也点名剥离
            # token 一次性：装成功即焚
            self.assertNotIn(p["token"], market_remote._PREVIEW_CACHE)

    def test_preview_cache_ttl(self):
        market_remote = self._seed()
        import time as _time
        with mock.patch.object(market_remote, "_fetch", return_value=_demo_zip()):
            p, err = market_remote.preview_remote(ENTRY_ID)
            self.assertIsNone(err, err)
            # 过期后缓存被清：token 失效回落正常下载
            market_remote._PREVIEW_CACHE[p["token"]]["ts"] = _time.time() - market_remote._PREVIEW_TTL - 1
            with mock.patch.object(market_remote, "_fetch", return_value=_demo_zip()) as fx2:
                res, err = market_remote.install_remote(ENTRY_ID, p["token"])
                self.assertIsNone(err, err)
                self.assertEqual(fx2.call_count, 1)
                self.assertIn(ENTRY_ID, __import__("app.core.market", fromlist=["x"]).installed_ids())

    def test_bogus_token_falls_back_to_download(self):
        market_remote = self._seed()
        with mock.patch.object(market_remote, "_fetch", return_value=_demo_zip()) as fx:
            res, err = market_remote.install_remote(ENTRY_ID, "deadbeef")
            self.assertIsNone(err, err)
            self.assertEqual(fx.call_count, 1)

    def test_installed_filter_and_total(self):
        market_remote = self._seed()
        v = market_remote.view()
        self.assertEqual(v["installed_total"], 0)
        self.assertTrue(v["entries"])
        with mock.patch.object(market_remote, "_fetch", return_value=_demo_zip()):
            res, err = market_remote.install_remote(ENTRY_ID, "")
            self.assertIsNone(err, err)
        v2 = market_remote.view()
        self.assertEqual(v2["installed_total"], 1)
        only = market_remote.view(installed=True)
        self.assertEqual(only["total"], 1)
        self.assertTrue(all(e["installed"] for e in only["entries"]))
        # installed_total 是未过滤前的全量数：开过滤后也不变
        self.assertEqual(only["installed_total"], 1)
        miss = market_remote.view(installed=True, q="不存在的关键词xyz")
        self.assertEqual(miss["total"], 0)

    def test_preview_unknown_entry(self):
        market_remote = self._seed()
        p, err = market_remote.preview_remote("remote-zcode-NoSuch")
        self.assertIsNone(p)
        self.assertTrue(err)
