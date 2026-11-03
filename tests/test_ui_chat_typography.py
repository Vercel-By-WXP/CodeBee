# -*- coding: utf-8 -*-
"""对话正文排版（2026-11-03 用户：「我觉得我们对话内容样式不好看，要不使用附件的样式」）。

用户给了三张图：执行轨迹逐条铺开（每条一行、一行一条横线）的那屏太碎，而另两张
「完成态回答」的排版是想要的——段落舒展、加粗小标题独立成段、列表有呼吸。

归因不是缺样式，是三处节奏错配 + 一处特异性事故：

1. 正文段距只有 `.8em`（约 12px），而列表缩进 1.7em、标题按 1.45em 放大——
   小标题比正文大一圈，粗体和字号抢戏，读起来像网页正文而不是一份文档。
2. 过程区（`.chat-think`）折叠态也画一条底线，展开态还有整圈虚线框；一屏里
   好几条横线层层堆叠，注意力全被过程抢走。
3. **特异性事故**：style.css 里 `#run-detail.chat-mode .chat-think`（单 id + 单类）
   一整套规则压过 conversation-reference.css 的单个 `.rd-chat` 前缀——只写在
   `.rd-chat` 下改了等于没改（实测 metaTop 仍回到 7px）。所以本文件所有排版规则
   一律抬到 `#run-detail #rd-chat`（双 id）才落地。

2026-11-03 第二轮（用户：「对话里面文字样式和大小和字体按照附件的来」）：参考件
像素实测过程行 13px / 正文近黑 #191919 / 行距 27px，本产品原来是 15px/1.78 +
#505052（字号大 15%、颜色又浅）。落回 DESIGN.md §3 规范区间：正文 13.5 /
过程 12.5 / meta 12，token 只在 `.rd-chat` 定义一次；气泡规则同样必须双 id
（style.css 的 `#run-detail.chat-mode .chat-bubble.agent` 15px 会压掉单 `.rd-chat`）。
"""

import os
import re
import unittest

UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "app", "ui")
CSS_FILE = "conversation-reference.css"


def _read(name, base=UI_DIR):
    with open(os.path.join(base, name), "r", encoding="utf-8") as fh:
        return fh.read()


def _block(css, marker):
    """取 marker 起第一条规则（到下一个 `}`）的声明体。"""
    i = css.find(marker)
    if i < 0:
        return None
    j = css.find("{", i)
    if j < 0:
        return None
    k = css.find("}", j)
    return css[j + 1:k] if k > j else None


def _decl(body, prop):
    if not body:
        return None
    m = re.search(r"(?m)(?<![\w-])%s\s*:\s*([^;]+);" % re.escape(prop), body)
    return m.group(1).strip() if m else None


def _px(value):
    m = re.match(r"(-?\d+(?:\.\d+)?)px", value or "")
    return float(m.group(1)) if m else None


class TestChatBodyRhythm(unittest.TestCase):
    """正文（.chat-markdown）的段落、列表、标题节奏。"""

    @classmethod
    def setUpClass(cls):
        cls.css = _read(CSS_FILE)

    def test_paragraph_gap_is_roomy(self):
        body = _block(self.css, "#run-detail #rd-chat .chat-markdown p {")
        self.assertIsNotNone(body, "缺少段落规则（或前缀被改回单 .rd-chat，会被 style.css 压掉）")
        margin = _decl(body, "margin")
        self.assertIsNotNone(margin, "段落规则必须显式写 margin")
        gap = _px((margin or "").split()[-1])
        self.assertIsNotNone(gap, "段距应写成 px，实际 %r" % margin)
        self.assertGreaterEqual(gap, 11, "段距 %spx 太挤，正文 13.5px 时 13px 起步才不糊" % gap)
        self.assertLessEqual(gap, 18, "段距 %spx 过大，会把回答拉成一屏一段" % gap)

    def test_type_scale_tokens_are_single_source(self):
        """2026-11-03 字阶对齐参考件：正文 13.5 / 过程 12.5 / meta 12，
        token 只在 .rd-chat 上定义一次，各角色引用 var()（改字号只改这一处）。"""
        block = _block(self.css, ".rd-chat {")
        for var, expect in (("--chat-fs", "13.5px"), ("--chat-fs-sm", "12.5px"), ("--chat-fs-xs", "12px")):
            self.assertEqual(_decl(block, var), expect,
                             "%s 应为 %s（参考件实测：过程行 13px、meta 更小）" % (var, expect))
        for marker, name in (("#run-detail #rd-chat .chat-bubble.agent {", "正文气泡"),
                             ("#run-detail .rd-chat-bar textarea {", "输入框")):
            body = _block(self.css, marker)
            self.assertIsNotNone(body, "缺少 %s 规则" % name)
            self.assertEqual(_decl(body, "font-size"), "var(--chat-fs)",
                             "%s 必须引用 --chat-fs，不许另立字号" % name)

    def test_bubble_rules_carry_dual_id(self):
        """血账：style.css 的 `#run-detail.chat-mode .chat-bubble.agent`（1 id + 2 类）
        会压掉 `.rd-chat .chat-bubble.agent`（0,3,0）——第一版正文 15px 没落地就是它。"""
        self.assertIn("#run-detail #rd-chat .chat-bubble.agent {", self.css,
                      "气泡字号规则前缀被削弱，15px 会赢回来")
        self.assertIn("#run-detail #rd-chat .chat-bubble.me {", self.css,
                      "用户气泡字号规则前缀被削弱")

    def test_light_text_is_near_black_not_muddy_gray(self):
        """参考件正文近黑 #191919、过程行 #7f7f7f；旧值 #505052/#929295 是「一律中灰」，
        字号降下来以后中灰会糊掉。"""
        block = _block(self.css, 'html[data-theme="light"] .rd-chat {')
        self.assertEqual(_decl(block, "--chat-text"), "#2b2b30", "浅色主题正文色应是近黑 #2b2b30")
        self.assertEqual(_decl(block, "--chat-muted"), "#87878d", "过程行灰应落在 #87878d 一带")

    def test_list_gets_air_and_light_markers(self):
        body = _block(self.css, "#run-detail #rd-chat .chat-markdown ul,")
        self.assertIsNotNone(body, "缺少列表容器规则")
        self.assertIsNotNone(_decl(body, "padding-left"), "列表缩进必须显式给出")
        li = _block(self.css, "#run-detail #rd-chat .chat-markdown li + li {")
        self.assertIsNotNone(li, "缺少列表项间距规则")
        self.assertGreaterEqual(_px(_decl(li, "margin-top")) or 0, 6,
                                "列表项之间至少 6px，否则要点糊成一团")
        marker = _block(self.css, "#run-detail #rd-chat .chat-markdown li::marker {")
        self.assertIsNotNone(marker, "圆点未弱化：::marker 必须走 token 而不是正文色")

    def test_headings_stay_below_body_scale(self):
        body = _block(self.css, "#run-detail #rd-chat .chat-markdown h1,")
        self.assertIsNotNone(body, "缺少标题规则")
        size = _px(_decl(body, "font-size"))
        self.assertIsNotNone(size, "标题字号应写成 px")
        self.assertLessEqual(size, 16, "小标题 %spx 太大（原来按 1.45em 放大到 21px）" % size)
        self.assertGreaterEqual(size, 13.5, "小标题 %spx 小于正文 13.5px，层级消失" % size)

    def test_bold_lead_paragraph_reads_as_subheading(self):
        body = _block(self.css, "#run-detail #rd-chat .chat-markdown p:has(> strong:first-child) {")
        self.assertIsNotNone(body, "以加粗开头的小标题段没有额外上留白")
        self.assertGreaterEqual(_px(_decl(body, "margin-top")) or 0, 16,
                                "小标题段上留白至少 16px，否则和上一段粘在一起")


class TestReasoningIsQuiet(unittest.TestCase):
    """过程区（.chat-think）只占一行，不画框、不画底线。"""

    @classmethod
    def setUpClass(cls):
        cls.css = _read(CSS_FILE)

    def test_container_has_no_frame(self):
        body = _block(self.css, "#run-detail #rd-chat .chat-think {")
        self.assertIsNotNone(body, "缺少过程区容器规则")
        self.assertEqual(_decl(body, "border"), "0",
                         "过程区还留着边框：折叠态应该是纯文字一行")
        self.assertEqual(_decl(body, "background"), "transparent",
                         "过程区不该有底色块，会跟答案抢视觉重量")

    def test_collapsed_summary_has_no_rule_line(self):
        i = self.css.find("#run-detail #rd-chat details.chat-think > summary {")
        self.assertGreater(i, 0, "缺少折叠摘要规则（双 id 前缀）")
        j = self.css.find("{", i)
        body = self.css[j:self.css.find("}", j)]
        self.assertEqual(_decl(body, "border-bottom"), "0",
                         "折叠摘要还画底线——一屏多条横线正是用户说的「不好看」")

    def test_expanded_body_uses_hairline_indent(self):
        body = _block(self.css, "#run-detail #rd-chat .chat-think .ct-body {")
        self.assertIsNotNone(body, "缺少展开正文规则")
        border_left = _decl(body, "border-left") or ""
        self.assertIn("2px solid", border_left, "展开内容应用 2px 发丝竖线归位，实际 %r" % border_left)
        self.assertIn("var(--chat-rule)", border_left, "竖线必须走 token，不许写死颜色")


class TestExecutionFeedCollapsed(unittest.TestCase):
    """执行轨迹默认收起。

    图 1 那一屏「一行一条」有两个来源：工具组在「运行中 / 最新步骤」时自动展开，
    以及实时思考把最近 8 条活动全铺在答案上方。渲染函数是唯一真源，这里直接盯它。
    """

    @classmethod
    def setUpClass(cls):
        cls.js = _read("app.js")
        cls.css = _read(CSS_FILE)

    def _fn(self, name, span=4000):
        i = self.js.find("function %s(" % name)
        self.assertGreater(i, 0, "找不到函数 %s" % name)
        return self.js[i:i + span]

    def test_only_the_running_group_opens(self):
        body = self._fn("chatToolActivityHTML")
        self.assertIn('chatDisclosureAttr(groupKey, it.status === "running")', body,
                      "默认展开条件必须是「只有正在跑的那组」：全收看不到进度，全开会刷屏")
        self.assertNotIn("index === latestLogIndex", body,
                         "「最新步骤」又变回默认展开了——已完成的任务会把最后一步摊在屏上")

    def test_live_group_is_still_visible_when_collapsed(self):
        body = self._fn("chatToolActivityHTML")
        self.assertIn("' is-live'", body, "运行中的组收起后就没有进行时标记了")
        # 断言整条声明而不是选择器前缀：`.is-live > summary .ico` 那条也含同样前缀，
        # 只比前缀会漏掉「主规则被改写/搬走」这种情况（回退验证时踩到过）。
        self.assertIn("#run-detail #rd-chat .chat-tool-group.is-live > summary "
                      "{ color: var(--chat-text); }", self.css,
                      "is-live 的主规则没了或被改写：收起态看不出「正在执行」")

    def test_live_activity_preview_is_capped(self):
        body = self._fn("chatThinkHTML", 1200)
        self.assertIn("actsAll.slice(-3)", body, "实时活动又放开到多条了")
        self.assertNotIn("(it.activity || []).slice(-8)", body, "旧的 8 条上限还在")
        self.assertIn("chat-activity-more", body, "被折叠的条数必须说出来，否则像丢数据")
        self.assertIn("#run-detail #rd-chat .chat-activity-more", self.css,
                      "「另有 N 条」没有样式")


class TestLegacyRulesDoNotWinBack(unittest.TestCase):
    """防空转：style.css 的 `#run-detail.chat-mode` 家族仍在，新规则必须压得住。"""

    def test_every_new_rule_carries_dual_id(self):
        css = _read(CSS_FILE)
        # 本轮新增/改写的三条关键规则，前缀必须是双 id
        for marker in ("#run-detail #rd-chat .chat-markdown p {",
                       "#run-detail #rd-chat .chat-think {",
                       "#run-detail #rd-chat .chat-meta {"):
            self.assertIn(marker, css, "规则前缀被削弱成单个 .rd-chat：%s" % marker)

    def test_no_old_bottom_line_left_in_chat_scope(self):
        css = _read(CSS_FILE)
        self.assertNotIn(
            "border-bottom: 1px solid var(--chat-rule);\n  font-weight: 400;",
            css,
            "旧的过程区底线规则回魂了（当初就是它在折叠态留线）",
        )

    def test_style_css_still_defines_the_competitor(self):
        """如果 style.css 那套 `#run-detail.chat-mode .chat-think` 被删了，
        本测试的前提变了——提醒后续维护者重估双 id 前缀是否还需要。"""
        legacy = _read("style.css")
        self.assertIn("#run-detail.chat-mode .chat-think {", legacy,
                      "style.css 的旧过程区规则已不存在，可考虑简化前缀（本测试需同步）")


if __name__ == "__main__":
    unittest.main()
