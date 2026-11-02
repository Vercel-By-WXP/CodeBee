# -*- coding: utf-8 -*-
"""「作品信息」页签的间距层级（2026-11-02 用户实案：优化样式，咋都没有间隔哟）。

背景：hero 带搬进本页签后，这一页变成「带边框卡片」的堆叠，而原来的间距是
按纯文本列表调的（4~6px）。现场实测的贴死点：
  · 封面卡（第二个 .bm-cards）与平台卡之间 **0px**，两张卡片边框直接相压；
  · 发布块 .bm-pub 与上方空态/字段区之间 0px，虚线紧贴内容；
  · 发布块内按钮行距 6px，四行按钮糊成一坨；
  · 页签容器 padding-top 0，hero 卡顶到页签栏。
统一成三级节奏后（区块 18 / 卡片内 14~16 / 子元素 8~10），这里守住数值，
避免以后有人「顺手」把它们调回紧凑值。
"""

import os
import re
import unittest

UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "app", "ui")


def _read(name):
    with open(os.path.join(UI_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


def _rules(css, selector):
    """取选择器完全相等的所有规则声明体（同一选择器可能被多处追加覆盖）。"""
    return [m.group(1) for m in re.finditer(
        r"(?m)^\s*%s\s*\{([^}]*)\}" % re.escape(selector), css)]


def _px(body, prop):
    """取某个属性（含 margin/padding 简写的第一段）的 px 值；找不到返回 None。"""
    m = re.search(r"(?:^|[;\s])%s\s*:\s*(-?[\d.]+)px" % re.escape(prop), body)
    return float(m.group(1)) if m else None


class TestBookMetaSpacing(unittest.TestCase):

    def test_card_groups_do_not_touch(self):
        rules = _rules(_read("style.css"), ".bm-cards + .bm-cards")
        self.assertTrue(rules,
                        "缺少 .bm-cards + .bm-cards 规则：封面卡是第二个 .bm-cards，"
                        "没有它两张卡片组边框直接相压（实测 0px，用户「咋都没有间隔哟」的第一现场）")
        gap = _px(rules[-1], "margin-top")
        self.assertIsNotNone(gap, ".bm-cards + .bm-cards 必须给 margin-top")
        self.assertGreaterEqual(gap, 12, "卡片组间距不应小于 12px（当前 %spx）" % gap)

    def test_hero_slot_breathing_room(self):
        rules = _rules(_read("style.css"), ".bm-hero-slot")
        gap = _px(rules[-1], "margin-bottom")
        self.assertIsNotNone(gap, ".bm-hero-slot 缺少 margin-bottom：hero 会贴住小标题")
        self.assertGreaterEqual(gap, 16, "hero 带与小标题至少 16px（当前 %spx）" % gap)

    def test_pane_has_top_padding(self):
        css = _read("style.css")
        # 生效的是高特异性那条（中段 #run-detail .rd-pane[data-pane="bookmeta"] #rd-bookmeta）
        scoped = [b for b in re.findall(
            r'#run-detail \.rd-pane\[data-pane="bookmeta"\] #rd-bookmeta\s*\{([^}]*)\}', css)]
        self.assertTrue(scoped, "bookmeta 页签容器的 padding 规则不见了")
        tops = [_px(b, "padding") for b in scoped]
        self.assertTrue(any(t is not None and t >= 10 for t in tops),
                        "bookmeta 页签顶部内边距应 ≥10px，否则 hero 卡顶到页签栏（实测曾是 0px）")

    def test_section_head_keeps_distance_from_cards(self):
        css = _read("style.css")
        scoped = re.findall(
            r"#run-detail \.rd-pane \.rd-bookmeta \.bm-head\s*\{([^}]*)\}", css)
        self.assertTrue(scoped,
                        "缺少 #run-detail .rd-pane .rd-bookmeta .bm-head 覆盖：中段共用规则把"
                        "小标题下距定死成 14px，且 bible/git 共用，不能直接改")
        body = scoped[-1]
        m = re.search(r"margin\s*:\s*0\s+0\s+([\d.]+)px", body)
        self.assertIsNotNone(m, ".bm-head 覆盖里的 margin 简写应保持 `0 0 <下距>px`")
        self.assertGreaterEqual(float(m.group(1)), 14,
                                "小标题与卡片之间至少 14px（当前 %spx）" % float(m.group(1)))

    def test_publish_block_rows_are_separated(self):
        rules = _rules(_read("style.css"), ".bm-pub")
        gaps = [r for r in rules if "gap" in r]
        self.assertTrue(gaps, ".bm-pub 缺少 gap：发布块里的按钮行会糊成一坨")
        # 行距取两条 gap 值里较大的那个（后加载的覆盖生效）
        rows = []
        for body in gaps:
            m = re.search(r"gap\s*:\s*([\d.]+)px(?:\s+([\d.]+)px)?", body)
            if m:
                rows.append(float(m.group(1)))
        self.assertTrue(rows, ".bm-pub 的 gap 应写 px 数值")
        self.assertGreaterEqual(max(rows), 10,
                                "发布块按钮行距至少 10px（实测曾是 6px，四行按钮糊成一坨）")

    def test_chapter_chips_stay_readable(self):
        body = _rules(_read("style.css"), ".eh-chrows")[-1]
        self.assertGreaterEqual(_px(body, "margin-top") or 0, 10,
                                "hero 与章节胶囊行之间至少 10px")
        m = re.search(r"gap\s*:\s*([\d.]+)px", body)
        self.assertIsNotNone(m, ".eh-chrows 缺少 gap")
        self.assertGreaterEqual(float(m.group(1)), 8,
                                "章节胶囊之间至少 8px（当前 %spx）" % float(m.group(1)))

    def test_empty_eh_more_does_not_reserve_space(self):
        """空 .eh-more 的 margin 不折叠，会在 hero 卡与章节胶囊之间白占 12px（实测 24 vs 12）。"""
        rules = _rules(_read("style.css"), ".eh-more:empty")
        self.assertTrue(rules, "缺少 .eh-more:empty 规则：空的评审维度槽会白占 12px，"
                              "把 hero 与章节胶囊的间距顶成两倍")
        self.assertIn("display: none", rules[-1],
                      ".eh-more:empty 必须 display:none（只靠 margin-top:0 仍会留出占位）")

    def test_field_rows_start_lower_than_card_head(self):
        body = _rules(_read("style.css"), ".bm-fields")[-1]
        pad = _px(body, "padding")
        self.assertIsNotNone(pad, ".bm-fields 的 padding 简写不见了")
        self.assertGreaterEqual(pad, 8,
                                "字段区与卡片头的距离 ≥8px，否则第一行字段顶着卡头（实测 6px）")


if __name__ == "__main__":
    unittest.main()
