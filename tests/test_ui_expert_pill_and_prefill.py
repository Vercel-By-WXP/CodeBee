# -*- coding: utf-8 -*-
"""底栏「当前专家」身份胶囊 + 换专家时示例要跟着换（2026-11-03 用户两张截图）。

用户第一句：「点了内容不会变啊，也看不出来是选了那个工程师啊，在对话框里面
是不是要体现一下，类似这样」——配的是参考产品输入行底部那颗「小台 · 工作台搭建师」。

拆成两条可验收的行为：
  ① 选完专家，**输入行里**要有一枚常驻身份胶囊（头像 + 专家名 + chevron），
     不再只靠上方胶囊的淡高亮；它仍是 #f-type 的人脸，点击开原生 #type-menu。
  ② 选完专家，目标框要有可见变化：上一位自动填的示例要换掉；
     用户自己写的字一个都不能动。

历史坑（别改回去）：
  - 老版本把 .cmp-mode 直接 display:none，理由写的是「上方胶囊行一目了然」——
    实测不成立，胶囊多、高亮淡，选完毫无反馈；
  - 老版本只在「目标框为空」时预填，于是先点 快答（填了示例）、再点 工程师
    就什么都不变 —— 这正是用户说的「点了内容不会变」。
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, "app", "ui")


def _read(name):
    with open(os.path.join(UI_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


def _rules(css, selector_pattern):
    return re.findall(r"(?m)^\s*%s\s*\{([^}]*)\}" % selector_pattern, css)


def _code(js):
    """去掉注释行，只对真正会执行的代码做断言（注释里出现关键词不算数）。"""
    return "\n".join(ln for ln in js.splitlines() if not ln.strip().startswith(("*", "/*", "//")))


class TestExpertPillInComposerBar(unittest.TestCase):
    """① 底栏身份胶囊。"""

    def setUp(self):
        self.css = _read("composer-context.css")
        self.html = _read("index.html")

    def test_pill_markup_still_owns_the_real_source(self):
        html = self.html
        for frag in ('id="f-type"', 'id="f-type-btn"', 'id="f-type-name"', 'id="f-type-ico"',
                     'id="type-menu"'):
            self.assertIn(frag, html,
                          "底栏胶囊的真源/人脸缺了 %s —— 胶囊必须复用既有控件，"
                          "不能另起一套选择状态" % frag)

    def test_pill_is_not_hidden_anymore(self):
        self.assertNotRegex(
            self.css, r"\.cmp-mode\s*\{\s*display\s*:\s*none",
            "底栏专家胶囊不能再被隐藏：用户明确要求「在对话框里面体现一下」")
        bodies = _rules(self.css, r":is\(#sub-tasks, #rd-input-slot\) \.cmp-bar \.cmp-mode")
        self.assertTrue(bodies, "缺 `.cmp-bar .cmp-mode` 规则 —— 胶囊会退回默认块级排版")
        self.assertRegex(bodies[-1], r"display\s*:\s*inline-flex")

    def test_pill_sits_after_the_plus_button(self):
        """参考产品的输入行是「＋ … 身份胶囊 … 右侧操作」，顺序靠 order 摆。"""
        strip = _rules(self.css, r":is\(#sub-tasks, #rd-input-slot\) \.cmp-bar \.att-strip")
        mode = _rules(self.css, r":is\(#sub-tasks, #rd-input-slot\) \.cmp-bar \.cmp-mode")
        self.assertTrue(strip and mode, "att-strip / cmp-mode 的 order 规则缺一条")
        o_strip = re.search(r"order\s*:\s*(\d+)", strip[-1])
        o_mode = re.search(r"order\s*:\s*(\d+)", mode[-1])
        self.assertIsNotNone(o_strip, "att-strip 没设 order，「＋ 添加」会被胶囊挤到后面")
        self.assertIsNotNone(o_mode, "cmp-mode 没设 order")
        self.assertLess(int(o_strip.group(1)), int(o_mode.group(1)),
                        "「＋ 添加 / 运行设置」必须排在身份胶囊前面")

    def test_pill_is_a_compact_round_pill(self):
        bodies = _rules(self.css, r":is\(#sub-tasks, #rd-input-slot\) \.cmp-mode \.type-pick")
        self.assertTrue(bodies, "缺身份胶囊本体样式")
        body = bodies[-1]
        self.assertRegex(body, r"width\s*:\s*auto",
                         "style.css 的 .type-pick 是 width:100%，底栏胶囊必须显式收成 auto")
        self.assertRegex(body, r"border-radius\s*:\s*999px")
        # 头像要圆：expertAvHtml 默认是方角小卡片
        av = _rules(self.css, r":is\(#sub-tasks, #rd-input-slot\) \.cmp-mode \.tp-ico \.exp-av")
        self.assertTrue(av, "胶囊头像缺圆形规则（默认方角，和参考图不一样）")
        self.assertRegex(av[-1], r"border-radius\s*:\s*50%")

    def test_no_hardcoded_colors_in_pill_rules(self):
        for body in _rules(self.css, r":is\(#sub-tasks, #rd-input-slot\) \.cmp-mode[^{]*"):
            self.assertNotRegex(body.lower(), r"#[0-9a-f]{3,8}\b",
                                "胶囊样式里出现写死的十六进制色值，应走 --panel2 / --border token")


class TestSampleFollowsThePickedExpert(unittest.TestCase):
    """② 换专家时目标框要看得见变化。"""

    def setUp(self):
        self.js = _code(_read("home-chips.js"))

    def test_auto_text_is_tracked_not_guessed(self):
        self.assertIn("data-ccx-auto", self.js,
                      "必须用一个标记记住「这段是自动填的示例」，"
                      "否则换专家时判不出该不该覆盖")
        self.assertIn("function fillGoal", self.js)
        self.assertIn("function autoText", self.js)

    def test_user_text_is_never_overwritten(self):
        body = self.js[self.js.index("function fillGoal"):]
        body = body[:body.index("function watchGoal")]
        guard = re.search(r"if\s*\(\s*cur\s*&&\s*cur\s*!==\s*autoText\(goal\)\s*\)\s*return\s*;", body)
        self.assertIsNotNone(guard,
                             "fillGoal 少了「用户自己写过的一律不动」的早退守卫")
        # 守卫必须写在第一次赋值之前，否则先覆盖再判断就晚了
        assign = re.search(r"goal\.value\s*=", body)
        self.assertIsNotNone(assign)
        self.assertLess(guard.start(), assign.start(),
                        "早退守卫必须在写 goal.value 之前")

    def test_switching_clears_the_previous_example(self):
        body = self.js[self.js.index("function fillGoal"):]
        body = body[:body.index("function watchGoal")]
        self.assertRegex(body, r'goal\.removeAttribute\(AUTOKEY\)',
                         "换到没有金句示例的专家时，上一位留下的示例必须清掉，"
                         "否则用户看到的还是别人的例句")

    def test_placeholder_is_left_to_the_real_source(self):
        """占位符唯一真源是 app.js onTypeChange 的 `t(flow.goal_hint)`。

        本层曾自己再写一遍（goal_hint + data-i18n-ph 兜底），把真源刚写好的
        占位符盖回默认 —— 16:47 复查逮出来的自摆乌龙，删掉后 app.js 的翻译
        （含英文）自动生效。
        """
        body = self.js[self.js.index("function fillGoal"):]
        body = body[:body.index("function watchGoal")]
        self.assertNotIn("goal.placeholder", body,
                         "fillGoal 不许碰 placeholder —— 占位符真源在 app.js 的 "
                         "onTypeChange（t(flow.goal_hint)），本层写第二遍就会盖掉它")

    def test_every_flow_goal_hint_has_english(self):
        """flows.py 新加专家时漏译 goal_hint，英文界面就会把中文塞进占位符。"""
        flows_src = None
        with open(os.path.join(ROOT, "app", "core", "flows.py"), "r", encoding="utf-8") as fh:
            flows_src = fh.read()
        hints = re.findall(r'"goal_hint"\s*:\s*"([^"]+)"', flows_src)
        self.assertTrue(hints, "flows.py 里没解析到 goal_hint —— 解析逻辑要跟着改")
        i18n = _read("i18n.js")
        missing = [h for h in hints if ('"%s"' % h) not in i18n]
        self.assertFalse(missing,
                         "这些 goal_hint 没有英文词条（英文界面占位符会漏中文）：%s"
                         % "；".join(missing))

    def test_marker_is_invalidated_by_typing(self):
        self.assertIn("addEventListener(\"input\"", self.js)
        body = self.js[self.js.index("function watchGoal"):]
        self.assertIn("removeAttribute(AUTOKEY)", body,
                      "用户一打字就要作废自动标记，否则下一次换专家会覆盖他写的内容")

    def test_prefill_is_wired_into_the_pick_path(self):
        body = self.js[self.js.index("function wrapPick"):]
        self.assertIn("fillGoal(id)", body,
                      "包装 pickQuickType 时必须调用 fillGoal，否则点胶囊后输入框没反应")
        self.assertIn("watchGoal(", body)


class TestFoldedChipCount(unittest.TestCase):
    """③ 折掉的专家要数在脸上（2026-11-03：「折掉了不提示，看到 7 颗就以为是全部」）。

    `.ccx-chips` 锁一行 + `overflow:hidden`，放不下的由 foldChips() 置 display:none
    折进「更多专家」。折叠本身早就有了，缺的是**提示**：用户不知道还有几位没露。
    做法＝在「更多专家」里放一个计数胶囊，foldChips 折完后写 `+N`（0 时隐藏），
    同时把 title 变成「查看全部专家 (+N)」、胶囊由虚线转实线。
    """

    def setUp(self):
        self.js = _code(_read("composer-context.js"))
        self.css = _read("composer-context.css")

    def test_badge_node_lives_inside_the_more_button(self):
        js = self.js
        more = js[js.index('class="ccx-chip ccx-more"'):]
        more = more[:more.index("</button>")]
        self.assertIn("ccx-more-n", more,
                      "计数必须长在「更多专家」这颗胶囊里，否则会被 paint() 的重绘冲掉")

    def test_fold_writes_the_count_after_hiding_chips(self):
        body = self.js[self.js.index("function foldChips"):]
        body = body[:body.index("function paintMoreCount")]
        self.assertIn("paintMoreCount(more, folded)", body,
                      "foldChips 折完必须把数量交给 paintMoreCount，否则计数永远不更新")
        # 计数必须在折（display:none）之后算，顺序反了会数错
        self.assertLess(body.index('style.display = "none"'), body.index("paintMoreCount(more, folded)"))

    def test_zero_folded_means_no_badge_and_plain_title(self):
        body = self.js[self.js.index("function paintMoreCount"):]
        body = body[:body.index("scheduleFold") if "scheduleFold" in body else len(body)]
        self.assertIn("hidden", body, "没折东西时必须把计数藏起来（不能显示 +0）")
        self.assertIn('"+" + folded', body)
        self.assertIn("查看全部专家", body)

    def test_count_does_not_rebuild_the_list(self):
        """重绘会和 foldChips 的量宽互相触发（抖），所以只改那一颗胶囊里的文字。"""
        body = self.js[self.js.index("function paintMoreCount"):]
        body = body[:body.index("scheduleFold") if "scheduleFold" in body else len(body)]
        self.assertNotIn("innerHTML", body, "paintMoreCount 不许重写列表 HTML")
        self.assertIn("textContent", body)

    def test_badge_has_fixed_min_width_and_hides_cleanly(self):
        bodies = _rules(self.css, r"\.ccx-more-n")
        self.assertTrue(bodies, "缺 .ccx-more-n 样式")
        body = bodies[-1]
        self.assertRegex(body, r"min-width\s*:\s*\d+px",
                         "计数胶囊要有最小宽：+2 → +12 时宽度一变就会「量宽→折位→再量宽」地抖")
        hidden_rule = _rules(self.css, r"\.ccx-more-n\[hidden\]")
        self.assertTrue(hidden_rule, "缺 `.ccx-more-n[hidden]{display:none}` —— "
                                     "inline-flex 会压过 hidden 属性")
        self.assertRegex(hidden_rule[-1], r"display\s*:\s*none")
        self.assertTrue(_rules(self.css, r"\.ccx-chip\.ccx-more\.has-folded"),
                        "有折起来的专家时，「更多专家」要从虚线转实线（这才是真的还有内容）")

    def test_badge_colors_use_tokens(self):
        for body in _rules(self.css, r"\.ccx-more-n?[^{]*"):
            self.assertNotRegex(body.lower(), r"#[0-9a-f]{3,8}\b",
                                "计数胶囊里出现写死的十六进制色值")


if __name__ == "__main__":
    unittest.main()
