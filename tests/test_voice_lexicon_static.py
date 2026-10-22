# -*- coding: utf-8 -*-
"""语音输入链路静态闸（paseo voice control 差量收口，2026-10-22）。

语音口述本体（bindVoiceInput，ad2ee3c）已有功能检测降级；本轮补上它承诺却缺失的
两块：设置页 orch.voice_terms 词表编辑器、voiceFixup 裸术语行生效。本闸做静态接线
检查，不启服务、不打 8765：

- index.html：词表 textarea / 保存按钮 / 外观导航可搜到「语音」/ 对话条麦克风按钮默认隐藏
- app.js：功能检测降级、词表读写函数、loadSettings 回填、裸术语词边界归一
- i18n.js：新增键存在且不重复（后值静默覆盖是发版前巡检的已知坑）
"""

import os
import re
import unittest

UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "app", "ui")


def _read(name):
    with open(os.path.join(UI_DIR, name), encoding="utf-8") as f:
        return f.read()


class VoiceLexiconStaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _read("index.html")
        cls.app = _read("app.js")
        cls.i18n = _read("i18n.js")

    def test_settings_editor_markup(self):
        self.assertRegex(self.html, r'id="set-voice-terms"')
        self.assertRegex(self.html, r'onclick="saveVoiceTerms\(\)"')
        # 外观导航条目可被「语音/口述/词表/麦克风」搜到
        nav = re.search(r'data-sub="appearance"[^>]*data-search="([^"]*)"', self.html)
        self.assertTrue(nav, "appearance 导航条目缺失")
        for kw in ("语音", "口述", "词表", "麦克风"):
            self.assertIn(kw, nav.group(1))

    def test_chat_bar_button_degrades(self):
        # 浏览器不支持 SpeechRecognition 时按钮保持隐藏（不能闪一个死按钮）
        btn = re.search(r'<button id="rd-chat-voice" class="([^"]*)"', self.html)
        self.assertTrue(btn, "rd-chat-voice 按钮缺失")
        self.assertIn("hidden", btn.group(1).split())
        self.assertIn("function bindVoiceInput", self.app)
        self.assertIn("window.webkitSpeechRecognition", self.app)

    def test_lexicon_functions_and_hook(self):
        self.assertIn("function loadVoiceTerms", self.app)
        self.assertIn("function saveVoiceTerms", self.app)
        # 设置页打开时回填词表（loadSettings 是全部设置字段的统一回填点）
        self.assertRegex(self.app, r"fillNotifyPush\(\);\s*\r?\n\s*loadVoiceTerms\(\);")
        self.assertIn('localStorage.getItem("orch.voice_terms")', self.app)
        self.assertIn('localStorage.setItem("orch.voice_terms"', self.app)

    def test_bare_term_unification(self):
        # 「错误→正确」对之外，裸术语行也要生效：大小写不敏感 + 前后不能是
        # ASCII 字母数字（词边界），否则 "AI" 会把 "said" 改坏
        self.assertIn('"gi"', self.app)
        self.assertRegex(self.app, r"\(\?<!\[A-Za-z0-9\]\)")

    def test_i18n_keys_present_and_unique(self):
        keys = [
            "语音纠偏词表（一行一条）",
            "错误写法→正确写法；或只写术语如 CodeBee",
            "保存词表",
            "语音词表已保存",
            "保存失败，请重试",
        ]
        for key in keys:
            quoted = '"' + key + '":'
            count = self.i18n.count(quoted)
            self.assertEqual(count, 1, "i18n 键 %r 应恰好出现一次，实际 %d 次" % (key, count))


if __name__ == "__main__":
    unittest.main()
