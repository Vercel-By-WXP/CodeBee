# -*- coding: utf-8 -*-
"""过签体检界面（P2-A）守测。

界面卡最容易在两类地方坏掉，所以只守这两类：

1. **资产与接线**：两个文件必须在、必须被 index.html 引（link + defer script），
   且脚本排在任何同类覆盖层之后——漏引一次，功能整块消失而没有任何报错。
2. **血账**：
   * 不许写死 `#modal`（它是整屏遮罩，写它会收窄遮罩而不是卡）；
   * 不许用 `-webkit-line-clamp`（Edge 147+ 弃用 -webkit-box，硬切丢字）；
   * 不许用原生 alert/confirm/prompt（项目纪律，走 toast/uiConfirm）；
   * 卡片只建一次：`paint()` 里不许再 createElement / innerHTML——
     按下与抬起之间重建节点，点击会派给游离节点（血账第一条）。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "app" / "ui"
JS = UI / "signing-check.js"
CSS = UI / "signing-check.css"
INDEX = UI / "index.html"

# 允许出现在接口里的字段（前端读、后端给，两边不许各说各话）
REPORT_KEYS = {"enabled", "reason", "reason_text", "genre", "genre_name", "verdict",
               "headline", "chapters", "blockers", "warnings", "unjudged",
               "gate", "scan", "min_words", "checked_at"}
ROW_KEYS = {"chapter", "title", "words", "gains", "gain_labels", "first_gain",
            "gated", "blocked", "status", "note"}


def _text(path):
    return path.read_text(encoding="utf-8")


def _rules_only(path):
    """剥掉注释再断言：注释里写「不要用 -webkit-line-clamp」不该把守测弄红。"""
    return re.sub(r"/\*.*?\*/", "", _text(path), flags=re.S)


def _function_body(source, name):
    """粗取函数体（够用于「里面有没有某个字符串」这类断言）。"""
    match = re.search(r"function\s+%s\s*\([^)]*\)\s*\{" % re.escape(name), source)
    if not match:
        return ""
    start = match.end()
    depth = 1
    index = start
    while index < len(source) and depth:
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    return source[start:index - 1]


class AssetsTest(unittest.TestCase):
    def test_files_exist(self):
        self.assertTrue(JS.is_file(), "signing-check.js 不见了")
        self.assertTrue(CSS.is_file(), "signing-check.css 不见了")
        self.assertGreater(len(_text(JS)), 2000, "JS 短得不像有实现")

    def test_index_links_both(self):
        html = _text(INDEX)
        self.assertIn('href="signing-check.css"', html)
        self.assertIn('src="signing-check.js"', html)

    def test_script_loads_after_revision_brief(self):
        # 弹层里的「看改稿单」要调 openRevisionBrief，顺序反了就是 undefined。
        html = _text(INDEX)
        self.assertLess(html.index("revision-brief.js"), html.index("signing-check.js"))


class BloodDebtTest(unittest.TestCase):
    def test_no_native_dialogs(self):
        js = _text(JS)
        for bad in ("alert(", "confirm(", "prompt("):
            self.assertNotIn(bad, js, "原生 %s 被禁，走 toast / uiConfirm" % bad)

    def test_css_scopes_modal_not_mask(self):
        css = _rules_only(CSS)
        # 尺寸必须写 `.sc-modal .modal`；`#modal` 是整屏遮罩。
        self.assertIn(".sc-modal .modal", css)
        self.assertNotRegex(css, r"#modal\b")

    def test_css_has_no_line_clamp(self):
        self.assertNotIn("webkit-line-clamp", _rules_only(CSS))

    def test_css_uses_theme_tokens_not_hex(self):
        css = _rules_only(CSS)
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", css),
                          "颜色要写 var(--token)，否则十套主题跟不过去")
        for token in ("--text", "--muted", "--border", "--ok", "--warn", "--bad"):
            self.assertIn("var(%s)" % token, css)

    def test_card_is_built_once(self):
        js = _text(JS)
        paint = _function_body(js, "paint")
        self.assertTrue(paint, "paint() 没找到")
        self.assertNotIn("createElement", paint)
        self.assertNotIn("innerHTML", paint)
        # 建卡只允许发生在 ensureCard 里，且带 isConnected 复用判断。
        ensure = _function_body(js, "ensureCard")
        self.assertIn("createElement", ensure)
        self.assertIn("isConnected", ensure)

    def test_no_window_reload_or_location(self):
        js = _text(JS)
        self.assertNotIn("location.reload", js)


class I18nTest(unittest.TestCase):
    # 界面文案走 i18n 词条；英文模式下漏一条就是把中文甩给英文用户。
    KEYS = ("过签体检", "看改稿单", "会被拦的地方",
            "短板（不拦，但读者能感觉到）", "这几条没有机器判据，得你自己看",
            "查了 {0} 章", "第 {0} 章", "{0} 处获得", "单章空窗", "体检报告加载失败")

    def test_keys_registered(self):
        js = _text(UI / "i18n.js")
        for key in self.KEYS:
            self.assertIn('"%s":' % key, js, "缺英文词条：%s" % key)

    def test_placeholders_are_passed_through(self):
        # t 只传 key 会把「查了 {0} 章」原样显示出来——必须 arguments 透传。
        self.assertIn("window.t.apply(null, arguments)", _text(JS))


class ContractTest(unittest.TestCase):
    """前端读的字段必须真在报告里：少一个字段界面就少一行，且不报错。"""

    def _report(self):
        from core.quality import health
        return health.report({"type": "code"})

    def test_keys_match_frontend(self):
        data = self._report()
        self.assertEqual(set(data), REPORT_KEYS)

    def test_unknown_report_has_same_shape(self):
        # 两个分支键必须一致：界面只有一套渲染，字段飘了就是空白。
        from core.quality import health
        unknown = health.report({"type": "code"})
        self.assertEqual(unknown["verdict"], health.VERDICT_UNKNOWN)
        row_keys = set(health._chapter_rows({
            "chapters": [{"chapter": 1, "words": 100, "gains": [], "gated": True,
                          "warnings": [], "blockers": ["x"]}]})[0])
        self.assertEqual(row_keys, ROW_KEYS)


if __name__ == "__main__":
    unittest.main()
