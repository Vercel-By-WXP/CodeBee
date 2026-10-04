# -*- coding: utf-8 -*-
"""流程级 serial.variants/branches 落盘守卫。

流程编辑器（app.js saveFlow）会把 serial.variants（同章赛马稿件数）随流程
提交，_norm_serial 曾只保留 chapters/words_per_chapter/分卷两键——variants
在 upsert（预置 overrides/自定义/分享码导入/历史恢复共用通道）时被静默丢弃，
任务创建从 flow.serial 继承后永远读到 1，赛马形同虚设。本文件锁定修复。
"""
from __future__ import annotations

from base import BaseTest


class SerialVariantsGuardTests(BaseTest):
    def setUp(self):
        super().setUp()
        from app.core import flows
        flows._FILE = self.data_dir / "flows.json"

    def test_upsert_builtin_serial_keeps_variants(self):
        """预置连载流程 override 带 variants → 落盘后仍在（1-3 钳位）。"""
        from app.core import flows
        flow, err = flows.upsert_flow({
            "id": "serial_novel",
            "serial": {"chapters": 8, "words_per_chapter": 2500, "variants": 3},
        })
        self.assertIsNone(err, err)
        got = flows.get_flow("serial_novel")["serial"]
        self.assertEqual(got.get("variants"), 3)

    def test_upsert_custom_serial_variants_and_branches(self):
        """自定义连载流程 variants/branches 双键保留；越界钳到 3。"""
        from app.core import flows
        flow, err = flows.upsert_flow({
            "id": "serialx", "name": "连载X", "engine": "review",
            "serial": {"chapters": 6, "words_per_chapter": 2000,
                       "variants": 9, "branches": 2},
        })
        self.assertIsNone(err, err)
        got = flows.get_flow("serialx")["serial"]
        self.assertEqual(got.get("variants"), 3)   # 越界钳位
        self.assertEqual(got.get("branches"), 2)

    def test_variants_off_omits_key(self):
        """variants=1（不启用）不落键——与 store.create_task 口径一致。"""
        from app.core import flows
        flow, err = flows.upsert_flow({
            "id": "serialy", "name": "连载Y", "engine": "review",
            "serial": {"chapters": 4, "words_per_chapter": 1500, "variants": 1},
        })
        self.assertIsNone(err, err)
        self.assertNotIn("variants", flows.get_flow("serialy")["serial"])

    def test_share_code_roundtrip_keeps_variants(self):
        """分享码导出→导入后 variants 不丢（全流程走 _norm_serial 通道）。"""
        from app.core import flows
        flow, err = flows.upsert_flow({
            "id": "serial_novel",
            "serial": {"chapters": 8, "words_per_chapter": 2500, "variants": 2},
        })
        self.assertIsNone(err, err)
        code, err = flows.export_flow_code("serial_novel")
        self.assertIsNone(err, err)
        self.assertTrue(code)
        result, err = flows.import_flow_code(code)
        self.assertIsNone(err, err)
        self.assertEqual(result["status"], "noop")   # 内容一致才不产修订噪音
        self.assertEqual(
            flows.get_flow("serial_novel")["serial"].get("variants"), 2)


if __name__ == "__main__":
    import unittest
    unittest.main()
