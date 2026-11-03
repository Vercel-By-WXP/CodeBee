# -*- coding: utf-8 -*-
"""运行详情「CLI 日志」控制台的版面闸门（2026-11-03 用户第二次截图「这个样式也有问题吧」）。

线上实拍暴露的三个真问题，逐条锁死：

1. **右段被常驻分区面板吃掉**：抽屉原先占满整列宽，右沿一直伸到 1425，而右上
   常驻品牌面板（`#rd-tabs-more.is-pinned`，absolute z-index 6）钉在 1227–1415；
   抽屉自己是 `position: static`（z-index 不生效）→ 日志长行在面板边缘突然消失，
   **复制/关闭两枚按钮整个落在面板底下**（`elementFromPoint` 命中 `rd-tab-chat`，
   等于日志关不掉）。修法：`margin-right: calc(--rd-panel-w + 26px)`，右沿停在面板
   外 6px；只在 ≥981 的并排布局里让位（390 里 230px 右让会把卡片挤成 124 宽）。

2. **高度是死 40vh**：工作台布局里 `#rd-railcol` 是 `display: block`，抽屉不是
   flex item，`flex: 0 1 40vh` 与拖拽手柄写入的 `box.style.flex` 全是空操作——
   两行日志也撑出一整块空白卡，长日志则靠父级裁切、正文根本滚不动。
   修法：父级改 flex 列 + 抽屉 `flex: 0 1 auto` + `max-height: min(38vh, 360px)`。

3. **正文是「面板白 + --log-text」的跨 token 错配**，且 `word-break: break-all`
   把长路径拦腰斩断（`tutti_prompt_bx9f2c` 被切成两半）。
   修法：坐 `--log-bg` / `--log-text` 天生配对，断行改 `overflow-wrap: anywhere`。

以上是**内联形态**。同日用户又提「点击详情这个很丑，要么弹窗看都要好一些吧」，
非 chat 模式曾短暂改成一版「#rd-log fixed 贴底浮卡 + 遮罩」，实测踩了两个祖先坑
（railcol 的 backdrop-filter 改写 fixed 包含块；z-index:2 把抽屉的 260 关在
层叠上下文里），靠三条补偿规则救活。**当天根治时发现那整套是死代码**：
`toggleLog` 无条件走 `openLogModal`（同文件 hoisted，「函数缺失才兜底」的分支
不可达），`#rd-log` 全程 hidden。真弹窗是 body 层的 `#modal.log-modal`
（样式守卫在 test_ui_log_modal.py），天生不在 railcol 的层叠上下文里。
根治 = **删除整套浮层 CSS 与补偿**；第 5 节的守测相应翻成「防复活」闸门。
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, "app", "ui")

DRAWER = "#run-detail #rd-log.rd-logdrawer"
# 让位规则：内容列同宽（只在 ≥981 的并排布局 + 非对话态）
GUTTER_SEL = 'html[data-layout="workbench"] #run-detail #rd-log.rd-logdrawer'
RAILCOL = 'html[data-layout="workbench"] #run-detail > #rd-railcol'


def _read(name):
    with open(os.path.join(UI_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


def _rule_blocks(css, selector):
    """取 selector 的所有规则体（先 `{` 起按花括号配平），按出现顺序返回。

    选择器可能写在逗号分组里（`.a,\\n.b { … }`），所以 `{` 与 `,` 都算合法结尾。
    """
    out = []
    for m in re.finditer(re.escape(selector) + r"\s*[,{]", css):
        brace = css.index("{", m.end() - 1)
        start = brace
        depth = 0
        for i in range(start, len(css)):
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
                if depth == 0:
                    out.append(css[start + 1:i])
                    break
    return out


def _declared(css, selector, prop):
    """该属性在「最后一个声明它的规则体」里的值（层叠以靠后者为准）。"""
    val = None
    for block in _rule_blocks(css, selector):
        m = re.search(r"(?:^|[;\s])" + re.escape(prop) + r"\s*:\s*([^;]+);", block)
        if m:
            val = m.group(1).strip()
    return val


class TestLogDrawerLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.style = _read("style.css")
        cls.parity = _read("demo-parity.css")

    # ---- 1. 不再被右上常驻面板盖住 -------------------------------------
    def test_drawer_leaves_room_for_pinned_panel(self):
        margin = _declared(self.style, GUTTER_SEL, "margin")
        self.assertIsNotNone(margin, "日志抽屉丢了给面板让位的 margin 声明")
        self.assertIn("--rd-panel-w", margin,
                      "抽屉必须给右上常驻分区面板让位（右沿压进面板会让日志右段"
                      "被裁、复制/关闭按钮点不到）")

    def test_panel_gutter_is_desktop_only(self):
        """≤980 详情是上下堆叠、面板不常驻：230px 右让会把 390 的卡片挤成 124 宽。"""
        idx = self.style.index(GUTTER_SEL)
        head = self.style[max(0, idx - 400):idx]
        self.assertIn("@media (min-width: 981px)", head,
                      "面板让位规则必须包在 ≥981 的媒体查询里（390 手机布局没有常驻面板）")
        self.assertIn("--rd-panel-w", self.style[idx:idx + 200])
        self.assertNotIn(":not(.chat-mode)", GUTTER_SEL,
                         "对话分区的抽屉同样是整条宽（顶部任务条形态），"
                         "排除 chat-mode 会让修复在默认页签上整条失效")

    # ---- 2. 高度：内容自适应 + 上限，拖拽基准重新生效 --------------------
    def test_drawer_height_follows_content_and_is_capped(self):
        flex = _declared(self.style, DRAWER, "flex")
        self.assertIsNotNone(flex, "日志抽屉丢了 flex 声明")
        self.assertNotIn("40vh", flex,
                         "死 40vh 基准会让两行日志撑出整块空白卡（工作台布局里"
                         "父级是 display:block，flex 基准只对 flex item 有效）")
        self.assertIn("auto", flex, "flex 基准应为内容（0 1 auto），拖拽时由行内 px 覆盖")
        max_h = _declared(self.style, DRAWER, "max-height")
        self.assertIsNotNone(max_h)
        self.assertIn("min(", max_h, "长日志必须有 max-height 上限，正文自己滚")

    def test_railcol_is_flex_column_so_drag_basis_works(self):
        sel = 'html[data-layout="workbench"] #run-detail > #rd-railcol'
        display = _declared(self.parity, sel, "display")
        self.assertEqual(display, "flex",
                         "#rd-railcol 一旦回到 display:block，抽屉的 flex 基准与拖拽"
                         "手柄写入的 box.style.flex 都变成空操作")
        self.assertEqual(_declared(self.parity, sel, "flex-direction"), "column")
        gap = _declared(self.parity, sel, "gap")
        self.assertTrue(gap and gap != "0",
                        "任务条与日志卡之间要有 gap（flex 列不写 gap 会贴死）")

    # ---- 3. 正文：token 配对 + 整词断行 ---------------------------------
    def test_log_body_pairs_log_surface_tokens(self):
        bg = _declared(self.style, DRAWER + " pre", "background")
        color = _declared(self.style, DRAWER + " pre", "color")
        self.assertEqual(bg, "var(--log-bg)",
                         "日志正文必须坐 --log-bg 终端面（面板白底 + --log-text 在浅字"
                         "皮肤里读不了）")
        self.assertEqual(color, "var(--log-text)", "日志正文字色用 --log-text")

    def test_log_body_wraps_tokens_instead_of_chopping(self):
        wb = _declared(self.style, DRAWER + " pre", "word-break")
        ow = _declared(self.style, DRAWER + " pre", "overflow-wrap")
        self.assertNotEqual(wb, "break-all",
                            "break-all 会把长路径/参数拦腰斩断，改 overflow-wrap: anywhere")
        self.assertEqual(ow, "anywhere", "长 token 放不下时才断")
        lh = _declared(self.style, DRAWER + " pre", "line-height")
        self.assertIsNotNone(lh)
        self.assertGreaterEqual(float(lh.replace("px", "")) if lh.endswith("px") else float(lh),
                                1.7, "日志行高 ≥1.7，中英混排才不挤成一片")

    # ---- 4. 头部：分层线 + 胶囊状态 + 可点的小图标钮 ---------------------
    def test_bar_is_separated_from_body(self):
        border = _declared(self.style, DRAWER + " .log-live-bar", "border-bottom")
        self.assertIsNotNone(border)
        self.assertIn("var(--border)", border, "状态条与正文之间要有一条发丝分层线")

    def test_status_is_a_pill(self):
        radius = _declared(self.style, DRAWER + " .log-live", "border-radius")
        self.assertEqual(radius, "999px", "「已结束 / ● 实时」应是状态胶囊（DESIGN §4）")

    def test_copy_and_close_are_compact_icon_buttons(self):
        for cls in (".rd-log-copy", ".rd-log-x"):
            width = _declared(self.style, DRAWER + " " + cls, "width")
            height = _declared(self.style, DRAWER + " " + cls, "height")
            self.assertEqual(width, "26px", "%s 应为 26px 见方的小图标钮" % cls)
            self.assertEqual(height, "24px", "%s 高度 24px（原 41×34 撑高状态条）" % cls)

    def test_drawer_still_closes_via_hidden(self):
        self.assertIn("display: none", self.style[self.style.index(DRAWER + ".hidden"):][:80],
                      "#rd-log.hidden 的开合契约不许动（JS 只切这个类）")

    # ---- 5. 防复活闸门：#rd-log 的浮层形态已删，不许再长回来 ------------
    def test_popup_form_for_rdlog_stays_dead(self):
        """真弹窗是 body 层的 #modal.log-modal（test_ui_log_modal.py 守卫）。

        #rd-log 若再被改成 fixed 浮卡，就会重蹈当天两个祖先坑（railcol 的
        backdrop-filter 改写 fixed 包含块 → 卡片 y=-240；z-index:2 层叠上下文
        → 看得见点不着），还得再背一整套补偿规则。它只该是内联形态。
        """
        self.assertNotIn("#run-detail:not(.chat-mode) #rd-log.rd-logdrawer",
                         self.style,
                         "不要给 #rd-log 再加浮层形态：真弹窗是 #modal.log-modal，"
                         "#rd-log 只保留内联抽屉（兜底）")
        self.assertNotIn("rd-log-open", self.style,
                         ".rd-log-open 状态类只喂已删的遮罩/补偿 CSS，别再引用它")

    def test_popup_gap_does_not_leak_to_normal_state(self):
        """任务条/常驻面板不允许被任何日志状态规则改 opacity/z-index。"""
        self.assertIsNone(_declared(self.style, "#run-detail > #rd-railcol > #rd-rail", "opacity"))
        self.assertIsNone(_declared(self.style, "#run-detail > #rd-railcol", "z-index"))


if __name__ == "__main__":
    unittest.main()
