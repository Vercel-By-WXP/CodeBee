# -*- coding: utf-8 -*-
"""前端禁止浏览器原生弹框（alert / confirm / prompt）。

原生框由浏览器绘制（标题形如「127.0.0.1:8765 显示」），与应用视觉完全脱节、
部分环境会被浏览器一键禁用，且阻塞 JS 主线程。2026-10-21 用户拍板：全部
改用应用内弹层，并以本闸门永久封禁原生调用。

- 确认框 → app.js uiConfirm(msg, {ok, danger, title}) → Promise<boolean>
- 输入框 → app.js uiPrompt(title, value)               → Promise<string|null>
- 复杂表单 → openModal(title, bodyHtml, footHtml)
"""

import glob
import os
import re
import unittest

UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "app", "ui")

# window.prompt( / window.alert( / window.confirm( —— 全文件硬禁：正常代码与
# 文案都不该出现这种写法，见了就是想调原生框。
WINDOW_CALL_RE = re.compile(r"window\.(?:prompt|alert|confirm)\s*\(")
# 裸名调用 prompt( / alert( / confirm(（前面不是 .、标识符字符、$），
# uiConfirm / confirmEdge / reconfirm 等标识符不误伤。
BARE_CALL_RE = re.compile(r"(?<![.\w$])(?:prompt|alert|confirm)\s*\(")
# .js 行内先摘掉引号字符串再查裸名，避免 i18n 词典英文文案误报
# （如 “Muted {0}'s alert (auto-rearms on recovery)”）。
_JS_STRINGS_RE = re.compile(r"'[^']*'|\"[^\"]*\"")


def _js_violations(path, text):
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        reasons = []
        if WINDOW_CALL_RE.search(line):
            reasons.append("window.* 原生弹框")
        if BARE_CALL_RE.search(_JS_STRINGS_RE.sub('""', line)):
            reasons.append("裸名原生弹框调用")
        if reasons:
            hits.append("%s:%d %s" % (os.path.basename(path), lineno, "/".join(reasons)))
    return hits


def _html_violations(path, text):
    # HTML 内联事件处理器的 JS 藏在属性引号里，不能摘字符串；当前零命中，
    # 若未来正文文案撞词再按需细化。
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if WINDOW_CALL_RE.search(line) or BARE_CALL_RE.search(line):
            hits.append("%s:%d" % (os.path.basename(path), lineno))
    return hits


class TestUiNativeDialogBan(unittest.TestCase):
    def test_no_native_dialog_calls(self):
        hits = []
        for path in sorted(glob.glob(os.path.join(UI_DIR, "*.js"))):
            with open(path, "r", encoding="utf-8") as fh:
                hits += _js_violations(path, fh.read())
        for path in sorted(glob.glob(os.path.join(UI_DIR, "*.html"))):
            with open(path, "r", encoding="utf-8") as fh:
                hits += _html_violations(path, fh.read())
        self.assertEqual(
            hits, [],
            "发现浏览器原生弹框调用（法则：一律用 uiConfirm/uiPrompt/openModal，见 AGENTS.md）：%s"
            % "; ".join(hits))

    def test_ui_confirm_ui_prompt_helpers_exist(self):
        with open(os.path.join(UI_DIR, "app.js"), "r", encoding="utf-8") as fh:
            src = fh.read()
        for needle in ("function uiConfirm(", "function uiPrompt(",
                       "window.uiConfirm = uiConfirm;", "window.uiPrompt = uiPrompt;"):
            self.assertIn(needle, src, "统一弹层基建被移除：%s" % needle)


if __name__ == "__main__":
    unittest.main()
