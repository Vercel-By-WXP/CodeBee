# -*- coding: utf-8 -*-
"""对话大纲覆盖层守测（2026-11-04）：左缘圆点轨道 + 悬停历史提问列表 + 点击跳转。

真源：`app/ui/chat-outline.js` + `chat-outline.css`（独立覆盖层，设计宪法 §9：
新交互不碰 app.js）。本文件锁住接线、血账与 token 纪律，防止并行会话改版时退化。
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "app", "ui")


def _read(name):
    with open(os.path.join(UI, name), "r", encoding="utf-8") as fh:
        return fh.read()


def _flat(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return re.sub(r"\s+", " ", css)


class TestChatOutlineWiring(unittest.TestCase):

    def setUp(self):
        self.js = _read("chat-outline.js")
        self.css = _read("chat-outline.css")
        self.html = _read("index.html")

    def test_files_are_wired(self):
        self.assertIn("chat-outline.css", self.html, "chat-outline.css 没接进 index.html")
        self.assertIn("chat-outline.js", self.html, "chat-outline.js 没接进 index.html")

    def test_outline_is_overlay_not_appjs(self):
        """设计宪法 §9：新交互落独立覆盖层，app.js 不许出现对话大纲实现。"""
        self.assertNotIn("rd-outline", _read("app.js"),
                         "对话大纲实现泄漏进了 app.js（应只在覆盖层）")

    def test_dom_truth_sources(self):
        """真源选择器：#rd-chat 宿主 / #rd-chat-flow 滚动区 / .chat-row.me 用户行。
        who=本机的系统注记渲染成 .chat-sysnote-group，天然不是 .me，不会被算进大纲。"""
        self.assertIn(':scope > .chat-row.me', self.js, "用户消息必须限定 flow 直接子行")
        self.assertIn('getElementById("rd-chat-flow")', self.js)
        self.assertIn('getElementById("rd-chat")', self.js)

    def test_click_loss_guard(self):
        """血账：按下与抬起之间不许重建被点的节点——指纹不变不写 DOM，
        pointerdown 期间到点的重建必须推迟。"""
        self.assertIn("rowsFingerprint(rows)", self.js, "重建前必须先比指纹")
        self.assertIn("downCount", self.js, "必须用按住计数推迟重建")
        self.assertIn("pointerdown", self.js) and self.assertIn("pointerup", self.js)

    def test_visibility_threshold(self):
        """注意力守恒：少于两条用户发言不占注意力；窄屏退场。"""
        self.assertIn("rows.length >= 2", self.js)
        self.assertIn("innerWidth > 900", self.js)
        self.assertIn("@media (max-width: 900px)", self.css)

    def test_css_discipline(self):
        """双 id 前缀压得过 style.css 复合规则；token 化，不许写死十六进制色；
        面板宽度不许挂在 22px 宿主的百分比上（负 calc 塌宽实案）。"""
        for sel in ("#run-detail #rd-chat #rd-outline",
                    "#run-detail #rd-chat .rd-outline-panel",
                    "#run-detail #rd-chat .rd-outline-dot"):
            self.assertIn(sel, self.css, f"缺 {sel} 规则")
        body = re.findall(r"#run-detail #rd-chat \.rd-outline-panel \{([^}]*)\}", self.css)
        self.assertTrue(body, "缺面板规则")
        self.assertNotIn("width: min(330px, calc(100% - 30px))", body[0],
                         "面板宽度不许引用宿主百分比（宿主只有 22px，会塌成负宽）")
        hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", self.css)
        hexes = [h for h in hexes if not re.match(r"#rd-|#chat", h)]
        self.assertEqual(hexes, [], f"CSS 写死十六进制色：{hexes}")

    def test_i18n_entries(self):
        i18n = _read("i18n.js")
        self.assertIn('"对话大纲": "Conversation outline"', i18n, "缺 i18n 词条")


if __name__ == "__main__":
    unittest.main()
