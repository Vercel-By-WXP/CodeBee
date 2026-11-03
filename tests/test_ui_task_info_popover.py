# -*- coding: utf-8 -*-
"""任务信息浮层的两条产品约束（2026-11-03 用户「请一起优化」）：

1. **默认短版**：浮层里 DAG / 运行时能力证据 / 任务契约 是三块诊断内容
   （「穿越」实测合计 ~1400px），必须默认收起，点浮层底部「运行详情」才摊开；
   收浮层时复位，下次打开还是短版。
2. **手机端有入口**：≤900 时 `mobile-shell.css:586` 把 `.rd-actions` 整块
   `display:none`，任务条上的仪表盘/任务动态两个图标都不出现，手机详情只有
   「进展 / 产出」两段 —— 加第三颗「信息」，点它开同一张浮层。

任何一条被"顺手清理"，用户就得再提一次「任务信息太长了」或「手机上找不到」。
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
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return re.sub(r"\s+", " ", css)


class TestPopoverIsShortByDefault(unittest.TestCase):
    def setUp(self):
        self.html = _read("index.html")
        self.css = _flat(_read("demo-parity.css"))
        self.js = _read("app.js")

    def test_toggle_button_lives_inside_the_popover(self):
        pop_i = self.html.index('id="rd-meta-popover"')
        more_i = self.html.index('id="rd-meta-more"')
        self.assertGreater(more_i, pop_i, "「运行详情」开关必须在浮层内部（底部）")
        self.assertIn('id="rd-meta-more"', self.html)
        self.assertRegex(self.html[more_i:more_i + 400], r'aria-expanded="false"',
                         "默认必须是收起态")

    def test_heavy_blocks_are_hidden_until_is_full(self):
        for cls in ("rd-dag", "rd-capability-evidence", "rd-contract"):
            self.assertIn("#rd-meta-popover:not(.is-full) #rd-overview > ." + cls, self.css,
                          f"{cls} 必须默认收起（:not(.is-full) 隐藏）")
        self.assertIn("#rd-meta-more", self.css, "「运行详情」开关缺样式")

    def test_closing_resets_the_full_state(self):
        self.assertIn('pop.classList.remove("is-full")', self.js,
                      "收浮层不复位的话，下次打开还是长版")
        self.assertIn('classList.toggle("is-full"', self.js,
                      "开关本体要切 is-full")


class TestMobileHasAnInfoEntry(unittest.TestCase):
    def setUp(self):
        self.js = _read("mobile-workbench.js")
        self.app = _read("app.js")

    def test_segment_has_a_third_info_button(self):
        self.assertIn('data-mob-pane="meta"', self.js,
                      "手机详情分段缺「信息」按钮")
        self.assertIn('label("信息", "Info")', self.js, "「信息」文案没落")

    def test_info_button_opens_the_same_popover(self):
        seg = self.js[self.js.index('data-mob-pane="meta"'):]
        self.assertIn("setRdMetaOpen(", seg, "「信息」必须开同一张浮层，不能另造一套")
        self.assertIn('paneButton("meta")', seg)

    def test_app_syncs_the_segment_state(self):
        self.assertIn("syncMobileMetaSeg", self.app,
                      "浮层开合要回写「信息」的选中态，否则点了没反馈")
        self.assertIn("syncMobileMetaSeg(on)", self.app, "setRdMetaOpen 里没调用")


if __name__ == "__main__":
    unittest.main()
