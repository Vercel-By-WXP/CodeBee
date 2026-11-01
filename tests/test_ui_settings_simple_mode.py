# -*- coding: utf-8 -*-
"""设置面板「全局默认简单模式」守测（源码级，不跑浏览器）。

2026-10-30：用户拍板「模型接入以及其它的，默认打开简单模式」+「不要左栏全局开关，
各个菜单子页面体现就是了」——
  · 不设 #settings-mode-seg 全局开关：简单/高级档位只在模型接入、本机智能体两页
    有各自的分段开关（orch.ui-level 联动），其余子页跟随档位自动折叠高级段；
  · foldAdvancedFields 随档位联动：高级=全展开，简单=对全部子页折高级段；
  · models / agents 有专属渲染，折叠逻辑在那两套视图里，不在此处折。
"""

import os
import unittest

UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "app", "ui")


def _read(name):
    with open(os.path.join(UI_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


def _func(src, signature):
    i = src.find(signature)
    if i < 0:
        return ""
    j = src.find("\n}", i)
    return src[i:j if j > 0 else len(src)]


class TestNoGlobalSegHtml(unittest.TestCase):
    def setUp(self):
        self.html = _read("index.html")

    def test_global_seg_absent(self):
        self.assertNotIn("settings-mode-seg", self.html,
                         "设置左栏不得再摆全局档位开关（用户 2026-10-30 拍板去掉）")
        self.assertNotIn("settings-mode-row", self.html)


class TestSettingsSimpleModeJs(unittest.TestCase):
    def setUp(self):
        self.js = _read("app.js")

    def test_seg_sync_does_not_reference_removed_seg(self):
        body = _func(self.js, "function syncCliModeSeg(")
        self.assertNotIn("#settings-mode-seg", body,
                         "已拆的全局开关不得残留在同步选择器里")
        self.assertIn("#cli-mode-seg .seg-btn", body)
        self.assertIn("#models-mode-seg .seg-btn", body)

    def test_switchtab_syncs_seg(self):
        body = _func(self.js, "function switchTab(")
        self.assertIn("syncCliModeSeg();", body,
                      "切设置子页时要同步两页分段开关高亮")

    def test_fold_is_mode_aware(self):
        body = _func(self.js, "function foldAdvancedFields(")
        self.assertIn('getUiLevel() === "advanced"', body,
                      "高级模式必须走全展开分支")
        self.assertIn("unfoldAdvancedFields(page)", body,
                      "高级模式要能还原上一轮简单模式的折叠")
        self.assertIn('name === "models" || name === "agents"', body,
                      "模型接入/本机智能体有专属渲染，此处不能再折")
        self.assertIn("const DEFAULT_KEEP = 2;", self.js,
                      "未列入 FOLD_KEEP 的页也要有保底折叠条数")

    def test_unfold_helper_exists(self):
        self.assertIn("function unfoldAdvancedFields(page)", self.js)

    def test_set_ui_level_refolds_current_page(self):
        body = _func(self.js, "function setUiLevel(")
        self.assertIn("foldAdvancedFields(S.tab)", body,
                      "切档后要重折当前设置子页（高级=展开 / 简单=折）")


if __name__ == "__main__":
    unittest.main()
