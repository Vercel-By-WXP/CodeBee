# -*- coding: utf-8 -*-
"""蜂巢工作台（详情页 · 蜂巢页签 · 2D 列表）的间距层级（2026-11-02 用户实案）。

用户原话：「为啥详情页面咋没有这坨内容没有间隔啊，好丑啊」（截图为 01 规划 / 02 起草 /
03 评审 三段泳道与格子全糊成一片文字墙）。

现场实测：泳道 `background: transparent` + `border: 0`（只有 1px 通栏顶线）+ `radius: 0`，
格子同样透明无边界——「行化」拆过头，`01/02/03` 与各自格子的边界一起消失，只剩文字对齐。
修复保留行化密度，只把两层边界加回来：
  · 泳道 = 1px 描边 + 12px 圆角 + 面板底，段与段之间 10px（外加航线箭头）；
  · 格子 = 通栏行，行间 8px + 左侧 2px 状态条，不再叠顶线（避免与间距形成双线）。
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
    return [m.group(1) for m in re.finditer(
        r"(?m)^\s*%s\s*\{([^}]*)\}" % re.escape(selector), css)]


def _last(css, selector):
    rs = _rules(css, selector)
    return rs[-1] if rs else None


def _px(body, prop):
    m = re.search(r"(?:^|[;\s])%s\s*:\s*(-?[\d.]+)px" % re.escape(prop), body)
    return float(m.group(1)) if m else None


class TestHiveLaneSpacing(unittest.TestCase):

    def setUp(self):
        self.css = _read("style.css")

    def test_lanes_are_separate_cards(self):
        body = _last(self.css, "#rd-hive-cells .hive-lane")
        self.assertIsNotNone(body, "缺少 #rd-hive-cells .hive-lane 规则：泳道没有边界，"
                                   "01/02/03 三段会糊成一片（用户「咋没有间隔啊」）")
        self.assertNotRegex(body, r"border\s*:\s*0\s*;",
                            "泳道不许再把 border 归零：通栏发丝线查不出「段」的边界")
        self.assertIn("border: 1px solid", body, "泳道要有 1px 描边才能成形")
        self.assertRegex(body, r"border-radius:\s*1[02]px",
                         "泳道圆角至少 10px（原来是 0，跟页面底连成一片）")
        self.assertNotRegex(body, r"background\s*:\s*transparent",
                            "泳道背景不许透明：透明 + 描边仍是「文字墙」")
        self.assertIn("var(--panel)", body, "泳道底应取面板色（与页面底拉开一层）")

    def test_gap_between_lanes(self):
        bodies = [b for b in _rules(self.css, "#rd-hive-cells") if "gap" in b]
        self.assertTrue(bodies, "#rd-hive-cells 缺少 gap：段与段之间没有间距")
        gaps = [_px(b, "gap") for b in bodies]
        gaps = [g for g in gaps if g is not None]
        self.assertTrue(gaps, "#rd-hive-cells 的 gap 应写 px 数值")
        self.assertGreaterEqual(max(gaps), 8,
                                "泳道之间至少 8px（原来是 0，段与段直接相邻）")

    def test_lane_head_has_breathing_room(self):
        body = _last(self.css, "#rd-hive-cells .lane-head")
        self.assertIsNotNone(body, "缺少 #rd-hive-cells .lane-head 规则")
        gap = _px(body, "margin-bottom")
        self.assertIsNotNone(gap, ".lane-head 缺少 margin-bottom")
        self.assertGreaterEqual(gap, 8, "泳道头与格子至少 8px（原来是 6px，标题贴着内容）")

    def test_cell_rows_are_separated(self):
        body = _last(self.css, "#rd-hive-cells .lane-cells")
        self.assertIsNotNone(body, "缺少 #rd-hive-cells .lane-cells 规则")
        m = re.search(r"gap\s*:\s*([\d.]+)px(?:\s+([\d.]+)px)?", body)
        self.assertIsNotNone(m, ".lane-cells 缺少 gap")
        row_gap = float(m.group(1))
        self.assertGreaterEqual(row_gap, 6,
                                "同一泳道内格子行间距至少 6px（原来是 0，多行格子粘成一片）")

    def test_cells_have_shape(self):
        body = _last(self.css, "#rd-hive-cells .hive-cell")
        self.assertIsNotNone(body, "缺少 #rd-hive-cells .hive-cell 规则")
        self.assertNotRegex(body, r"border\s*:\s*0\s*;",
                            "格子不许再把 border 归零：会退回「纯文字行」")
        radius = re.search(r"border-radius:\s*([\d.]+)px", body)
        self.assertIsNotNone(radius, "格子要有圆角，否则 hover 也只有淡底没有形状")
        self.assertGreaterEqual(float(radius.group(1)), 6, "格子圆角至少 6px")


if __name__ == "__main__":
    unittest.main()
