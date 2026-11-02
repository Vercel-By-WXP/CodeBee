# -*- coding: utf-8 -*-
"""运行详情 hero「最近 5 章」不得再撞上章节评审卡的全局 .ch-row（2026-11-02 实案）。

用户截图「格式要不得」：五章被排成五张竖排小卡、章号与字数各占一行、
横跨整栏铺满。根因不是 hero 的样式写错，而是**类名撞车**——style.css 后段
（章节评审卡）把 .ch-row 定义成纵向卡片（border + border-radius:8px + 纵向
堆叠），文件更靠后、特异性相同，于是抢走了 hero 章节行的样式。

根治方式是把 hero 的章节行改名成组件族前缀 eh-chrow（hero 专属），
样式只挂在 .eh-chrows 作用域下并排成单行胶囊。两条防线：
1. 渲染侧：app.js 里 hero 章节行只允许 class="eh-chrow"，不许出现 class="ch-row"；
2. 样式侧：.eh-chrow 必须锚在 .eh-chrows 下且是横向胶囊；评审卡的全局
   .ch-row 保持原样（它服务别的页面，不能为了 hero 改掉）。
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
    """取选择器完全相等的那条规则的声明体（按顶层 } 截断）。"""
    m = re.search(r"(?m)^%s\s*\{([^}]*)\}" % re.escape(selector), css)
    return m.group(1) if m else None


class TestHeroChapterRowClass(unittest.TestCase):

    def test_hero_render_uses_prefixed_class(self):
        src = _read("app.js")
        self.assertIn('class="eh-chrow"', src,
                      "hero 章节行的渲染类名丢了 eh-chrow：会再次被评审卡的全局 .ch-row 抢样式")
        # 只允许出现 eh-chrow / eh-chrows 两种写法，不许回退到裸 ch-row
        self.assertNotIn('class="ch-row"', src,
                         "app.js 里仍有 class=\"ch-row\"：与章节评审卡全局类同名，必然串样式")

    def test_hero_rows_are_horizontal_pills(self):
        css = _read("style.css")
        wrap = _css_rule(css, ".eh-chrows")
        self.assertIsNotNone(wrap, ".eh-chrows 容器样式被删了（章节行会退化成块级纵向堆叠）")
        self.assertIn("display: flex", wrap, ".eh-chrows 必须是 flex 横排，否则又变竖排小卡")
        self.assertIn("flex-wrap: wrap", wrap, ".eh-chrows 需要 flex-wrap，窄屏要能换行而不是溢出")

        row = _css_rule(css, ".eh-chrows .eh-chrow")
        self.assertIsNotNone(row, "hero 章节行 .eh-chrows .eh-chrow 样式缺失")
        self.assertIn("inline-flex", row, "章节行应是 inline-flex 单行胶囊（章号与字数同一行）")
        self.assertIn("border-radius: 999px", row, "章节行胶囊圆角丢了，会退回方块小卡")
        for cls, label in ((".n", "章号"), (".w", "字数")):
            one = _css_rule(css, ".eh-chrows .eh-chrow %s" % cls)
            self.assertIsNotNone(one, ".eh-chrows .eh-chrow %s 样式缺失" % cls)
            self.assertIn("white-space: nowrap", one,
                          "%s 需要 nowrap，否则窄屏被拆成两行（用户原话「格式要不得」正是这个）"
                          % label)

    def test_review_card_global_rule_untouched(self):
        css = _read("style.css")
        rule = _css_rule(css, ".ch-row")
        self.assertIsNotNone(rule, "章节评审卡的全局 .ch-row 被删了——那是别的页面在用，不能连带清掉")
        self.assertIn("border-radius: 8px", rule,
                      "评审卡 .ch-row 的形态被改了：hero 的改名不该动到这份全局规则")


if __name__ == "__main__":
    unittest.main()
