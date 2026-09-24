# -*- coding: utf-8 -*-
"""模型模态能力（image_in）测试：名字启发式、set_model_caps、refresh 透传。

models 条目新平铺字段 image_in（bool，缺省 falsy=纯文本）：名字启发式预填只发生在
新模型首次进列表时；用户手动设置后 refresh 不得抹掉——与 hidden 墓碑同级的透传纪律
（refresh 重建 dict 时只保留白名单字段，漏透传 = 刷新一次用户声明全丢）。
"""
from __future__ import annotations

from base import BaseTest


class TestModelCaps(BaseTest):
    def _seed(self, modelhub, models):
        modelhub._FILE = self.data_dir / "models.json"
        modelhub._save({"providers": [{
            "id": "prov-caps", "name": "网关", "protocol": "openai",
            "base_url": "https://a.test/v1", "api_key": "sk-test",
            "enabled": True, "models": models}], "bindings": {}})
        return "prov-caps"

    def test_auto_image_in_heuristic(self):
        from app.core import modelhub
        # claude/gemini 全系原生多模态；显式命名段（vl/vision/数字+v）命中
        self.assertTrue(modelhub._auto_image_in("claude-sonnet-4-5"))
        self.assertTrue(modelhub._auto_image_in("gemini-2.5-flash"))
        self.assertTrue(modelhub._auto_image_in("qwen2.5-vl-72b"))
        self.assertTrue(modelhub._auto_image_in("GLM-4V"))
        self.assertTrue(modelhub._auto_image_in("internvl2-8b"))
        # 普通文本模型不误标
        self.assertFalse(modelhub._auto_image_in("glm-5.3-flash"))
        self.assertFalse(modelhub._auto_image_in("deepseek-chat"))
        self.assertFalse(modelhub._auto_image_in("kimi-k2"))
        self.assertFalse(modelhub._auto_image_in(""))

    def test_set_and_query(self):
        from app.core import modelhub
        pid = self._seed(modelhub, [
            {"name": "glm-5.3-flash", "enabled": True, "priority": 1,
             "hidden": False}])
        prov = modelhub.providers()[0]
        self.assertFalse(modelhub._model_image_in(prov, "glm-5.3-flash"))   # 缺省=纯文本
        self.assertFalse(modelhub._model_image_in(prov, "不存在"))           # 查不到=False
        # 开 → 落盘 → 再查为真；关 → 回落
        self.assertIsNone(modelhub.set_model_caps(pid, "glm-5.3-flash", True))
        self.assertTrue(next(m for m in modelhub.providers()[0]["models"]
                             if m["name"] == "glm-5.3-flash")["image_in"])
        self.assertTrue(modelhub._model_image_in(modelhub.providers()[0],
                                                 "glm-5.3-flash"))
        self.assertIsNone(modelhub.set_model_caps(pid, "glm-5.3-flash", False))
        self.assertFalse(modelhub._model_image_in(modelhub.providers()[0],
                                                  "glm-5.3-flash"))
        # 供应商/模型不存在 → 错误文案（非异常）
        self.assertIn("不存在", modelhub.set_model_caps("nope", "m", True))
        self.assertIn("不存在", modelhub.set_model_caps(pid, "nope", True))

    def test_refresh_preserves_manual_and_prefills_new(self):
        """refresh 不抹手工声明（透传纪律），新模型按名字预填。"""
        from app.core import modelhub
        pid = self._seed(modelhub, [
            {"name": "glm-5.3-flash", "enabled": True, "priority": 1,
             "hidden": False, "image_in": True},          # 用户手工开的
            {"name": "deepseek-chat", "enabled": True, "priority": 2,
             "hidden": False}])                            # 没声明过（缺字段）
        orig = modelhub._fetch_models_http
        modelhub._fetch_models_http = lambda *a, **k: (
            ["glm-5.3-flash", "deepseek-chat", "qwen2.5-vl-72b"], "")
        try:
            n, err = modelhub.refresh_models(pid)
        finally:
            modelhub._fetch_models_http = orig
        self.assertEqual(err, "")
        by_name = {m["name"]: m for m in modelhub.providers()[0]["models"]}
        self.assertTrue(by_name["glm-5.3-flash"]["image_in"])     # 手工声明不被刷新抹掉
        self.assertFalse(by_name["deepseek-chat"]["image_in"])    # 缺字段透传为 False
        self.assertTrue(by_name["qwen2.5-vl-72b"]["image_in"])    # 新模型名字预填
