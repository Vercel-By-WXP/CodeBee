# -*- coding: utf-8 -*-
"""手工添加模型：厂商列表接口调不通（或只返回部分）时的通路。

要点：
- 不要求密钥、不发网络请求；models 为 null（从未拉到列表）的供应商也能加。
- manual 标记：之后刷新即使厂商列表里没有它也保留，不被静默清掉；进了列表也透传标记。
- 重名不重复加（无副作用）；删除墓碑视同恢复；新模型排启用块末尾不抢 #1。
所有模型名均为运行时构造的假值，无真实凭据。
"""
from __future__ import annotations

from base import BaseTest


class TestManualAdd(BaseTest):
    def _prov(self, modelhub, **kw):
        entry = {"name": "网关", "protocol": "openai",
                 "base_url": "https://a.test/v1", "api_key": "sk-test-" + "1" * 24}
        entry.update(kw)
        modelhub.upsert_provider(entry)
        return modelhub.providers()[0]["id"]

    def _seed_models(self, modelhub, pid, models):
        """直接种盘模型列表（绕过网络拉取）。"""
        data = modelhub._load()
        for p in data["providers"]:
            if p["id"] == pid:
                p["models"] = models
        modelhub._save(data)

    def test_add_into_unfetched_provider(self):
        """models 为 null（从未拉到列表）也能加：启用、manual 标记、排 #1（仅有的模型）。"""
        from app.core import modelhub
        pid = self._prov(modelhub)
        self.assertIsNone(modelhub.providers()[0].get("models"))
        n, err = modelhub.add_model_manual(pid, "  deepseek-chat \n")
        self.assertEqual(err, "")
        self.assertEqual(n, 1)
        prov = modelhub.providers()[0]
        m = prov["models"][0]
        self.assertEqual(m["name"], "deepseek-chat")      # 首尾空白已剥
        self.assertTrue(m["enabled"])
        self.assertTrue(m["manual"])
        self.assertEqual(m["priority"], 1)
        self.assertNotIn("models_fetched_at", prov)       # 手工加不算「已拉取」

    def test_validation_errors(self):
        from app.core import modelhub
        pid = self._prov(modelhub)
        for bad in ("", "   "):
            n, err = modelhub.add_model_manual(pid, bad)
            self.assertTrue(err, "空名应报错")
        n, err = modelhub.add_model_manual(pid, "a\r\nb")  # 换行拒绝
        self.assertTrue(err)
        n, err = modelhub.add_model_manual("nope", "m1")   # 供应商不存在
        self.assertEqual(err, "供应商不存在")

    def test_duplicate_visible_no_side_effect(self):
        """重名不重复加、不改既有条目。"""
        from app.core import modelhub
        pid = self._prov(modelhub)
        self._seed_models(modelhub, pid, [
            {"name": "m1", "enabled": False, "priority": 1}])
        n, err = modelhub.add_model_manual(pid, "m1")
        self.assertEqual(err, "模型已存在")
        m = {x["name"]: x for x in modelhub.providers()[0]["models"]}["m1"]
        self.assertFalse(m["enabled"])                    # 停用状态没被顺手改掉
        self.assertNotIn("manual", m)
        self.assertEqual(n, 1)

    def test_add_restores_hidden_tombstone(self):
        """名字撞上删除墓碑：视同恢复（可见、启用、补 manual 标记）。"""
        from app.core import modelhub
        pid = self._prov(modelhub)
        self._seed_models(modelhub, pid, [
            {"name": "m1", "enabled": False, "priority": 1, "hidden": True}])
        n, err = modelhub.add_model_manual(pid, "m1")
        self.assertEqual(err, "")
        self.assertEqual(n, 1)
        m = modelhub.providers()[0]["models"][0]
        self.assertFalse(m["hidden"])
        self.assertTrue(m["enabled"])
        self.assertTrue(m["manual"])

    def test_add_lands_at_end_of_enabled_block(self):
        """新模型排启用块末尾：参与编排但不抢 #1 默认模型，停用块仍在最后。"""
        from app.core import modelhub
        pid = self._prov(modelhub)
        self._seed_models(modelhub, pid, [
            {"name": "m1", "enabled": True, "priority": 1},
            {"name": "m2", "enabled": False, "priority": 2},
            {"name": "m3", "enabled": True, "priority": 3}])
        _, err = modelhub.add_model_manual(pid, "mz")
        self.assertEqual(err, "")
        # 排序语义真源是 priority（UI/运行时都按它排），不是原始列表位置
        names = [m["name"] for m in modelhub._ranked(modelhub.providers()[0]["models"])]
        self.assertEqual(names, ["m1", "m3", "mz", "m2"])

    def test_refresh_keeps_manual_model_missing_from_list(self):
        """核心保障：刷新时厂商列表里没有手工模型，也不许被清掉。"""
        from app.core import modelhub
        pid = self._prov(modelhub)
        self._seed_models(modelhub, pid, [
            {"name": "m1", "enabled": True, "priority": 1},
            {"name": "mz", "enabled": True, "priority": 2, "manual": True,
             "image_in": False}])
        orig = modelhub._fetch_models_http
        try:
            modelhub._fetch_models_http = lambda *a, **k: (["m1"], "")
            n, err = modelhub.refresh_models(pid)
            self.assertEqual(err, "")
            after = {m["name"]: m for m in modelhub.providers()[0]["models"]}
            self.assertIn("mz", after)
            self.assertTrue(after["mz"]["manual"])
            self.assertTrue(after["mz"]["enabled"])
            # 停用的手工模型同样保留
            self._seed_models(modelhub, pid, [
                {"name": "m1", "enabled": True, "priority": 1},
                {"name": "mz", "enabled": False, "priority": 2, "manual": True}])
            modelhub.refresh_models(pid)
            after = {m["name"]: m for m in modelhub.providers()[0]["models"]}
            self.assertIn("mz", after)
            self.assertFalse(after["mz"]["enabled"])
        finally:
            modelhub._fetch_models_http = orig

    def test_refresh_preserves_manual_flag_when_listed(self):
        """手工模型后来进了厂商列表：manual 标记不能在重建数组时被抹掉。"""
        from app.core import modelhub
        pid = self._prov(modelhub)
        self._seed_models(modelhub, pid, [
            {"name": "mz", "enabled": True, "priority": 1, "manual": True}])
        orig = modelhub._fetch_models_http
        try:
            modelhub._fetch_models_http = lambda *a, **k: (["mz", "m9"], "")
            modelhub.refresh_models(pid)
            after = {m["name"]: m for m in modelhub.providers()[0]["models"]}
            self.assertTrue(after["mz"]["manual"])
            self.assertFalse(after["m9"].get("manual"))   # 拉来的不算手工
            self.assertFalse(after["mz"]["hidden"])
        finally:
            modelhub._fetch_models_http = orig
