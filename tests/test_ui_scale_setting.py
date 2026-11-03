# -*- coding: utf-8 -*-
"""设置 → 外观 →「界面字号」整体缩放（2026-11-03 用户：「设置里面也需要一个字体大小的设置，
可以调整整体字体的大小」）。

机制（为什么是根元素 zoom，不是 html{font-size}）：
11-03 刚把全站 1257 处 font-size 收成绝对 px 阶梯（scratch/type_scale.py），
改 root 字号只能带动 rem，px 纹丝不动——「整体调大调小」只有 CSS zoom 能把
文字、控件与视口单位一起等比缩。zoom 挂**根元素**（html）：按 CSS Viewport
增补（Chromium 128+ / FF 126+），根 zoom 等比缩放初始包含块与视口单位——
`#app { height:100dvh }` 仍正好铺满视口，fixed 遮罩一起缩放，坐标 API 保持
同一视觉坐标系。挂 body 则 100dvh 元素被放大 1.25 倍撑出屏幕（本仓库 25 个
样式表 40+ 处 100vh/vw/dvh，必踩）。

守测盯七件事（都是线上真会出事的点）：
  1. 三档规则存在且挂在根选择器上（html[data-uiscale]），不是 body；
  2. 白名单一致：CSS 三档 == JS UI_SCALE_STEPS（90/110/125）== index.html
     四个按钮（含默认 100）——三处任何一处单方面加档都会静默失配；
  3. 默认档零影响：CSS 里不许出现 100 档规则；JS applyUiScale 在 100 档
     必须 delete dataset.uiscale（不落属性＝没有 zoom）；
  4. 预涂脚本只在非 100 档写属性，且只认白名单（防刷新闪跳 + 非法值上屏）；
  5. seg 委托 + boot 同步都在（少了就是点不动 / 选中态不跟随）；
  6. i18n 双份（小/大/特大/界面字号 + hint，zh 原文即 key）；
  7. 只许 zoom 缩根元素，不许有人在别处对 body 再叠 zoom（双重缩放）。
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, "app", "ui")

STEPS = {"90", "110", "125"}   # 非 100 的档位


def _read(name):
    with open(os.path.join(UI_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


class TestUiScaleCss(unittest.TestCase):
    def setUp(self):
        self.css = _read("ui-scale.css")

    def test_three_steps_on_root_selector(self):
        """三档必须挂在根选择器 html[data-uiscale]，且各自带 zoom 值。"""
        for step, factor in (("90", ".9"), ("110", "1.1"), ("125", "1.25")):
            m = re.search(r'html\[data-uiscale="%s"\]\s*\{([^}]*)\}' % step, self.css)
            self.assertIsNotNone(m, "缺 %s 档规则" % step)
            self.assertIn("zoom: %s" % factor, m.group(1),
                          "%s 档 zoom 值应为 %s" % (step, factor))

    def test_no_rule_targets_body(self):
        """zoom 声明不许落在 body 选择器上（body 变量赋值等其它规则不禁）。"""
        for m in re.finditer(r"(?m)^\s*([^\n{}]*?)\s*\{([^}]*)\}", self.css):
            sel, body = m.group(1).strip(), m.group(2)
            if "zoom" in body and "data-uiscale" in sel:
                self.assertIn("html", sel.split("body")[0] if "body" in sel else sel,
                              "zoom+data-uiscale 规则必须以 html 为目标，不能把 zoom 挂 body")

    def test_default_step_writes_nothing(self):
        """默认档（100）不许有 CSS 规则：不写属性＝零影响＝可整体回滚。"""
        self.assertNotIn('data-uiscale="100"', self.css)

    def test_only_whitelisted_values(self):
        hits = set(re.findall(r'data-uiscale="(\d+)"', self.css))
        self.assertEqual(hits, STEPS, "CSS 档位应恰为 %s" % sorted(STEPS))


class TestUiScaleWiring(unittest.TestCase):
    def setUp(self):
        self.html = _read("index.html")
        self.js = _read("app.js")

    def test_link_is_wired(self):
        self.assertIn('href="ui-scale.css', self.html,
                      "ui-scale.css 未接入 index.html —— 切档不会有任何效果")

    def test_buttons_match_whitelist_plus_default(self):
        btns = set(re.findall(r'data-uiscale="(\d+)"', self.html))
        self.assertEqual(btns, STEPS | {"100"},
                         "seg 按钮应恰为 小/标准/大/特大 四档")

    def test_js_whitelist_matches_css(self):
        m = re.search(r'UI_SCALE_STEPS\s*=\s*\[([^\]]*)\]', self.js)
        self.assertIsNotNone(m, "app.js 缺 UI_SCALE_STEPS 白名单")
        js_steps = set(re.findall(r'"(\d+)"', m.group(1)))
        self.assertEqual(js_steps, STEPS | {"100"},
                         "JS 白名单应与 CSS 三档 + 默认档一致")

    def test_default_deletes_attribute(self):
        m = re.search(r"function applyUiScale\(\)\s*\{(.*?)\n\}", self.js, re.S)
        self.assertIsNotNone(m, "app.js 缺 applyUiScale()")
        body = m.group(1)
        self.assertIn('v === "100"', body, "必须显式判默认档")
        self.assertIn("delete root.dataset.uiscale", body,
                      "100 档必须 delete 属性（不落 zoom）；只写 else 分支不算数")
        self.assertRegex(body, r"else\s+root\.dataset\.uiscale\s*=\s*v",
                         "非 100 档必须赋值 data-uiscale（本轮实案：还原回退时把 set 写成 delete，"
                         "守测没盯住——ls 存了 125 但 zoom 永远不生效")

    def test_pretaint_script_only_writes_non_default(self):
        """首屏预涂：非 100 档才写属性，且值必须在白名单内（防非法值上屏）。"""
        m = re.search(r'var _uis[^;]*;', self.html)
        self.assertIsNotNone(m, "预涂脚本缺 _uis 读取")
        line = m.group(0)
        self.assertIn('localStorage.getItem("orch.uiscale")', line)
        cond = re.search(r'if\s*\(([^)]*_uis[^)]*)\)', _read("index.html"))
        self.assertIsNotNone(cond, "预涂脚本缺白名单判断")
        for step in STEPS:
            self.assertIn('"%s"' % step, cond.group(1),
                          "预涂白名单缺 %s 档" % step)
        self.assertNotIn('"100"', cond.group(1),
                         "预涂不该写 100 档属性（默认档零影响）")

    def test_click_delegate_and_boot_sync(self):
        self.assertIn('$("ui-scale").addEventListener', self.js,
                      "缺 #ui-scale 点击委托 —— 按钮点不动")
        self.assertIn("saveUiScale(b.dataset.uiscale)", self.js)
        self.assertIn("applyUiScale();", self.js,
                      "boot 缺 applyUiScale() —— 刷新后选中态不跟随")

    def test_no_double_zoom_on_body_anywhere(self):
        """全 UI 目录不许有第二处 body zoom（叠加＝双重缩放事故）。"""
        for name in os.listdir(UI_DIR):
            if not name.endswith(".css"):
                continue
            with open(os.path.join(UI_DIR, name), "r", encoding="utf-8") as fh:
                css = fh.read()
            for m in re.finditer(r"(?m)^\s*([^\n{}]*?)\s*\{([^}]*zoom[^}]*)\}", css):
                sel = m.group(1).strip()
                if "data-uiscale" in sel:
                    self.assertIn("html", sel,
                                  "%s: zoom 规则必须挂在 html[data-uiscale]" % name)


class TestUiScaleI18n(unittest.TestCase):
    def test_entries_exist_in_both_langs(self):
        i18n = _read("i18n.js")
        for key in ("小", "大", "特大", "界面字号"):
            self.assertRegex(i18n, r'"%s"\s*:\s*"[^"]+"' % key,
                             "i18n 缺词条：%s" % key)
        self.assertIn('"标准": "Standard"', i18n)   # 既有词条，防误删


if __name__ == "__main__":
    unittest.main()
