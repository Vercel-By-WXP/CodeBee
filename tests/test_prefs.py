# -*- coding: utf-8 -*-
"""偏好记忆（prefs，借鉴 chinese-novelist-skill）单测：记录/钳制/损坏容错。"""
from __future__ import annotations

from base import BaseTest


class PrefsTests(BaseTest):
    def test_record_and_load(self):
        from app.core import prefs
        prefs.record({"type": "serial_novel", "rounds": 3, "threshold": 8.0,
                      "mode": "expert", "thinking": "high",
                      "goal": "绝不能记进来的目标内容", "title": "标题也不记"})
        p = prefs.load()
        self.assertEqual(p["type"], "serial_novel")
        self.assertEqual(p["rounds"], 3)
        self.assertEqual(p["threshold"], 8.0)
        self.assertEqual(p["mode"], "expert")
        self.assertEqual(p["thinking"], "high")
        self.assertNotIn("goal", p)     # 只记参数不记内容
        self.assertNotIn("title", p)

    def test_partial_update_keeps_other_keys(self):
        from app.core import prefs
        prefs.record({"type": "code", "rounds": 2})
        prefs.record({"threshold": 8.5})           # 只更新一个键
        p = prefs.load()
        self.assertEqual(p["type"], "code")        # 旧键保留
        self.assertEqual(p["threshold"], 8.5)

    def test_clamp_out_of_range(self):
        from app.core import prefs
        prefs.record({"threshold": 99, "rounds": -3, "chapters": 10 ** 6})
        p = prefs.load()
        self.assertEqual(p["threshold"], 10.0)
        self.assertEqual(p["rounds"], 1)
        self.assertEqual(p["chapters"], 2000)

    def test_garbage_payload_ignored(self):
        from app.core import prefs
        prefs.record(None)
        prefs.record({"rounds": "abc", "mode": "turbo", "thinking": "maximum"})
        self.assertEqual(prefs.load(), {})

    def test_serial_params_roundtrip(self):
        from app.core import prefs
        prefs.record({"type": "serial_novel", "chapters": 12, "words_per_chapter": 3000,
                      "variants": 2})
        p = prefs.load()
        self.assertEqual(p["chapters"], 12)
        self.assertEqual(p["words_per_chapter"], 3000)
        self.assertEqual(p["variants"], 2)

    def test_direct_model_prefs_recorded(self):
        """direct 类型对话模型偏好（2026-09-23 补全）：厂商/模型/推理档被记住。"""
        from app.core import prefs
        prefs.record({"type": "direct", "direct_provider": "prov-a",
                      "direct_model": "glm-5.3", "direct_thinking": "high"})
        p = prefs.load()
        self.assertEqual(p["direct_provider"], "prov-a")
        self.assertEqual(p["direct_model"], "glm-5.3")
        self.assertEqual(p["direct_thinking"], "high")

    def test_direct_string_fields_truncated_and_blank_ignored(self):
        """字符串截断 64；空串不覆盖已有值（清空不等于改选）。"""
        from app.core import prefs
        prefs.record({"direct_provider": "p" * 100, "direct_model": ""})
        p = prefs.load()
        self.assertEqual(p["direct_provider"], "p" * 64)
        self.assertNotIn("direct_model", p)   # 空串被忽略

    def test_direct_thinking_choice_validated(self):
        """推理档走闭集校验：非法值丢弃。"""
        from app.core import prefs
        prefs.record({"direct_thinking": "ultra"})
        self.assertNotIn("direct_thinking", prefs.load())
        prefs.record({"direct_thinking": "LOW"})    # 大小写归一
        self.assertEqual(prefs.load()["direct_thinking"], "low")


if __name__ == "__main__":
    unittest.main()
