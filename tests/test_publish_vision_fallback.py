# -*- coding: utf-8 -*-
"""视觉兜底点击（publish/vision_fallback.py）单测。

覆盖：模型回复解析（脏文本/低置信/缺坐标全放行）、视觉模型名字启发、
候选挑选（开关闸/钉死/自动挑的启停与无 KEY 过滤）、救援链全程（DPR 换算、
坐标钳制、真实点击派发、取证日志）、flow 两处失败出口的接线（救活/救不活
维持原错误）。全部 mock，不触网、不起浏览器。
"""
import unittest
from unittest import mock

from app.core.publish import flow, vision_fallback as vf
from app.core.publish.flow import FlowError, run_flow


class LooksVisionTests(unittest.TestCase):
    def test_hits(self):
        for name in ("glm-4.5v", "GLM-4V-Plus", "qwen3-vl-plus", "QVQ-72B",
                     "gemini-2.5-flash", "gpt-4o-mini", "claude-sonnet-4",
                     "doubao-vision-pro", "grok-4-fast-omni"):
            self.assertTrue(vf._looks_vision(name), name)

    def test_misses(self):
        for name in ("glm-4.6", "deepseek-chat", "kimi-k2", "qwen3-coder",
                     "llama-3.3-70b", ""):
            self.assertFalse(vf._looks_vision(name), name)


class ParseReplyTests(unittest.TestCase):
    def test_clean_json(self):
        loc = vf._parse_reply(
            '{"found": true, "text": "立即发布", "x": 300, "y": 450, "confidence": 0.9}')
        self.assertEqual(loc["x"], 300.0)
        self.assertEqual(loc["conf"], 0.9)
        self.assertEqual(loc["text"], "立即发布")

    def test_markdown_fenced_and_prose(self):
        self.assertIsNotNone(vf._parse_reply(
            '好的，结果如下：\n```json\n{"found":true,"x":10,"y":20,"confidence":0.8}\n```'))
        self.assertIsNotNone(vf._parse_reply(
            '{"found": true, "confidence": 0.7, "x": 5, "y": 6, "text": "存为草稿"} 完毕'))

    def test_not_found_or_low_confidence(self):
        self.assertIsNone(vf._parse_reply('{"found": false, "confidence": 0.2}'))
        self.assertIsNone(vf._parse_reply(
            '{"found": true, "x": 10, "y": 20, "confidence": 0.3}'))
        self.assertIsNone(vf._parse_reply('页面上没有这个按钮'))

    def test_bad_payload(self):
        self.assertIsNone(vf._parse_reply('{"found": true, "y": 20}'))
        self.assertIsNone(vf._parse_reply('{"found": true, "x": -5, "y": 20, "confidence": 0.9}'))
        # 出界坐标在解析层放行（1e9 也算数），钳制留给救援链的视口边界
        self.assertIsNotNone(vf._parse_reply(
            '{"found": true, "x": 1e9, "y": 20, "confidence": 0.9}'))
        self.assertIsNone(vf._parse_reply('{"found": true, "x": "a", "y": 20, "confidence": 0.9}'))
        self.assertIsNone(vf._parse_reply(None))


class CandidatesTests(unittest.TestCase):
    def _patch(self, settings_ret, provs=None):
        p1 = mock.patch("app.core.settings.load", return_value=settings_ret)
        p2 = mock.patch("app.core.modelhub.providers",
                        return_value=provs if provs is not None else [])
        p1.start(), p2.start()
        self.addCleanup(p1.stop), self.addCleanup(p2.stop)

    def test_kill_switch(self):
        self._patch({"vision_fallback_enabled": False})
        self.assertEqual(vf._candidates(), [])

    def test_pinned_model(self):
        self._patch({"vision_fallback_enabled": True,
                     "vision_fallback_model": "prov-9/glm-4.5v"})
        self.assertEqual(vf._candidates(), [("prov-9", "glm-4.5v")])

    def test_pinned_malformed_ignored(self):
        self._patch({"vision_fallback_enabled": True,
                     "vision_fallback_model": "prov-9"})
        self.assertEqual(vf._candidates(), [])

    def test_auto_pick_filters_and_caps(self):
        provs = [
            {"id": "p-nokey", "enabled": True, "api_key": "", "keys": [],
             "models": [{"name": "glm-4.5v", "enabled": True}]},
            {"id": "p-off", "enabled": False, "api_key": "k",
             "models": [{"name": "gpt-4o", "enabled": True}]},
            {"id": "p-ok", "enabled": True, "api_key": "k",
             "models": [{"name": "glm-4.6", "enabled": True},        # 非视觉
                        {"name": "qwen3-vl-plus", "enabled": False},  # 停用
                        {"name": "gemini-2.5-flash", "enabled": True}]},
            {"id": "p-ok2", "enabled": True, "api_key": "k",
             "models": [{"name": "gpt-4o-mini", "enabled": True},
                        {"name": "claude-sonnet-4", "enabled": True}]},
        ]
        self._patch({"vision_fallback_enabled": True, "vision_fallback_model": ""},
                    provs)
        self.assertEqual(vf._candidates(),
                         [("p-ok", "gemini-2.5-flash"),
                          ("p-ok2", "gpt-4o-mini"),
                          ("p-ok2", "claude-sonnet-4")])


class _FakePage:
    """满足 rescue_click 的最小页面桩：截图/DPR/视口可脚本化，send 记账。"""

    def __init__(self, dpr=1.0, vw=1366, vh=900, png=b"PNGDATA"):
        self.dpr, self.vw, self.vh, self.png = dpr, vw, vh, png
        self.sent = []

    def screenshot_bytes(self):
        if self.png is None:
            raise RuntimeError("截图失败")
        return self.png

    def evaluate(self, expr, **kw):
        if "devicePixelRatio" in expr:
            return self.dpr
        if "innerWidth" in expr:
            return self.vw
        if "innerHeight" in expr:
            return self.vh
        return None

    def send(self, method, params=None, timeout=30.0):
        self.sent.append((method, params))

    def call(self, fn, *args, **kw):
        return "BUTTON:立即发布"


class RescueClickTests(unittest.TestCase):
    def _gen(self, reply=None, ok=True, error=""):
        def fake(pid, model, prompt, max_tokens=2048, timeout_s=120, image_b64=""):
            self.seen = {"pid": pid, "model": model, "b64": bool(image_b64),
                         "prompt_has_targets": "立即发布" in prompt}
            if not ok:
                return {"ok": False, "text": "", "error": error}
            return {"ok": True, "text": reply if reply is not None else ""}
        return fake

    def _run(self, page, gen):
        notes = []
        with mock.patch.object(vf, "_candidates",
                               return_value=[("prov-9", "glm-4.5v")]), \
             mock.patch("app.core.modelhub.generate_once", side_effect=gen):
            r = vf.rescue_click(page, ["立即发布", "存为草稿"],
                                note=notes.append)
        return r, notes

    def test_full_chain_converts_dpr_and_clicks(self):
        page = _FakePage(dpr=1.5)
        gen = self._gen('{"found": true, "text": "立即发布", "x": 300, '
                        '"y": 450, "confidence": 0.9}')
        r, notes = self._run(page, gen)
        self.assertTrue(r["ok"])
        self.assertEqual((r["x"], r["y"]), (200, 300))       # 300/1.5, 450/1.5
        self.assertEqual(r["provider"], "prov-9")
        presses = [p for m, p in page.sent
                   if p and p.get("type") == "mousePressed"]
        releases = [p for m, p in page.sent
                    if p and p.get("type") == "mouseReleased"]
        self.assertEqual(len(presses), 1)
        self.assertEqual((presses[0]["x"], presses[0]["y"]), (200, 300))
        self.assertEqual(len(releases), 1)
        self.assertTrue(any("视觉兜底命中" in n for n in notes))
        self.assertTrue(self.seen["b64"])
        self.assertTrue(self.seen["prompt_has_targets"])

    def test_clamps_to_viewport(self):
        page = _FakePage(dpr=1.0, vw=1366, vh=900)
        gen = self._gen('{"found": true, "text": "x", "x": 9000, '
                        '"y": 9000, "confidence": 0.9}')
        r, _ = self._run(page, gen)
        self.assertEqual((r["x"], r["y"]), (1360, 894))

    def test_model_not_found_passes_through(self):
        page = _FakePage()
        gen = self._gen('{"found": false, "confidence": 0.2}')
        r, _ = self._run(page, gen)
        self.assertIsNone(r)
        self.assertEqual(page.sent, [])          # 绝不乱点

    def test_model_error_notes_and_passes_through(self):
        page = _FakePage()
        gen = self._gen(ok=False, error="HTTP 403 用户额度不足")
        r, notes = self._run(page, gen)
        self.assertIsNone(r)
        self.assertEqual(page.sent, [])
        self.assertTrue(any("未命中" in n and "403" in n for n in notes))

    def test_screenshot_failure_passes_through(self):
        page = _FakePage(png=None)
        gen = self._gen('{"found": true, "x": 1, "y": 1, "confidence": 0.9}')
        r, _ = self._run(page, gen)
        self.assertIsNone(r)
        self.assertEqual(page.sent, [])

    def test_no_candidates_short_circuits(self):
        page = _FakePage()
        with mock.patch.object(vf, "_candidates", return_value=[]):
            self.assertIsNone(vf.rescue_click(page, ["立即发布"]))
        self.assertEqual(page.sent, [])


class _FlowPage:
    """run_flow 桩：real_click_text 恒败（模拟改版全灭），其余探针全空。"""

    def __init__(self):
        self.sent = []
        self.rescued = []

    def neutralize_dialogs(self):
        pass

    def real_click_text(self, text, scope="", **kw):
        return {"ok": False, "err": "页面上找不到文本为「%s」的可点元素" % text}

    def call(self, fn, *args, **kw):
        return {}

    def send(self, method, params=None, timeout=30.0):
        self.sent.append((method, params))


class FlowWiringTests(unittest.TestCase):
    def test_click_real_rescued_by_vision(self):
        page = _FlowPage()
        logs = []

        def fake_rescue(pg, targets, note=None):
            page.rescued.append((targets, list(targets)))
            if note:
                note("视觉兜底命中「立即发布」→(680,450)")
            return {"ok": True, "via": "vision", "text": "立即发布",
                    "x": 680, "y": 450, "conf": 0.9,
                    "provider": "p", "model": "m"}

        with mock.patch.object(vf, "rescue_click", side_effect=fake_rescue):
            n = run_flow(page, [{"do": "click_real", "text": "立即发布",
                                 "tries": 1}], log=logs.append)
        self.assertEqual(n, 1)
        self.assertEqual(page.rescued[0][0], ["立即发布"])

    def test_click_real_rescue_fails_keeps_original_error(self):
        page = _FlowPage()
        with mock.patch.object(vf, "rescue_click", return_value=None):
            with self.assertRaises(FlowError) as cm:
                run_flow(page, [{"do": "click_real", "text": "立即发布",
                                 "tries": 1}])
        self.assertIn("页面上找不到", str(cm.exception))

    def test_submit_rescued_by_vision(self):
        page = _FlowPage()

        def fake_rescue(pg, targets, note=None):
            page.rescued.append(list(targets))
            return {"ok": True, "via": "vision", "text": "立即发布",
                    "x": 680, "y": 450, "conf": 0.9,
                    "provider": "p", "model": "m"}

        with mock.patch.object(vf, "rescue_click", side_effect=fake_rescue):
            n = run_flow(page, [{"do": "submit", "text": "立即发布",
                                 "tries": 1}], log=lambda s: None)
        self.assertEqual(n, 1)
        self.assertEqual(page.rescued[0], ["立即发布"])

    def test_submit_rescue_fails_keeps_original_error(self):
        page = _FlowPage()
        with mock.patch.object(vf, "rescue_click", return_value=None):
            with self.assertRaises(FlowError) as cm:
                run_flow(page, [{"do": "submit", "text": "立即发布",
                                 "tries": 1}], log=lambda s: None)
        self.assertIn("页面上找不到", str(cm.exception))   # real_click_text 的原错误优先于默认文案


if __name__ == "__main__":
    unittest.main()
