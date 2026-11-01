# -*- coding: utf-8 -*-
"""P0-c：视觉兜底判成功（page_signature / assert_advanced + flow 接线）回归。

验收口径（迭代计划 P0-c）：造「视觉点击成功但页面未前进」的 fixture，
断言判 failed 而非 silently ok；「页面前进」放行；测量失败 fail-open。
"""
from __future__ import annotations

import unittest
from unittest import mock

from app.core.publish import vision_fallback as vf


class _Page:
    """最小 page 桩：evaluate/call 返回 canned 值。"""

    def __init__(self, sig=None, checked=False):
        self.sig = sig or {}
        self.checked = checked
        self.calls = []

    def call(self, fn, *args, **kw):
        self.calls.append(fn[:40])
        if "location.href" in fn:      # page_signature 的指纹 JS
            return dict(self.sig)
        if "all[i].checked" in fn or "checked" in fn:   # _checked_js 回读
            return {"ok": self.checked}
        return {}

    def evaluate(self, expr, *a, **kw):
        return 1.0

    def send(self, *a, **kw):
        return {}

    def screenshot_bytes(self):
        return b"\x89PNG"


class AssertAdvancedTests(unittest.TestCase):
    def test_not_advanced_judged_failed(self):
        before = {"url": "http://x/editor", "n": 1200, "d": ""}
        after = {"url": "http://x/editor", "n": 1201, "d": ""}
        self.assertFalse(vf.assert_advanced(before, after))

    def test_url_change_is_advanced(self):
        before = {"url": "http://x/a", "n": 10, "d": ""}
        after = {"url": "http://x/b", "n": 10, "d": ""}
        self.assertTrue(vf.assert_advanced(before, after))

    def test_text_len_jump_is_advanced(self):
        before = {"url": "u", "n": 100, "d": ""}
        after = {"url": "u", "n": 200, "d": ""}
        self.assertTrue(vf.assert_advanced(before, after))

    def test_dialog_change_is_advanced(self):
        before = {"url": "u", "n": 100, "d": ""}
        after = {"url": "u", "n": 103, "d": "发布设置"}
        self.assertTrue(vf.assert_advanced(before, after))

    def test_unmeasurable_fails_open(self):
        self.assertTrue(vf.assert_advanced(None, {"url": "u", "n": 1, "d": ""}))
        self.assertTrue(vf.assert_advanced({}, None))

    def test_page_signature_fail_open(self):
        class Boom:
            def call(self, *a, **kw):
                raise OSError("page gone")
        self.assertIsNone(vf.page_signature(Boom()))


class SignatureJSTests(unittest.TestCase):
    def test_signature_js_uses_registry_dialog_enum(self):
        from app.core.publish import selectors
        class P:
            def call(self, fn, *a, **kw):
                self.fn = fn
                return {"url": "u", "n": 1, "d": ""}
        p = P()
        vf.page_signature(p)
        self.assertIn(selectors.get("dialog_enum"), p.fn)


if __name__ == "__main__":
    unittest.main()
