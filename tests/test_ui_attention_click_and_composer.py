# -*- coding: utf-8 -*-
"""两处「点了没反应 / 看都看不到」（2026-11-02 用户实案，同一轮两张截图）。

## 一、首页「需要你处理」行点了没反应
用户截了一张注意力卡（发布告警 + 四条运行失败），点行毫无反应。
根因不在行本身，而在**入口选错**：borrow-ui.js 行点击直接调 `window.openRun(rid)`，
而详情面板住在 `#page-settings` 壳里；停在主页时那层壳是 hidden 的，裸 openRun
只把 `#run-detail` 自身的 hidden 摘掉 → 详情在隐藏页面里铺开 = 点了没反应。
老血账（app.js showDetailInMain 注释里写过 2026-10-21 同款）：壳必须一并翻回
`page-settings` + `sub-runs` 子页。正确入口是 `jumpToRun`——它先 showDetailInMain
翻壳再 openRun。

同轮还收掉「假可点」：没有 run / task 落点的行（如发布成功率告警）加 `.ba-static`，
指针不再变手型；否则用户点它同样是「没反应」。

## 二、详情页输入框被压成一条线
长对话时 `.rd-chat` 高度固定、两个 flex 子项都在缩：`.rd-chat-flow` 是 `flex: 1 1 auto`
（基准=消息总高，动辄几千像素），依 flex 收缩按「基准×系数」分配，`.rd-chat-bar`
的基准只有一百来像素 → 几乎全部亏空由它吃。实测（1440×900，该 run 消息很长）：
输入条只剩 **22px**，textarea 顶在 y=895（视口 900），工具条跑到 y=958 完全看不见，
与用户截图「看都看不到输入框这些了」一致。
定式：消息流 `flex: 1 1 0`（基准 0，内容再长也不参与收缩比例）+ 输入条
`flex: 0 0 auto`（一律不压缩，过高由 max-height:62% 封顶并内部滚动）。
"""

import os
import re
import unittest

UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "app", "ui")


def _read(name):
    with open(os.path.join(UI_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


def _css_rule(css, selector):
    """取选择器完全相等的那条规则的声明体（允许行首缩进，便于抓媒体查询内的规则）。"""
    m = re.search(r"(?m)^\s*%s\s*\{([^}]*)\}" % re.escape(selector), css)
    return m.group(1) if m else None


class TestAttentionRowClick(unittest.TestCase):

    def test_row_click_goes_through_jumpToRun(self):
        src = _read("borrow-ui.js")
        self.assertIn("window.jumpToRun", src,
                      "注意力条行点击必须走 jumpToRun：裸 openRun 会把详情开在 hidden 的 "
                      "#page-settings 壳里，主页点行就是「没反应」（用户实案）")
        # 首选入口必须是 jumpToRun：确认它在 openRun 之前被调用
        self.assertLess(src.index("window.jumpToRun"), src.index("window.openRun"),
                        "jumpToRun 必须先于裸 openRun 兜底出现，否则首选又退化回死点击")

    def test_keydown_only_for_non_button_rows(self):
        src = _read("borrow-ui.js")
        self.assertIn('row.tagName !== "BUTTON"', src,
                      "行是原生 <button>，Enter/Space 会自己转成 click；再绑 keydown 会连开两次详情")

    def test_rows_without_target_are_not_clickable(self):
        src = _read("borrow-ui.js")
        self.assertIn('ba-static', src,
                      "没有 run/task 落点的行（发布成功率告警）必须标 .ba-static，别做成可点")
        rule = _css_rule(_read("borrow-ui.css"), "#borrow-attention .ba-row.ba-static")
        self.assertIsNotNone(rule, "缺少 .ba-row.ba-static 规则：假可点的行指针仍是手型")
        self.assertIn("cursor: default", rule, ".ba-static 行必须 cursor: default")


class TestAttentionCardBelowComposer(unittest.TestCase):
    """2026-11-03 用户：「这个要不放到对话框下面去？」
    卡片原先插在页面最顶（问候语上方，demo .need-card 的位置），workbench 布局里
    还写死 `order: -1` 强行抢排位。现在挂点锚到输入卡之后。"""

    def test_mounts_right_after_input_card(self):
        src = _read("borrow-ui.js")
        self.assertIn('.rd-input-card', src,
                      "注意力卡挂点必须锚到输入卡（.rd-input-card）：插回 firstChild 又压到问候语上方")
        self.assertIn('anchor.insertAdjacentElement("afterend", bar)', src,
                      "必须用 afterend 把卡片插到输入卡后面；缺这行挂点会退化回页面顶部")
        self.assertIn('anchor.parentElement === host', src,
                      "锚点必须在同一个宿主里才插，否则会跨容器塞进别处")

    def test_no_order_neg1_in_workbench(self):
        css = _read("demo-parity.css")
        rule = _css_rule(css, 'html[data-layout="workbench"] #page-home #borrow-attention')
        self.assertIsNotNone(rule, "缺少 workbench 下注意力卡的排位规则")
        self.assertNotRegex(rule, r"order:\s*-\d",
                            "workbench 里不许再用负 order 抢排位：它会把卡片顶回页顶，"
                            "与「放到对话框下面」相反（DOM 顺序已是输入卡之后）")
        self.assertRegex(rule, r"order:\s*0",
                         "应显式写 order: 0，覆盖掉历史负值")


class TestComposerNeverSqueezed(unittest.TestCase):

    def test_chat_bar_does_not_shrink(self):
        css = _read("desktop-workbench.css")
        rule = _css_rule(css, "#run-detail.chat-mode .rd-chat-bar")
        self.assertIsNotNone(rule, "缺少 #run-detail.chat-mode .rd-chat-bar 规则")
        self.assertRegex(rule, r"flex:\s*0 0 auto",
                         "输入条必须 flex: 0 0 auto（不参与收缩）：写成 0 1 auto 时长对话会把"
                         "输入框压到 22px、textarea 与工具条被挤出视口（用户实案）")
        self.assertIn("max-height", rule,
                      "输入条不压缩的前提是有 max-height 封顶，否则排队消息多时会吞掉消息流")

    def test_message_stream_uses_zero_flex_basis(self):
        css = _read("desktop-workbench.css")
        rule = _css_rule(css, "#run-detail.chat-mode .rd-chat-flow")
        self.assertIsNotNone(rule, "缺少 #run-detail.chat-mode .rd-chat-flow 规则")
        self.assertRegex(rule, r"flex:\s*1 1 0\b",
                         "消息流必须 flex: 1 1 0（基准 0）：默认 auto 时基准=消息总高，"
                         "shrink 按基准比例分配，输入条会被连带压扁")


if __name__ == "__main__":
    unittest.main()
