# -*- coding: utf-8 -*-
"""记忆图谱的 atlas-live 不得压过 .hidden（2026-10-22 实案守卫）。

#sub-atlas.atlas-live 的 display:flex!important 与 .hidden 的
display:none!important 同为 important，ID+类优先级更高：图谱开过一次后，
不摘 atlas-live 就永远藏不住这一页。而摘表的出口原本只挂在 switchTab 上，
侧栏点任务走的 showDetailInMain 刻意绕开 switchTab（防 closeRun 抹
detailTaskKey）——于是「图谱→点任务」主区钉死在记忆图谱、标题却已是
运行详情。

两层防线，缺一不可：
1. atlas.css：flex 规则必须带 :not(.hidden) 限定（结构性根治，堵所有出口）；
2. app.js：不走 switchTab 的三个出口（showDetailInMain / closeRun /
   exitSettings）必须各自调用统一停表 atlasLiveStop()（顺带停掉藏起后
   仍在空转的 rAF 星图动画）。
"""

import os
import re
import unittest

UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "app", "ui")


def _read(name):
    with open(os.path.join(UI_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


def _js_function_body(src, name):
    """取顶层 function name() {...} 的近似正文：从函数头到下一个行首 function/return 边界。"""
    m = re.search(r"(?m)^function %s\s*\(" % re.escape(name), src)
    if not m:
        return None
    nxt = re.search(r"(?m)^(?:function |/\* =)", src[m.end():])
    end = m.end() + nxt.start() if nxt else len(src)
    return src[m.start():end]


class TestAtlasLiveHidden(unittest.TestCase):

    def test_css_flex_rule_scoped_to_not_hidden(self):
        css = _read("atlas.css")
        self.assertIn("#sub-atlas.atlas-live:not(.hidden)", css,
                      "atlas-live 的 flex 规则丢了 :not(.hidden) 限定：图谱会压过 .hidden 钉死在主区")
        self.assertNotRegex(
            css, r"#sub-atlas\.atlas-live\s*\{",
            "存在不带 :not(.hidden) 的 #sub-atlas.atlas-live 规则，会重新压过 .hidden")

    def test_stop_helper_exists(self):
        src = _read("app.js")
        self.assertIn("function atlasLiveStop()", src, "统一停表 atlasLiveStop 被移除")
        self.assertIn("ATLAS._stop = null;", src, "停表必须置空 ATLAS._stop（幂等闸）")

    def test_switchtab_delegates_to_helper(self):
        body = _js_function_body(_read("app.js"), "switchTab")
        self.assertIsNotNone(body, "switchTab 不见了")
        self.assertIn("atlasLiveStop()", body, "switchTab 离开图谱页必须走统一停表")

    def test_bypass_exits_call_stop(self):
        src = _read("app.js")
        for fn in ("showDetailInMain", "closeRun", "exitSettings"):
            body = _js_function_body(src, fn)
            self.assertIsNotNone(body, "%s 不见了" % fn)
            self.assertIn("atlasLiveStop()", body,
                          "%s 不走 switchTab，必须自己调用 atlasLiveStop()" % fn)


if __name__ == "__main__":
    unittest.main()
