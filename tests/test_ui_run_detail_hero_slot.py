# -*- coding: utf-8 -*-
"""详情页 hero 带改挂「作品信息」页签（2026-11-02 用户实案）。

用户截图原话：「建议换个位置去展示这一块，现在把整个页面都压坏了」→
「建议放到作品信息？」。hero 带（封面 / 书名 / 章节·字数进度 / 最近 5 章）
在详情页顶部独占一整行，110px 起步、章节行一多更高，把对话区压成一条缝。

落地方案是**搬 DOM 节点**而不是复制一份内容：/writing 的异步回填与章节行重绘
都还写同一个 #exp-hero，不必两处维护。四条防线（缺一条就回归）：
1. 载体：#bm-hero-slot 存在，空槽不占高度（否则空 div 又撑出一段空白）；
2. 时机：面板 innerHTML 重建前必须先摘节点 + 留引用（否则 innerHTML 一冲就没了）；
3. 安全：只有「作品信息」页签真能用（bmNeedsPanel）才搬，否则 hero 搬进
   永远不显示的页签 = 直接消失；
4. 高度：chat 模式下顶部行是 180px 固定预留，搬走后必须整行删掉，
   否则对话区凭空少 180px（实测 chat 起点比面板顶低 182px）；
5. 回退（2026-11-03）：搬不进页签时不再回详情页顶部独占一行（用户「这块
   挡住页面了」），改挂右侧常驻分区面板底部——仅桌面工作台 + .is-pinned。
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


def _js_function(src, name):
    """粗略截取 `function <name>(...) {` 到下一个行首 `}` 的函数体。"""
    m = re.search(r"(?m)^function %s\s*\([^)]*\)\s*\{" % re.escape(name), src)
    if not m:
        return None
    start = m.end()
    end = src.find("\n}\n", start)
    return src[start:end if end != -1 else len(src)]


class TestRunDetailHeroRelocation(unittest.TestCase):

    def test_slot_exists_and_is_collapsed_when_empty(self):
        src = _read("app.js")
        self.assertIn('id="bm-hero-slot"', src,
                      "作品信息面板里的 hero 载体 #bm-hero-slot 被删了：relocateRunHero 会退化成直接挂面板，"
                      "下次 innerHTML 重建就丢 hero")
        css = _read("style.css")
        rule = _css_rule(css, ".bm-hero-slot:empty")
        self.assertIsNotNone(rule, ".bm-hero-slot:empty 缺失：空槽会占掉一段高度，等于没搬")
        self.assertIn("display: none", rule,
                      ".bm-hero-slot:empty 必须 display:none，否则空槽仍撑出 margin/高度")

    def test_panel_rebuild_detaches_hero_first(self):
        src = _read("app.js")
        self.assertIn("let _detachedHero = null;", src,
                      "_detachedHero 兜底引用被删了：hero 被摘出文档的瞬间 querySelector 找不到它，会永久丢失")
        self.assertIn("_detachedHero = heroNode", src,
                      "重建面板前没有暂存 hero 节点：innerHTML 一冲 hero 就没了")
        self.assertIn('const hero = $("exp-hero") || _detachedHero;', src,
                      "relocateRunHero 必须用 _detachedHero 兜底查找 hero 节点")
        # 摘节点只能走 parentElement：hero 此刻在 #bm-hero-slot 里，不在面板直下
        self.assertRegex(src, r"heroNode\.parentElement\.removeChild\(heroNode\)",
                         "摘 hero 必须用 heroNode.parentElement.removeChild，"
                         "写成 box.removeChild 会抛 NotFoundError（hero 在 slot 里，不是 box 直属子节点）")

    def test_only_moves_when_bookmeta_pane_is_usable(self):
        body = _js_function(_read("app.js"), "relocateRunHero")
        self.assertIsNotNone(body, "relocateRunHero 函数没了：hero 不会再回位/搬位")
        self.assertIn("bmNeedsPanel(task)", body,
                      "relocateRunHero 缺少 bmNeedsPanel 护栏：非连载任务的 hero 会被搬进永远不显示的页签而消失")
        self.assertIn("hero-moved", body,
                      "搬走后必须给 #run-detail 加 .hero-moved（chat 模式靠它删掉 180px 空行）")
        self.assertIn('detail.classList.remove("hero-moved", "hero-rail")', body,
                      "回位时必须摘掉 .hero-moved/.hero-rail，否则占位行消失、hero 与主区叠在一起")
        # 任务 key 从 hero 自身反解，不依赖调用时序
        self.assertIn("heroTaskOf", body,
                      "relocateRunHero 应通过 heroTaskOf(hero) 反解任务，别依赖 S.lastRunTask 的调用时序")

    def test_fallback_hero_docks_into_pinned_rail_panel(self):
        """2026-11-03 用户截图：「这块挡住页面了，要不放到右边菜单下面？」——
        hero 搬不进「作品信息」页签时不能回退到详情页顶部独占一行，要挂进
        右侧常驻分区面板（#rd-tabs-more）底部；且仅桌面工作台 + 面板常驻
        形态（.is-pinned）才搬，移动端/弹层形态下面板是隐藏下拉，搬进去
        等于 hero 消失。"""
        body = _js_function(_read("app.js"), "relocateRunHero")
        self.assertIn('$("rd-tabs-more")', body,
                      "回退分支缺少右侧面板挂载点 #rd-tabs-more：hero 会继续压在详情页顶部")
        self.assertIn('contains("is-pinned")', body,
                      "搬进面板前必须确认常驻形态 .is-pinned：弹层形态下面板隐藏，hero 会直接消失")
        self.assertIn('getAttribute("data-layout") === "workbench"', body,
                      "搬进面板前必须确认桌面工作台布局：移动端面板是隐藏下拉，不能搬")
        self.assertIn('classList.add("hero-moved", "hero-rail")', body,
                      "挂进面板后要同时打 .hero-moved/.hero-rail：chat 模式删 180px 空行靠 .hero-moved")
        css = _read("demo-parity.css")
        rule = _css_rule(css, "html[data-layout=\"workbench\"] #rd-tabs-more > #exp-hero")
        self.assertIsNotNone(
            rule,
            "缺少面板内 hero 的窄列样式：188px 面板里 hero 仍按顶部整行布局渲染会溢出")
        self.assertIn("border-top", rule,
                      "面板内 hero 需要一道上分隔线，跟任务信息折叠段分开")

    def test_repaint_paths_keep_hero_placed(self):
        src = _read("app.js")
        self.assertGreaterEqual(src.count("relocateRunHero()"), 3,
                                "relocateRunHero 调用点不足 3 处：① expertHeroPaint 短路分支、"
                                "② 绘制末尾、③ renderBookMetaPanel 重建后，缺一处就会丢位")
        # 短路分支会随渲染签名增加条件而换行，断言只钉「归位调用 + 立即返回」这个行为
        self.assertRegex(src, r"relocateRunHero\(\);\s*return;",
                         "expertHeroPaint 的「同 key 短路」分支也要重新归位，否则重绘后 hero 停在旧容器")

    def test_novel_type_task_counts_as_book_meta(self):
        """2026-11-03 用户：「要不放到作品信息里面去吧」——「穿越」任务
        type=novel、serial=false 时曾是白名单外的类型，作品信息页签不出现，
        hero 只能回退。novel 型同样是「一本书」，封面/建书/发布都要有落点。"""
        body = _js_function(_read("app.js"), "bmNeedsPanel")
        self.assertIsNotNone(body, "bmNeedsPanel 函数没了：作品信息页签会永远不出现")
        self.assertIn('t.type === "novel"', body,
                      "bmNeedsPanel 白名单缺少 novel：novel 型任务的 hero 无处可去，只能回退到顶部/右侧面板")
        self.assertIn("t.serial", body,
                      "bmNeedsPanel 必须保留 serial 兜底：带 serial 数据的任务无论 type 都算一本书")

    def test_status_pill_not_duplicated_in_panel_or_book_meta(self):
        """状态胶囊（失败/已完成）与页头徽标重复（用户圈红「这个失败是干啥的，
        是否可以不要」）——右侧面板与作品信息页签里都不显示；顶部回退位保持原样。"""
        css = _read("demo-parity.css")
        self.assertIn("html[data-layout=\"workbench\"] #rd-tabs-more > #exp-hero .eh-side",
                      css, "右侧面板里的状态胶囊没有被隐掉")
        self.assertIn("html[data-layout=\"workbench\"] .bm-hero-slot .eh-side", css,
                      "作品信息页签里的状态胶囊没有被隐掉，与页头状态重复")
        idx = css.find("html[data-layout=\"workbench\"] .bm-hero-slot .eh-side")
        rule = css[idx:css.find("}", idx)]
        self.assertIn("display: none", rule,
                      ".eh-side 的隐藏规则必须真的 display:none")
        # 顶部回退位（无 workbench 前缀）不得被误伤
        self.assertNotIn(".exp-hero-band .eh-side { display: none", css.replace(" ", " "),
                         "不许全局隐藏状态胶囊：顶部回退位（移动端）要保留")

    def test_chat_mode_drops_the_empty_hero_row(self):
        css = _read("desktop-workbench.css")
        rule = _css_rule(css, "main.chat-fill #run-detail.chat-mode.hero-moved")
        self.assertIsNotNone(
            rule,
            "缺少 main.chat-fill #run-detail.chat-mode.hero-moved 规则：chat 模式顶部行是 180px 固定预留，"
            "hero 搬走后对话区会凭空少 180px（用户原话「把整个页面都压坏了」）")
        self.assertRegex(rule, r'grid-template-areas:\s*"main rail"',
                         "hero 搬走后栅格应只剩 main/rail 两区，否则空 hero 区仍占位")
        self.assertRegex(rule, r"grid-template-rows:\s*minmax\(0,\s*1fr\)",
                         "hero 搬走后应只剩一行 minmax(0,1fr)，主区才能顶到上沿")


if __name__ == "__main__":
    unittest.main()
