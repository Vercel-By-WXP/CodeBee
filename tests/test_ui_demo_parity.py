# -*- coding: utf-8 -*-
"""demo 骨架对齐（2026-11-02 用户实案：「按照这个 demo 来改我们的页面啊，
现在根本不是这样改的啊，完全按照 demo 来啊」）。

真源：`design/codebee-demo/`（index.html + theme.css）。
落地层：`app/ui/demo-parity.css` + `app/ui/demo-parity.js`（纯覆盖层，删 <link> 即回滚）。

对照表（demo → 产品）：
  .titlebar   38px      → .topbar
  .side       228px     → --side-w（产品取 244：字号抬档后补偿名字列宽）
  .main       canvas-1  → main 背景 + .cb-scene 圆环
  .home-inner 788 居中  → .rd-home
  .task-head  56px      → #rd-rail .detail-head
  .rd-tabs    38px 横排 → #rd-tabs（产品原本是竖排导航）
  .chat-inner 790 居中  → #run-detail > .rd-main（11-03 起改为「吃满剩余宽度、右侧
                          让出常驻分区面板 204」：790 居中会与面板打架且左边空 200+）
  .dock       贴底      → .rd-chat-bar
  .settings   1120 浮层 → body.settings-mode #page-settings

最容易退化的三处，重点守住：
  1. 高度链（main → #page-settings → #sub-runs → #run-detail）：断一环 .rd-main 就塌成
     内容高、输入坞跟着内容跑；
  2. .rd-main 的右侧让位（204 / 窄屏 56）：少了它内容会被常驻分区面板压住，多了又白掉一列；
  3. 页签横排：style.css 里 `#rd-rail > #rd-tabs` 是竖排导航，覆盖必须同层级。
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
    """去掉注释与多余空白，避免注释里的字样骗过断言。"""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return re.sub(r"\s+", " ", css)


def _rule(css, selector_literal):
    """按选择器字面量（已压缩空白）取规则体，返回最后一条。"""
    flat = _flat(css)
    pat = re.escape(re.sub(r"\s+", " ", selector_literal)) + r"\s*\{([^{}]*)\}"
    hits = re.findall(pat, flat)
    return hits[-1] if hits else None


class TestDemoParityWiring(unittest.TestCase):

    def test_stylesheet_and_script_are_wired(self):
        html = _read("index.html")
        self.assertIn("demo-parity.css", html, "demo-parity.css 没有接进 index.html")
        self.assertIn("demo-parity.js", html, "demo-parity.js（圆环装饰层）没有接进 index.html")
        # 必须排在 demo-skin.css 之后，否则皮肤层会把骨架规则压回去
        self.assertGreater(html.index("demo-parity.css"), html.index("demo-skin.css"),
                           "demo-parity.css 必须排在 demo-skin.css 之后")

    def test_scene_injection_is_idempotent(self):
        js = _read("demo-parity.js")
        self.assertIn("__cbDemoParityScene", js,
                      "缺幂等守卫：每次进主页都会再插一层圆环，越插越黑")
        self.assertIn("pointer-events", js + _read("demo-parity.css"),
                      "装饰层必须不挡点击")


class TestDemoShellMetrics(unittest.TestCase):

    def setUp(self):
        self.css = _read("demo-parity.css")

    def test_sidebar_is_244(self):
        # 228 → 244（2026-11-03）：全局字号抬一档后，228 里任务名只剩 4 个字；
        # 参考产品同尺寸侧栏给的名字列更宽，244 才抵得掉字宽增量。
        self.assertRegex(_flat(self.css), r"--side-w:\s*244px",
                         "侧栏宽度按 demo 的 244px（字号抬档后的补偿值）")

    def test_status_rail_is_hidden(self):
        body = _rule(self.css, 'html[data-layout="workbench"] #status-sidebar')
        self.assertIsNotNone(body, "demo 没有右侧状态栏，必须归零/隐藏")
        self.assertIn("display: none", body)

    def test_topbar_is_38(self):
        body = _rule(self.css, 'html[data-layout="workbench"] .topbar')
        self.assertIsNotNone(body, "缺少标题栏尺寸规则")
        self.assertRegex(body, r"height:\s*38px", "demo 标题栏是 38px")

    def test_home_column_is_788_centered(self):
        body = _rule(self.css, 'html[data-layout="workbench"] .rd-home')
        self.assertIsNotNone(body, "缺少首页内容列规则")
        self.assertRegex(body, r"max-width:\s*788px", "demo 首页内容列是 788")
        # `margin: auto` 是 demo 的竖直+水平居中（.home-inner 在 flex 列里居中）
        self.assertRegex(body, r"margin:\s*(0 )?auto", "必须居中，否则贴左＝没留白")


class TestDemoDetailSkeleton(unittest.TestCase):

    def setUp(self):
        self.css = _read("demo-parity.css")

    def test_height_chain_reaches_sub_runs(self):
        """#run-detail 住在 #page-settings > #sub-runs 里，链要一路打通到 sub-runs。"""
        flat = _flat(self.css)
        self.assertIn("#page-settings > #sub-runs", flat,
                      "高度链缺少 #sub-runs 这一环：#run-detail 的 flex:1 在一个 auto "
                      "高度的块里会塌成 0（输入坞跟着塌到顶上）")
        self.assertIn("#sub-runs:not(.hidden) > #run-detail:not(.hidden)", flat,
                      "高度链条件必须带 `#run-detail:not(.hidden)`：运行记录列表态"
                      "（详情隐藏）也要给 main 打通高度，否则列表被 overflow:hidden 裁掉")

    def test_run_detail_is_flex_column(self):
        body = _rule(self.css, 'html[data-theme][data-layout="workbench"] #page-settings #run-detail')
        self.assertIsNotNone(
            body,
            "缺少 #run-detail 的 flex 列覆盖：style.css 5928 是 grid（右侧 324 栏），"
            "demo 是「任务条 + 页签 + 内容」的单列")
        self.assertIn("flex-direction: column", body)
        self.assertRegex(body, r"flex:\s*1 1 0", "高度要吃掉父级剩余空间")

    def test_content_column_fills_width_reserving_panel(self):
        # 790 居中 → 吃满剩余宽度 + 右侧让出常驻分区面板（2026-11-03 用户实拍：
        # 「两边是不是空太多了」——790 居中在 1180 主区里左右各空 195）。
        body = _rule(self.css, 'html[data-layout="workbench"] #run-detail > .rd-main')
        self.assertIsNotNone(body, "缺少内容列规则")
        self.assertRegex(body, r"max-width:\s*min\(1180px, 100%\)",
                         "内容列要吃到 1180 上限（100% 时随窗口收缩），不能再钉回 790")
        self.assertRegex(body, r"margin:\s*0 auto", "内容列必须居中")
        self.assertRegex(body, r"padding:\s*0 var\(--rd-panel-w, 204px\) 0 0",
                         "右侧必须让出常驻分区面板那一条（188 + 间距），否则被面板压住")
        self.assertRegex(body, r"flex:\s*1 1 0",
                         "内容列要 flex: 1 1 0（基准归零），否则内容一长就把输入坞挤没")
        # 面板 11-03 起还住着「操作」shelf 与「任务信息」折叠段，40px 图标条装不下
        # → 窄屏同样 188 全功能，让位不分档（旧 56px 那档已删，别再「修」回来）
        flat = _flat(self.css)
        self.assertIn('html[data-layout="workbench"] #run-detail { --rd-panel-w: 204px; }', flat,
                      "让位 204（面板 188 + 间距）没定义")
        self.assertNotIn("--rd-panel-w: 56px", flat,
                         "窄屏 56px 让位档已废弃：面板要全功能，让位必须保持 204")

    def test_meta_popover_floats_from_top_right(self):
        # 2026-11-03 二次改判（用户实拍箭头：「任务信息太长了，放到右上角点了按钮
        # 弹出框看？」）：任务信息从常驻面板的折叠段改回**浮层**，由任务条上的
        # 仪表盘按钮（#rd-meta-toggle）开合；「操作」shelf 仍常驻面板，单独有个宿主
        # #rd-actions-shelf。
        html = _read("index.html")
        flat = _flat(self.css)
        shelf_i = html.index('id="rd-actions-shelf"')
        pop_i = html.index('id="rd-meta-popover"')
        self.assertLess(shelf_i, pop_i, "#rd-actions-shelf 在面板里，浮层在它之后")
        self.assertIn('id="rd-meta-toggle"', html, "缺少右上角「任务信息」按钮")
        toggle_i = html.index('id="rd-meta-toggle"')
        self.assertLess(toggle_i, html.index('id="rd-context-toggle"'),
                        "仪表盘按钮要排在任务动态左边，两个图标都在任务条右端")
        self.assertNotIn('id="rd-meta-fold"', html, "折叠头已退役（面板里不再有那一行）")
        self.assertIn('id="rd-meta-body"', html, "缺少浮层内容容器")
        # 浮层必须挂在 #rd-rail 下：面板 #rd-tabs-more 只有 188 宽且 overflow-y:auto，
        # 浮层放进去会被裁掉（2026-11-02 血账里的「裁剪」同款坑）。
        self.assertIn('html[data-layout="workbench"] #rd-rail > #rd-meta-popover', flat,
                      "浮层定位规则必须写成 #rd-rail > #rd-meta-popover")
        self.assertNotIn('html[data-layout="workbench"] #rd-tabs-more .rd-meta-popover', flat,
                         "面板内那套 static 覆盖已退役，留着会跟浮层打架")
        self.assertIn('#rd-rail > #rd-meta-popover.hidden', flat,
                      "浮层靠 .hidden 关闭，缺这条会被常驻显示")
        for sel in ["#rd-tabs-more .cb-actions-shelf", "#rd-tabs-more .cb-shelf-body"]:
            self.assertIn(sel, _flat(self.css), f"操作区缺 {sel} 的样式")

    def test_tab_row_is_horizontal_38(self):
        flat = _flat(self.css)
        self.assertIn('#rd-rail > #rd-tabs', flat,
                      "必须在 `#rd-rail > #rd-tabs` 同层级覆盖竖排导航（style.css 那条"
                      "`flex-direction: column` 会把五个页签竖着堆成五行）")
        self.assertRegex(flat, r"height:\s*38px", "demo 页签行是 38px")

    def test_chat_flow_is_scroll_area(self):
        body = _rule(self.css, 'html[data-layout="workbench"] #run-detail .rd-chat-flow')
        self.assertIsNotNone(body, "缺少对话流规则")
        self.assertRegex(body, r"flex:\s*1 1 0",
                         "滚动内容区必须 flex: 1 1 0（记忆：定高 flex 列里 flex:1 1 auto "
                         "会按内容高抢收缩额，把输入条压成 22px）")
        self.assertRegex(body, r"padding:\s*24px 26px 8px", "demo .chat-inner 是 24 26 8")

    def test_agent_bubble_is_plain(self):
        body = _rule(self.css, 'html[data-layout="workbench"] .chat-bubble.agent')
        self.assertIsNotNone(body, "demo 的智能体回复是平铺正文，需要覆盖成无卡面")
        self.assertIn("background: transparent", body)
        self.assertIn("border: 0", body)

    def test_settings_shell_is_1120_float(self):
        body = _rule(self.css, 'html[data-layout="workbench"] body.settings-mode #page-settings')
        self.assertIsNotNone(body, "缺少设置页居中浮层规则")
        self.assertRegex(body, r"max-width:\s*1120px", "demo 设置浮层是 1120")
        self.assertRegex(body, r"border-radius:\s*16px", "demo 设置浮层圆角 16")

    def test_settings_inner_panels_are_flattened(self):
        """设置浮层已经是带边框的大卡（2026-11-03 用户「咋有两层框」）：任务/模型
        两页的 .panel.wide 若再保留自己的白卡边框就是卡中卡，必须拍平。"""
        body = _rule(
            self.css,
            'html[data-layout="workbench"] body.settings-mode #page-settings > #sub-tasks > .panel.wide, '
            'html[data-layout="workbench"] body.settings-mode #page-settings > #sub-models > .panel.wide',
        )
        self.assertIsNotNone(body, "缺少内层 panel 拍平规则")
        self.assertIn("border: 0 !important", body, "边框必须清掉（settings-v3 白卡带 !important）")
        self.assertIn("background: transparent !important", body, "背景必须透明")
        self.assertIn("box-shadow: none !important", body, "投影必须清掉")

    def test_change_summary_hides_without_run_detail(self):
        """fixed 浮卡 #change-summary 的显隐只在 side 快照轮询里刷新：closeRun
        收起详情后轮询即停，卡会僵在屏幕右上角压住运行列表（2026-11-04 实案）。
        双保险：CSS 兜底（详情不可见就收）+ closeRun 补一次刷新，缺一不可。"""
        flat = _flat(self.css)
        self.assertIn(
            'html[data-layout="workbench"] main:not(:has(#run-detail:not(.hidden))) #change-summary',
            flat,
            "缺少「详情不可见就收起浮卡」的 CSS 兜底规则",
        )
        js = _read("app.js")
        i = js.index("function closeRun()")
        seg = js[i:js.index("\nfunction ", i + 10)]
        self.assertIn("renderChangeSummary()", seg, "closeRun 必须补一次 renderChangeSummary()")
        self.assertGreater(
            seg.index("renderChangeSummary()"),
            seg.index('$("run-detail").classList.add("hidden")'),
            "renderChangeSummary() 要排在详情收起之后",
        )


class TestDemoHomeSample(unittest.TestCase):
    """demo 首页样张（用户 2026-11-02 第三次贴图：「这个样式哟，不要搞错了哟」）。

    样张顺序：待处理卡 → 问候 → 主标题 → 副标题 → 输入卡 → 最近动态。
    装饰：5 个同心环 + 25 片漂浮菱形。
    """

    def setUp(self):
        self.css = _read("demo-parity.css")
        self.js = _read("demo-parity.js")

    def test_kite_field_is_generated(self):
        self.assertIn("kitefield", self.js, "缺少菱形层容器")
        self.assertIn("KITE_SEED", self.js, "缺少菱形种子表")
        self.assertGreaterEqual(self.js.count("["), 20, "菱形种子数量不够（demo 是 25 片）")
        self.assertIn("pointer-events", self.css, "装饰层必须不挡点击")

    def test_scene_layer_wins_over_main_child_rule(self):
        """`main > *` 会把 .cb-scene 改成 position:relative，inset 失效、高度塌成 0，
        菱形全被 overflow 裁掉（实测 25 片全消失）。选择器必须压过它。"""
        flat = _flat(self.css)
        self.assertIn("main > .cb-scene", flat,
                      "`.cb-scene` 必须写成 `main > .cb-scene` 才压得过 `main > * { position: relative }`")

    def test_kite_has_fill(self):
        flat = _flat(self.css)
        self.assertRegex(flat, r"\.cb-scene \.kite \{[^}]*fill:",
                         "菱形不给 fill 会画成纯黑方块（实测）")
        self.assertRegex(flat, r"\.cb-scene \.kite \{[^}]*position: absolute")

    def test_need_card_follows_demo(self):
        body = _rule(self.css, 'html[data-layout="workbench"] #borrow-attention .ba-card')
        self.assertIsNotNone(body, "缺少待处理卡的 demo 化样式")
        self.assertIn("#fffdfa", body, "demo .need-card 是米白底 #fffdfa")
        self.assertIn("#f0dccb", body, "demo .need-card 是暖描边 #f0dccb")

    def test_home_first_screen_order(self):
        flat = _flat(self.css)
        self.assertIn("#rd-quick-start", flat, "快速开始 / 能力卡要收进高级区")
        self.assertIn("#rd-caps", flat)
        self.assertIn("#rd-chat-feed", flat,
                      "最近动态块（demo .home-recent）不见了 —— 原生 #rd-chat-feed 是唯一真源")
        self.assertIn("#btn-home-adv", flat, "「高级功能」要排到最近动态之后，首屏顺序才对得上 demo")

    def test_single_recent_block_no_duplicate(self):
        """2026-11-03 实案：首页并排出现两个「最近动态」。
        原生 chat-stream.js 已经在 composer 下挂了 `#rd-chat-feed`，覆盖层**不得**
        再把 `#home-adv` 里的 `.rd-recent-tasks` 搬出来（它本来在高级区，
        不搬就不会有第二块同名列表）。"""
        self.assertIn("#rd-chat-feed", self.css, "原生最近动态块缺少 demo 化样式")
        self.assertNotIn("relocateRecent", self.js,
                         "demo-parity.js 不得再搬「最近任务」到首页：会和 #rd-chat-feed "
                         "形成两个同名「最近动态」（实测首页重复）")
        # 判代码本体，不吃注释里的字样（注释会解释"为什么不再搬 .rd-recent-tasks"）
        js_code = re.sub(r"/\*.*?\*/", "", self.js, flags=re.S)
        self.assertNotIn(".rd-recent-tasks", js_code,
                         "搬移逻辑必须彻底移除，避免两个最近列表并存")


if __name__ == "__main__":
    unittest.main()
