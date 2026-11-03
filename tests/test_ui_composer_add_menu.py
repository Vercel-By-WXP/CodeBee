# -*- coding: utf-8 -*-
"""输入行「＋」菜单的二级结构（2026-11-03 用户实案：参考截图「按照附件样式实现」）。

## 为什么改
旧弹层是一张 340×470 的卡片，把「添加文件 / 工作流 / 浏览器 / 目标 / 计划模式 / 绘图」
和**全量**能力目录（插件 13 + 技能 203 + 智能体 18）、工作区文件、会话列表全塞进去。
线上实测（8765）：`.cmp-add-item` **628 行**、内容高 **29834px**——想点「连接器」要滚
六十屏。菜单的第一职责是「选一类东西」，不是「把仓库摊开」（DESIGN.md §0 注意力守恒）。

## 改成什么
一级卡片 6 行（添加文件 / 引用对话中的文件 / ─ / 模式 / 专家 / 技能 / 连接器），
悬停或点击在右侧张二级浮层：搜索框 + 列表 + 钉在底部的动作（召唤更多专家 / 选择文件）。

## 这份守测盯什么（都是本轮真踩过的坑）
1. 结构：`#cmp-add-menu` 里只剩 `#cmp-add-list` + `#cmp-add-flyout`，旧的全量列表容器
   `#cmp-capability-items` / `cmp-capability-filters` 不许回来（回来 = 六屏菜单复活）。
2. 搜索框必须沿用 id `cmp-capability-search`：app.js 初始化时无条件
   `$("cmp-capability-search").addEventListener(...)`，换 id 会在启动时抛 TypeError。
3. 二级浮层要能溢出：一级卡片若还是 `overflow: auto`，浮层被裁成一条。
4. 列表用 `flex: 0 1 auto`（basis:auto + shrink）——第一版写成 `flex: 1 1 0`，
   容器高度是 auto、没有「多余空间」可分配，列表直接塌成 **0 高**（实测 listClient=0）。
5. 高度钳位走 JS 实测：主页输入卡在半空，菜单向下弹时浮层只顶对齐会长出视口
   （第一版实测 bottom=986 > 视口 900）。
6. 动作复用既有 data 钩子（data-add-action / data-add-capability / data-add-chat /
   data-add-workspace-file），不重写一套点击逻辑；点专家走 window.pickExpert。
7. 文案走 t() + i18n 双语词条（DESIGN.md §7）。
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "app", "ui")


def _read(name):
    with open(os.path.join(UI, name), "r", encoding="utf-8") as fh:
        return fh.read()


def _block(css, selector):
    """取出「选择器 ... { ... }」的声明体（允许跨行；选择器按完全相等匹配）。"""
    m = re.search(r"(?m)^\s*%s\s*\{([^}]*)\}" % re.escape(selector), css, re.S)
    return m.group(1) if m else None


class TestComposerAddMenuStructure(unittest.TestCase):
    """DOM：一级 6 行 + 二级浮层，旧的全量列表不再存在。"""

    @classmethod
    def setUpClass(cls):
        cls.html = _read("index.html")
        start = cls.html.find('id="cmp-add-menu"')
        assert start > 0, "index.html 里找不到 #cmp-add-menu"
        # 取到该卡片的结束：下一层同缩进的 </div>
        tail = cls.html[start:]
        end = tail.find('</div>\n                  </div>')
        cls.menu = tail[: end + 6] if end > 0 else tail[:4000]

    def test_menu_only_holds_list_and_flyout(self):
        self.assertIn('id="cmp-add-list"', self.menu)
        self.assertIn('id="cmp-add-flyout"', self.menu)
        self.assertNotIn('id="cmp-capability-items"', self.html,
                         "旧的全量能力列表容器不许回来（628 行 / 29834px 六屏菜单）")
        self.assertNotIn("cmp-capability-filters", self.html,
                         "旧的分类筛选条已并入二级浮层的搜索")

    def test_search_input_keeps_legacy_id(self):
        # app.js 初始化时无条件绑定这个 id；换掉会在启动阶段抛 TypeError
        self.assertIn('id="cmp-capability-search"', self.html)
        self.assertIn('$("cmp-capability-search")', _read("app.js"))

    def test_flyout_footer_is_outside_scroll_area(self):
        self.assertIn('id="cmp-fly-list"', self.menu)
        self.assertIn('id="cmp-fly-foot"', self.menu)
        # 底部动作必须在滚动区之外（截图里「召唤更多专家」是钉住的）
        self.assertLess(self.menu.index('id="cmp-fly-list"'), self.menu.index('id="cmp-fly-foot"'))

    def test_page_loads_overlay_assets(self):
        # 允许带 ?v= 版本号（新增资源第一次上线时 404 会被浏览器启发式缓存，
        # 带版本号可绕开那类脏缓存）
        self.assertRegex(self.html, r'href="composer-add-menu\.css(\?[^"]*)?"')
        self.assertRegex(self.html, r'src="composer-add-menu\.js(\?[^"]*)?"')
        # 必须在 app.js 之后（要包装 window.cmpAddMenu 并复用它的全局函数）
        self.assertLess(self.html.index('src="app.js"'), self.html.index('src="composer-add-menu.js'))


class TestComposerAddMenuScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _read("composer-add-menu.js")

    def test_root_rows_order_matches_reference(self):
        order = ["添加文件", "引用对话中的文件", "模式", "专家", "技能", "连接器"]
        idx = [self.js.find('label: "%s"' % k) for k in order]
        self.assertNotIn(-1, idx, "一级六行必须齐全：%s" % order)
        self.assertEqual(idx, sorted(idx), "一级菜单顺序必须与参考截图一致")

    def test_overrides_cmp_add_menu_and_keeps_old_side_effects(self):
        self.assertIn("window.cmpAddMenu = function", self.js)
        self.assertIn("orig.call(window, open)", self.js,
                      "包装时必须继续调用 app.js 原实现：计划模式同步 / 能力目录加载都挂在它身上")

    def test_actions_reuse_existing_hooks(self):
        for hook in ('data-add-action="', 'data-add-capability="', 'data-add-chat="',
                     'data-add-workspace-file="', "window.pickExpert"):
            self.assertIn(hook, self.js, "动作必须复用既有钩子，别另写一套：%s" % hook)

    def test_reads_only_existing_apis(self):
        for path in ("/api/flows", "/api/capabilities", "/api/dir/scan"):
            self.assertIn(path, self.js)

    def test_flips_up_when_below_has_no_room(self):
        # 主页输入卡贴得低：271px 的卡片向下弹会顶出视口（线上实测 bottom 903 > 900），
        # 必须量一次上下空间后整块翻到按钮上方，而不是靠 CSS 猜方向。
        self.assertIn("function placeMenu()", self.js)
        self.assertIn('classList.add("is-up")', self.js)
        self.assertIn("window.innerHeight", self.js)

    def test_height_clamped_by_measurement(self):
        self.assertIn("function clampFly()", self.js)
        self.assertIn("window.innerHeight", self.js,
                      "浮层高度必须按视口实测钳位（主页向下弹时只有很少空间）")
        self.assertIn('classList.toggle("is-down"', self.js)


class TestComposerAddMenuCss(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = _read("composer-add-menu.css")

    def test_menu_card_can_overflow_so_flyout_is_visible(self):
        body = _block(self.css, ":is(#sub-tasks, #rd-input-slot) .cmp-add-wrap > .cmp-add-menu")
        self.assertIsNotNone(body, "一级卡片必须有覆盖规则")
        self.assertIn("overflow: visible", body,
                      "一级卡片仍 overflow:auto 会把二级浮层裁掉")

    def test_list_uses_auto_basis_not_zero(self):
        body = _block(self.css, ".cmp-fly-list")
        self.assertIsNotNone(body)
        self.assertIn("flex: 0 1 auto", body,
                      "写成 flex:1 1 0 时容器高度是 auto（无多余空间可分配），列表会塌成 0 高")
        self.assertIn("min-height: 0", body)
        self.assertIn("overflow-y: auto", body)

    def test_flyout_is_sibling_layer_with_shadow(self):
        body = _block(self.css, ":is(#sub-tasks, #rd-input-slot) .cmp-add-wrap > .cmp-add-menu > .cmp-add-flyout")
        self.assertIsNotNone(body)
        self.assertIn("position: absolute", body)
        self.assertIn("left: calc(100% + 8px)", body)
        self.assertIn("box-shadow", body, "浮层必须用浮层级投影（DESIGN.md §6）")

    def test_down_direction_rule_exists(self):
        self.assertRegex(self.css, r"\.cmp-add-flyout\.is-down\s*\{[^}]*top: 0",
                         "菜单向下弹（主页）时浮层要顶对齐，否则长出视口")

    def test_up_flip_rule_exists(self):
        self.assertRegex(self.css, r"\.cmp-add-menu\.is-up\s*\{[^}]*bottom: calc\(100% \+ 10px\)",
                         "翻转向上必须有对应的定位规则，否则 .is-up 只是加了个没用的类")

    def test_no_hardcoded_hex_colors(self):
        # 允许 rgba(...) 投影；色板必须走 token
        hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", self.css)
        self.assertEqual(hexes, [], "组件里写死十六进制会跨主题失效（DESIGN.md §2）")


class TestComposerAddMenuCopy(unittest.TestCase):
    def test_new_copy_has_english(self):
        i18n = _read("i18n.js")
        for key in ("添加文件", "引用对话中的文件", "模式", "召唤更多专家",
                    "搜索模式", "搜索会话", "搜索工作区文件"):
            self.assertRegex(i18n, r'"%s":\s*"[^"]+"' % re.escape(key),
                             "新增可见文案必须进 i18n 双语词条：%s" % key)


if __name__ == "__main__":
    unittest.main()
