# -*- coding: utf-8 -*-
"""详情页内容留白（2026-11-02 用户实案：「为啥这些页面都没有加 margin 呢，我说过很多次了」）。

背景：作品信息等页签在宽屏下把卡片一路拉到主区边缘，左右只剩主区 padding，
用户读成「整页没留白」。三条根因与对应守点：
  1. 页签内部左右 padding 为 0（.rd-bookmeta 写的是 `12px 0 28px`）→ 卡片顶边；
  2. #run-detail 在宽屏铺满主区，没有内容列上限与居中，右侧没有余量；
  3. 设置态把 .panel.wide 的 margin 定成 `0 0 6px !important`，把横向 auto 余量吃掉；
  4. .bm-cards 用 minmax(400px, 1fr)，容器窄于 400px 时卡片撑破容器顶出页签。
另外守住画布：wb-codebee 的主区底必须是 demo 的「大白柔光 + 152deg 三段青蓝」，
退化成平涂或过淡即失去「按 demo 来做」的意义。
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, "app", "ui")


def _read(name):
    with open(os.path.join(UI_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


def _rule(css, selector_pattern):
    """取匹配选择器的最后一条规则体（后加载覆盖生效）。"""
    hits = re.findall(r"(?m)^\s*%s\s*\{([^}]*)\}" % selector_pattern, css)
    return hits[-1] if hits else None


class TestDetailContentGutters(unittest.TestCase):

    def test_non_chat_panes_have_side_gutters(self):
        body = _rule(_read("style.css"),
                     r'#run-detail > \.rd-main > \.rd-pane:not\(\[data-pane="chat"\]\)')
        self.assertIsNotNone(
            body,
            "#run-detail 非对话页签缺少 padding-inline：作品信息/蜂巢/步骤等页面"
            "会贴着主区边，用户看到的就是「整页没留白」")
        m = re.search(r"padding-inline\s*:\s*([\d.]+)px", body)
        self.assertIsNotNone(m, "应写 `padding-inline: <n>px`（不要用 padding 简写，"
                                "会连带改掉上下间距）")
        self.assertGreaterEqual(float(m.group(1)), 16,
                                "内容页签左右留白至少 16px（当前 %spx）" % float(m.group(1)))

    def test_run_detail_is_capped_and_centered(self):
        # 选择器必须是后代（`#page-settings #run-detail`）：#run-detail 实际住在
        # `#page-settings > #sub-runs` 里，写成子选择器会一条都不命中（见下一条守测）。
        body = _rule(_read("style.css"),
                     r'html\[data-layout\] main:where\(:not\(\.chat-fill\)\) #page-settings #run-detail')
        self.assertIsNotNone(
            body,
            "缺少 #run-detail 的限宽规则：宽屏下工作台铺满主区，"
            "左右只剩主区 padding（chat-fill 态仅 24px）＝用户口中的「没 margin」")
        self.assertRegex(body, r"max-width\s*:\s*\d+px", "必须给内容列一个 max-width")
        self.assertRegex(body, r"margin-inline\s*:\s*auto",
                         "限宽必须配 margin-inline: auto，否则只是贴左不是居中")

    def test_no_dead_child_selector_on_run_detail(self):
        """#run-detail 住在 `#page-settings > #sub-runs` 里（index.html 957/971）。

        历史上 style.css 6156、settings-v3 259 都写成 `#page-settings > #run-detail`
        （子选择器），**一条都没生效过**，于是「限宽居中」「设置态放回 auto」这些
        修复全是空转，用户反复看到「没加 margin」。凡是这个形状的选择器一律拦。
        """
        offenders = []
        for name in sorted(os.listdir(UI_DIR)):
            if not name.endswith(".css"):
                continue
            # 剥注释：注释里会引用这个错误写法当反面教材，不剥会自己把自己判红
            css = re.sub(r"/\*.*?\*/", "", _read(name), flags=re.S)
            for m in re.finditer(r"#page-settings\s*>\s*#run-detail", css):
                line = css[:m.start()].count("\n") + 1
                offenders.append("%s:%d" % (name, line))
        self.assertFalse(
            offenders,
            "`#page-settings > #run-detail` 永远匹配不上（中间隔着 #sub-runs），"
            "请改成 `#page-settings #run-detail`：%s" % ", ".join(offenders))

    def test_settings_mode_keeps_detail_horizontal_margin(self):
        """设置态那条 `margin: 0 0 6px !important` 会把横向 auto 余量清零，需要单独放回。"""
        body = _rule(_read("settings-v3.css"),
                     r'html\[data-layout\] body\.settings-mode #page-settings #run-detail')
        self.assertIsNotNone(
            body,
            "缺少设置态下 #run-detail 的 margin 覆盖：settings-v3 的 "
            "`margin: 0 0 6px !important` 会吃掉左右 auto，详情页退化成贴左")
        self.assertIn("auto", body, "margin 里必须给 auto（水平居中）")
        self.assertIn("!important", body, "同层有 !important 的 margin 简写，这里也必须带")

    def test_settings_subpages_are_centered(self):
        """设置子页只写 max-width 不写 auto 时会整块贴左（右侧空一大片）。"""
        css = _read("settings-v3.css")
        blocks = re.findall(
            r'html\[data-layout\] body\.settings-mode #page-settings > \.subpage\s*\{([^}]*)\}', css)
        self.assertTrue(blocks, "设置子页的 max-width 规则不见了")
        body = blocks[-1]
        self.assertRegex(body, r"max-width\s*:\s*\d+px", "设置子页应有 max-width")
        self.assertRegex(body, r"margin-inline\s*:\s*auto",
                         "设置子页限宽必须配 margin-inline: auto，否则宽屏下整块贴左")

    def test_bookmeta_cards_cannot_overflow(self):
        body = _rule(_read("style.css"), r"\.bm-cards")
        self.assertIsNotNone(body, ".bm-cards 规则不见了")
        flat = re.sub(r"\s+", "", body)
        self.assertIn("minmax(min(400px,100%),1fr)", flat,
                      "列宽下限必须是 min(400px, 100%)：纯 minmax(400px, 1fr) 在窄容器里"
                      "会把卡片撑到 400px 顶出页签（实测 400 > 393）")

    def test_codebee_canvas_follows_demo(self):
        """demo 的主区底：大白柔光 + 152deg 三段青蓝（canvas-1→2→3）。"""
        css = _read("demo-skin.css")
        self.assertIn("152deg", css, "画布渐变角度丢失（demo 是 152deg）")
        self.assertRegex(css, r"rgba\(255,\s*255,\s*255,\s*\.93\)",
                         "demo 的 51% 36% 大白柔光（rgba(255,255,255,.93)）不见了")
        self.assertIn("--wb-uc-out", css,
                      "画布加深段应由 --wb-uc-out 派生；用 --wb-accent 会偏绿，"
                      "用 97% bg 混合则过淡（都不是 demo 的观感）")


if __name__ == "__main__":
    unittest.main()
