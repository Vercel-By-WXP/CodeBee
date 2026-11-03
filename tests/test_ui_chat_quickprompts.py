# -*- coding: utf-8 -*-
"""对话条「快捷指令」按钮 + 快捷指令浮层（2026-11-03 用户截图，红箭头指向
输入行右端那颗圆形小按钮，点开向上弹出的快捷指令菜单）。

参考图里的东西是三件：
  ① 输入行右端、对话模型选择之前的一颗圆形小按钮；
  ② 点开向上弹出的快捷指令菜单：每条指令＝短标签（+ 说明），底部一行
     「点击后发送」+ 右下「编辑 →」；
  ③ 点条目主体＝把指令作为对话消息直接发送；点「编辑 →」＝填入输入框改完再发。

这一层是纯覆盖层（chat-quickprompts.{js,css}）：删掉 index.html 一行 link +
一行 script 即整体回滚。守测盯住六件事（都是真会出事的点）：

  1. 接线：link + script 都在，script 必须在 app.js 之后（发送要复用
     window.chatSend 真源，不另写第二条 /chat 请求）；
  2. 挂载位置：按钮插在 .rd-chat-tools 里 #rd-model-wrap 之前（与参考图同位）；
  3. 浮层挂 document.body（.rd-chat-bar 自带 overflow:auto + sticky +
     backdrop-filter，挂在 bar 里的浮层会被裁剪——实拍坐实），且禁用
     position:fixed——#run-detail 带 backdrop-filter，fixed 的包含块会被
     改写（11-03 两起浮层飞走的血账）；
  4. 坐标与高度按视口实测（getBoundingClientRect），不许写死 max-height；
  5. 直接发送只走 window.chatSend("queue")；输入框有草稿时草稿必须保留
     （只发指令本身），「编辑」有草稿时追加不覆盖；
  6. 交互三件套（点开 / Esc 关 / 外点关）+ 新文案全部走 i18n（中英双份）+
     CSS 只消费 token、不写死十六进制色。
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, "app", "ui")


def _read(name):
    with open(os.path.join(UI_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


class TestQuickPromptsWiring(unittest.TestCase):

    def test_overlay_files_are_wired_after_app_js(self):
        html = _read("index.html")
        self.assertIn('href="chat-quickprompts.css', html,
                      "chat-quickprompts.css 未接入 index.html —— 浮层会退化成裸列表")
        self.assertIn('src="chat-quickprompts.js', html,
                      "chat-quickprompts.js 未接入 index.html")
        self.assertGreater(html.index('src="chat-quickprompts.js'),
                           html.index('src="app.js"'),
                           "chat-quickprompts.js 必须在 app.js 之后：发送要复用 window.chatSend")

    def test_icon_exists_in_sprite(self):
        html = _read("index.html")
        self.assertIn('id="i-chat"', html,
                      "按钮引用的 #i-chat 图标不在 sprite 里——按钮会变成空白圆")


class TestQuickPromptsMount(unittest.TestCase):

    def setUp(self):
        self.js = _read("chat-quickprompts.js")
        self.css = _read("chat-quickprompts.css")

    def test_button_inserted_before_model_wrap(self):
        self.assertIn('.rd-chat-tools', self.js)
        self.assertIn('#rd-model-wrap', self.js,
                      "按钮必须插在对话模型选择之前（与参考图同位）")
        self.assertIn('insertBefore(wrapEl, model)', self.js)
        # 不许挂到首页输入卡（.cmp-send）：那是新建任务，「继续执行」没有语义
        self.assertNotIn("cmp-send", self.js)

    def test_popup_attaches_to_body_not_chat_bar(self):
        self.assertIn("document.body.appendChild(pop)", self.js,
                      "浮层必须挂 body：.rd-chat-bar 的 overflow:auto + sticky + "
                      "backdrop-filter 会把挂在它里面的浮层裁掉（实拍坐实）")
        self.assertNotIn("wrapEl.appendChild(pop)", self.js)

    def test_popup_measured_positioning_not_fixed(self):
        body = re.search(r"\.qp-pop\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(body, ".qp-pop 规则缺失")
        self.assertIn("position: absolute", body.group(1))
        self.assertNotIn("position: fixed", self.css,
                         "#run-detail 带 backdrop-filter，fixed 的包含块会被改写"
                         "（11-03 浮层飞走血账）——只许 absolute")
        self.assertIn("position: relative", self.css,
                      ".qp-wrap 需要建立定位上下文（按钮包壳）")

    def test_popup_height_measured_not_hardcoded(self):
        self.assertIn("getBoundingClientRect", self.js,
                      "浮层高度必须按视口实测钳位")
        self.assertIn("maxHeight", self.js)
        # 写死 max-height 的矮屏会顶出视口（workspace-picker ⑤ 同款血账）
        self.assertNotIn("max-height: 316px", self.css)
        self.assertNotIn("max-height: 400px", self.css)

    def test_wrap_is_positioned_parent(self):
        body = re.search(r"\.qp-wrap\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(body, ".qp-wrap 规则缺失")
        self.assertIn("position: relative", body.group(1),
                      "浮层定位父级必须是按钮包壳（跟输入条一起动）")


class TestQuickPromptsBehavior(unittest.TestCase):

    def setUp(self):
        self.js = _read("chat-quickprompts.js")

    def test_send_reuses_chatsend_source_of_truth(self):
        self.assertIn('window.chatSend("queue")', self.js,
                      "直接发送必须复用 window.chatSend（与 Enter 同一条真源）")
        self.assertNotIn("/api/runs/", self.js,
                         "不许在覆盖层里另写第二条 /chat 请求")

    def test_direct_send_preserves_existing_draft(self):
        self.assertIn("var prev = ta.value;", self.js)
        self.assertIn("ta.value = prev;", self.js,
                      "输入框已有草稿时，直接发送只发指令本身，草稿必须放回输入框")

    def test_edit_appends_not_overwrites(self):
        self.assertIn('prev ? prev + "\\n" + text : text', self.js,
                      "「编辑」在有草稿时必须追加在下一行，不许覆盖用户写的内容")
        self.assertIn('dispatchEvent(new Event("input", { bubbles: true }))',
                      self.js,
                      "填入后必须派发 input 事件，触发输入框自动伸高")

    def test_escape_and_outside_click_close(self):
        self.assertIn('e.key !== "Escape"', self.js)
        self.assertIn("setOpen(false)", self.js)
        self.assertIn("pop.contains(t)", self.js,
                      "外点关闭必须先排除按钮包壳与浮层自身，否则点浮层内部也会关")

    def test_edit_button_stops_propagation(self):
        self.assertIn("e.stopPropagation()", self.js,
                      "「编辑 →」不能冒泡成「点击后发送」")


class TestQuickPromptsStyle(unittest.TestCase):

    def setUp(self):
        self.css = _read("chat-quickprompts.css")

    def test_no_hardcoded_colors(self):
        self.assertIsNone(
            re.search(r"#[0-9a-fA-F]{3,8}\b", self.css),
            "CSS 里出现写死十六进制色（DESIGN.md：只消费 token，写死色是回归事故头号来源）")

    def test_popup_uses_float_layer_shadow(self):
        body = re.search(r"\.qp-pop\s*\{([^}]*)\}", self.css)
        self.assertIn("box-shadow", body.group(1),
                      "浮层必须用浮层级投影（0 8px 24px），不许贴卡片浅投影")


class TestQuickPromptsI18n(unittest.TestCase):

    def test_new_copy_goes_through_i18n(self):
        i18n = _read("i18n.js")
        for key in ("快捷指令", "快捷指令：点一条直接发送，或先改再发", "点击后发送",
                    "先填入输入框，改完再发", "检查后再继续", "总结进度",
                    "先检查已完成的部分，发现问题直接修复，然后继续往下做。",
                    "总结当前进度：已完成什么、还剩什么、下一步是什么。"):
            self.assertIn('"%s"' % key, i18n,
                          "新文案「%s」缺 i18n 词条（DESIGN.md §7：中英双份）" % key)
        for key in ("继续执行", "编辑"):
            self.assertIn('"%s"' % key, i18n,
                          "复用的文案「%s」也必须在词条表里" % key)

    def test_ui_text_uses_tr_not_literal(self):
        js = _read("chat-quickprompts.js")
        self.assertIn('typeof window.t === "function"', js,
                      "文案必须走 t()（i18n 词条），不许在 JS 里写死中文用户可见串")
        self.assertIn('tr("点击后发送")', js)
        self.assertIn('tr("编辑")', js)


if __name__ == "__main__":
    unittest.main()
