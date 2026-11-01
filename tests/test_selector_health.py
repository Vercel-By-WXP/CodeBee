# -*- coding: utf-8 -*-
"""P0-b：零额度改版自检（selector_health）回归。

验收口径（迭代计划 P0-b）：隔离实例连本地 fixture 页——
「改坏一个 sel → 自检报红」且「全对 → 模型调用计数为 0」。
fixture 在 page 边界注入（present 集合），不碰真浏览器、不烧额度。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core.publish import selector_health as sh


FIXTURE_DOM = {
    # qimao upload_chapter 真实基线的选择器（fixture 页「有这些元素」）
    "a.create-new-chapter-btn, a[class*=\"create-new-chapter\"]",
    "textarea[placeholder=\"请输入章节名称，最多20个字\"]",
    ".q-contenteditable.book.font-size-16.edit-mask",
}


class _FakePage:
    def call(self, fn, sels, **kw):
        return [{"s": s, "ok": s in FIXTURE_DOM} for s in sels]


STEPS = [
    {"do": "wait", "sel": "a.create-new-chapter-btn, a[class*=\"create-new-chapter\"]"},
    {"do": "fill", "sel": "textarea[placeholder=\"请输入章节名称，最多20个字\"]",
     "key": "chapter_title"},
    {"do": "fill", "sel": ".q-contenteditable.book.font-size-16.edit-mask",
     "key": "chapter_body"},
    {"do": "submit", "text": "立即发布"},          # text 型不参与盘点
]


class HealthCheckTests(unittest.TestCase):
    def test_all_hit_red_false_and_zero_model_calls(self):
        with mock.patch.object(sh, "_model_locate") as m:
            rep = sh.health_check("qimao", "upload_chapter",
                                  present=FIXTURE_DOM, steps=STEPS)
        m.assert_not_called()                      # 健康=模型一次都不调
        self.assertFalse(rep["red"])
        self.assertEqual(rep["model_calls"], 0)
        self.assertEqual(rep["checked"], 3)
        self.assertEqual(rep["hits"], 3)
        self.assertEqual(rep["rate"], 1.0)

    def test_broken_sel_reports_red_and_calls_model(self):
        broken = STEPS + [{"do": "wait",
                           "sel": ".old-button-that-gone"}]
        with mock.patch.object(sh, "_model_locate",
                               return_value={"hint": ".new-button", "calls": 1}) as m:
            rep = sh.health_check("qimao", "upload_chapter",
                                  present=FIXTURE_DOM, steps=broken)
        m.assert_called_once()                     # 红线才调模型
        self.assertTrue(rep["red"])
        self.assertEqual(rep["misses"], [".old-button-that-gone"])
        self.assertEqual(rep["model_calls"], 1)
        self.assertEqual(rep["model_hint"], ".new-button")

    def test_model_locate_off_switch(self):
        broken = STEPS + [{"do": "wait", "sel": ".gone"}]
        with mock.patch.object(sh, "_model_locate") as m:
            rep = sh.health_check("qimao", "upload_chapter",
                                  present=FIXTURE_DOM, steps=broken,
                                  model_locate=False)
        m.assert_not_called()
        self.assertTrue(rep["red"])
        self.assertEqual(rep["model_calls"], 0)

    def test_offline_audit_never_red(self):
        rep = sh.health_check("qimao", "upload_chapter", steps=STEPS)
        self.assertFalse(rep["red"])
        self.assertTrue(rep["offline"])
        self.assertIsNone(rep["rate"])

    def test_real_page_duck_path(self):
        rep = sh.health_check("qimao", "upload_chapter",
                              page=_FakePage(), steps=STEPS)
        self.assertFalse(rep["red"])
        self.assertEqual(rep["rate"], 1.0)


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(sh.paths, "PUBLISH_DIR",
                              Path(self.tmp.name) / "publish")
        p.start()
        self.addCleanup(p.stop)

    def test_record_and_latest(self):
        self.assertIsNone(sh.latest())
        rep = sh.health_check("qimao", "upload_chapter",
                              present=FIXTURE_DOM, steps=STEPS)
        self.assertTrue(rep["recorded"])
        last = sh.latest("qimao", "upload_chapter")
        self.assertIsNotNone(last)
        self.assertEqual(last["rate"], 1.0)
        self.assertEqual(last["platform"], "qimao")
        # 过滤不匹配 → None
        self.assertIsNone(sh.latest("fanqie", "upload_chapter"))


class ModelLocateTests(unittest.TestCase):
    def test_model_locate_parses_json_array(self):
        # 注意：selector_health 用 `from .. import modelhub`（= app.core.modelhub），
        # 补丁必须打在同一个模块对象上（core.* 与 app.core.* 是两个实例）
        from app.core import modelhub
        with mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=({"id": "p"}, "m")), \
             mock.patch.object(modelhub, "chat",
                               return_value={"ok": True,
                                             "text": '[".new-btn", ".new-dd"]'}):
            out = sh._model_locate("qimao", "upload_chapter", [".old"])
        self.assertEqual(out, {"hint": ".new-btn；.new-dd", "calls": 1})

    def test_model_locate_silent_on_failure(self):
        from app.core import modelhub
        with mock.patch.object(modelhub, "resolve_orchestrator",
                               return_value=None):
            out = sh._model_locate("qimao", "upload_chapter", [".old"])
        self.assertEqual(out, {"hint": "", "calls": 0})


if __name__ == "__main__":
    unittest.main()
