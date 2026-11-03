# -*- coding: utf-8 -*-
"""任务详情「任务条 + 页签」形态的守测（2026-11-02 用户实案：

    「任务详情、设置页面这些有问题，和 demo 不一样，样式也是乱的，特别是任务详情」

那天实测到的四处硬冲突（都是产品原设计与 demo 横条形态互斥造成的，不是"没调好"）：

  1. `#rd-rail` 被 `main.chat-fill #run-detail.chat-mode #rd-rail`（(1,3,1)）锁成
     flex 列 + 12~16px 内距 → 任务条上下各多一道白边；
  2. `.detail-head` 是 `flex-direction: column`（竖排右栏卡的写法）→ 标题占满一行、
     状态徽标掉第二行、右侧按钮叠上去（截图里就是"标题消失、徽标落到下面"）；
  3. `#rd-title` 写死 `width: calc(100% - 26px)`，`.rd-actions` 写死 grid + `width:100%`
     → 标题被压成 0 宽、两个图标按钮被 grid 拉到左右两端；
  4. 「任务动态」(#rd-context) 与「对话导航胶囊」(#rd-chat-nav) 在直连任务下常驻，
     合计 615px 顶在任务条下方，把页签行推到屏幕中间（实测 #rd-tabs y=622）。

这些规则要么靠 !important + 同长度选择器前缀压住，要么根本不该在横条形态里出现，
所以逐条守住——任何一条被"顺手清理"掉，详情页会立刻退回那天截图的样子。
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, "app", "ui")


def _read(name):
    with open(os.path.join(UI_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


def _flat(css):
    """去掉注释与多余空白：注释里写的反例（"原本是 column"）不能骗过断言。"""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return re.sub(r"\s+", " ", css)


def _rule(css, selector_literal):
    flat = _flat(css)
    pat = re.escape(re.sub(r"\s+", " ", selector_literal)) + r"\s*\{([^{}]*)\}"
    hits = re.findall(pat, flat)
    return hits[-1] if hits else None


def _group_body(css, selector_literal):
    """选择器**组**（逗号分隔）的规则体：从该选择器字面量往后找第一个 `{`。
    `_rule` 要求字面量紧邻 `{`，组内非末尾的选择器永远匹配不到。"""
    flat = _flat(css)
    lit = re.sub(r"\s+", " ", selector_literal)
    i = flat.find(lit)
    while i >= 0:
        brace = flat.find("{", i)
        if brace < 0:
            return None
        # 选择器与 `{` 之间不该出现 `}`（出现说明已经跨进了别的规则）
        if "}" not in flat[i:brace]:
            end = flat.find("}", brace)
            return flat[brace + 1:end] if end > brace else None
        i = flat.find(lit, i + 1)
    return None


class TestTaskHeadIsAHorizontalBar(unittest.TestCase):
    """demo .task-head = height 56 / display flex / flex-direction row / padding 0 20。"""

    def setUp(self):
        self.css = _read("demo-parity.css")

    def test_rail_is_not_the_floating_task_card(self):
        """浮卡形态那几条规则特异度是 (1,3,1)，覆盖必须带同样长的前缀。"""
        body = _group_body(self.css, 'html[data-layout="workbench"] main.chat-fill '
                                     '#run-detail.chat-mode #rd-rail')
        self.assertIsNotNone(
            body,
            "缺少 main.chat-fill #run-detail.chat-mode #rd-rail 的覆盖："
            "conversation-reference.css:300 / desktop-workbench.css:187 会把它锁成"
            "flex 列 + 16px 内距的右上角浮卡，任务条横不过来")
        self.assertIn("padding: 0", body, "浮卡内距必须归零，否则任务条上下各一道白边")
        self.assertRegex(body, r"display:\s*block", "任务条容器应是块级两行，不是 flex 列")
        self.assertIn("max-height: none", body, "浮卡的 max-height 会把两行内容裁掉")

    def test_detail_head_is_a_row(self):
        body = _rule(self.css, 'html[data-layout="workbench"] #rd-rail > .detail-head')
        self.assertIsNotNone(body, "缺少任务条规则")
        self.assertRegex(body, r"flex-direction:\s*row",
                         "style.css:5970 是 flex-direction: column（竖排右栏卡），"
                         "不改回 row 就会出现「标题占满一行、状态徽标掉第二行」")
        self.assertRegex(body, r"height:\s*56px", "demo .task-head 是 56px")
        self.assertRegex(body, r"padding:\s*0 20px", "demo .task-head 左右内距 20")

    def test_title_takes_the_remaining_width(self):
        body = _rule(self.css, 'html[data-layout="workbench"] #rd-rail > .detail-head > #rd-title')
        self.assertIsNotNone(body, "缺少标题规则")
        self.assertRegex(body, r"flex:\s*1 1 0",
                         "标题基准必须归零：style.css:5998 的 width: calc(100% - 26px) "
                         "会把基准撑满整行，标题反而被右侧操作区压成 0 宽（实测直接消失）")
        self.assertRegex(body, r"width:\s*auto", "必须显式 width: auto 才顶得掉那条 calc")
        self.assertIn("text-overflow: ellipsis", body, "长标题要省略号，不许换行撑高任务条")

    def test_actions_are_an_inline_row(self):
        body = _rule(self.css, 'html[data-layout="workbench"] #rd-rail > .detail-head > .rd-actions')
        self.assertIsNotNone(body, "缺少操作区规则")
        self.assertRegex(body, r"display:\s*flex",
                         "style.css:6009 是 grid 两列 + width:100% + 上边框（竖排卡形态），"
                         "不改回 inline flex 就会把两个图标按钮拉到任务条左右两端")
        self.assertRegex(body, r"width:\s*auto", "操作区不能吃满整行")
        self.assertIn("margin: 0 0 0 auto", body, "靠右对齐要交给 auto 外边距")


class TestHiddenBlocksStayOutOfFlow(unittest.TestCase):

    def setUp(self):
        self.css = _read("demo-parity.css")

    def test_context_panel_becomes_a_popover(self):
        body = _rule(self.css, 'html[data-layout="workbench"] #rd-rail > #rd-context')
        self.assertIsNotNone(body, "缺少「任务动态」规则")
        self.assertRegex(body, r"position:\s*absolute",
                         "#rd-context 原本是右栏浮卡内的常驻块（实测 513px 高）；"
                         "留在流里会把页签行顶到屏幕中间")
        self.assertIsNotNone(
            _rule(self.css, 'html[data-layout="workbench"] #rd-rail:not(.context-open) > #rd-context'),
            "缺少默认收起规则：必须由 .context-open 控制显隐")

    def test_chat_nav_pills_are_off(self):
        body = _rule(self.css, 'html[data-layout="workbench"] #rd-chat-nav')
        self.assertIsNotNone(body, "缺少 #rd-chat-nav 规则")
        self.assertIn("display: none !important", body,
                      "页签条已在，chat-mode 的导航胶囊再出现就是两套导航并存 + 错位")


class TestSettingsOverlay(unittest.TestCase):
    """demo .overlay{padding:20px} + .settings{max-width:1120; radius:16}。"""

    def setUp(self):
        self.css = _read("demo-parity.css")

    def test_main_gutter_beats_the_important_zero(self):
        """settings-v3.css:76 写 `padding: 0 !important`，普通声明甚至内联样式都压不住。"""
        body = _rule(self.css, 'html[data-layout="workbench"] body.settings-mode #mainwrap main')
        self.assertIsNotNone(
            body,
            "缺少含 #mainwrap 的设置态 main 规则：选择器短一截就比不过 "
            "settings-v3.css:76 的 (1,3,2)，浮层会重新贴死侧栏与窗口边")
        self.assertIn("padding: 20px !important", body,
                      "必须带 !important 且值是 20px：那条规则是 !important，"
                      "同级加起来还要比特异度")

    def test_page_settings_zeroes_its_own_padding(self):
        body = _rule(self.css, 'html[data-layout="workbench"] body.settings-mode #page-settings')
        self.assertIsNotNone(body, "缺少设置浮层规则")
        self.assertIn("padding: 0 !important", body,
                      "settings-v3.css:80 给的是 padding: 26px 40px 72px（无浮层形态的内距），"
                      "浮层里这层内距会把圆角边框撑开")
        self.assertRegex(body, r"max-width:\s*1120px", "demo 设置浮层是 1120")
        self.assertRegex(body, r"border-radius:\s*16px", "demo 设置浮层圆角 16")


class TestTaskInfoPopover(unittest.TestCase):

    def setUp(self):
        self.css = _read("demo-parity.css")
        self.js = _read("demo-parity.js")

    def test_popover_floats_under_the_task_bar(self):
        # 2026-11-03 二次改判：任务信息从「面板内折叠段」改回**浮层**，藏在任务条
        # 右上的仪表盘按钮后面（用户「任务信息太长了，放到右上角点了按钮弹出框看」）。
        # 落点与「任务动态」一致：绝对定位在 #rd-rail 右上角，开合靠 .hidden。
        self.assertIsNone(
            _rule(self.css, 'html[data-layout="workbench"] #rd-tabs-more .rd-meta-popover'),
            "面板内那套 static 覆盖已退役：浮层放回 188 宽常驻面板会被 overflow 裁掉")
        body = _rule(self.css, 'html[data-layout="workbench"] #rd-rail > #rd-meta-popover')
        self.assertIsNotNone(body, "缺少任务信息浮层定位规则")
        self.assertRegex(body, r"position:\s*absolute", "浮层必须是右上角绝对定位")
        self.assertRegex(body, r"z-index:\s*4[0-9]", "浮层要盖住常驻面板（z-index 46 档）")
        self.assertIsNotNone(
            _rule(self.css, 'html[data-layout="workbench"] #rd-rail > #rd-meta-popover.hidden'),
            "浮层缺 .hidden 关闭规则")

    def test_actions_are_moved_into_the_shelf(self):
        self.assertIn("tidyDetailActions", self.js, "缺少任务条操作收纳逻辑")
        self.assertIn("rd-actions-shelf", self.js, "操作区宿主必须是 #rd-actions-shelf")
        self.assertIn("rd-context-toggle", self.js, "任务动态按钮不能被搬走")
        self.assertIn("rd-meta-toggle", self.js, "任务信息按钮不能被搬走（要留在任务条上）")
        self.assertIn("cb-actions-shelf", self.js + self.css,
                      "收纳区缺类名：按钮搬进去后没有任何排版")


if __name__ == "__main__":
    unittest.main()
