# -*- coding: utf-8 -*-
"""输入卡「专家胶囊行 + 底部上下文条」（2026-11-03 用户两张桌面端观感截图）。

用户诉求拆成两句：
  ① 「我们选专家这块，是不是也这样选？改成这种样式？」→ 输入框**上方**一排
     分组页签 + 一排专家胶囊（头像 + 人名），选中态一眼可见，不再藏在底部
     一颗「快答 ▾」下拉里；
  ② 「还有选择文件夹和分支？」→ 输入框**底部**一条「文件夹 ▾ / 分支 ▾」上下文条，
     只露目录名与分支名，而不是把整条绝对路径铺在卡顶。

这一层是纯覆盖层（composer-context.{js,css}）：删掉 index.html 里一行 link +
一行 script 即回滚。守测盯住五件事：
  1. 真源不变：#f-type / #f-workdir / #f-git-rev 仍是唯一数据源，本层只回写值；
  2. 搬节点不复制（cloneNode 会把 pickFolder/分支联动的事件绑定一起丢掉）；
  3. 被取代的两处入口（#cmp-quick、.cmp-mode）必须收掉，否则同一功能出现两排；
  4. 压住旧规则的特异性：desktop-workbench.css 有一条 2,1,0 的 `.cmp-quick{flex}`，
     本层必须用同样的父子链（`#page-settings > :is(...)`）才赢（2026-11-03 实案：
     只用本层自己的 :is() 前缀时量出来仍是 flex，快捷行照旧露在页签下面）；
  5. 最近文件夹菜单在卡底要向上翻，且不写死色值。
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


def _rules(css, selector_pattern):
    """取所有匹配的规则体（同一个选择器可能在媒体查询里再出现一次）。"""
    return re.findall(r"(?m)^\s*%s\s*\{([^}]*)\}" % selector_pattern, css)


class TestComposerContextWiring(unittest.TestCase):

    def test_overlay_files_are_wired_after_app_js(self):
        html = _read("index.html")
        self.assertIn('href="composer-context.css"', html,
                      "composer-context.css 未接入 index.html —— 胶囊行会退化成裸按钮")
        self.assertIn('src="composer-context.js"', html,
                      "composer-context.js 未接入 index.html")
        # 必须在 app.js 之后：要复用 expertAvHtml / pickQuickType / S.flows
        self.assertGreater(html.index('src="composer-context.js"'),
                           html.index('src="app.js"'),
                           "composer-context.js 必须在 app.js 之后加载，"
                           "否则拿不到 expertAvHtml 与 pickQuickType 的包装链")
        self.assertGreater(html.index('src="composer-context.js"'),
                           html.index('src="home-chips.js"'),
                           "必须在 home-chips.js 之后：它是 pickQuickType 的外层包装")
        # CSS 必须排在 style/desktop-workbench 之后，否则压不住旧规则
        self.assertGreater(html.index('href="composer-context.css"'),
                           html.index('href="desktop-workbench.css"'))


class TestExpertChips(unittest.TestCase):
    """① 专家胶囊行。"""

    def setUp(self):
        self.js = _read("composer-context.js")
        self.css = _read("composer-context.css")

    def test_experts_come_from_backend_grouping(self):
        self.assertRegex(self.js, r"\.agent\.group_order",
                         "胶囊排序必须用后端透传的 group_order，"
                         "前端另排一份会和专家页的分组顺序对不上")
        self.assertRegex(self.js, r"group\.group_name|agent && rows\[i\]\.agent\.group_name",
                         "页签名必须读 agent.group_name，不许在前端另存文案表")
        self.assertNotRegex(self.js, r"EXPERT_AVATAR_ORDER",
                            "不要在前端复制专家名单")

    def test_chip_click_goes_through_the_real_source(self):
        self.assertIn("window.pickQuickType", self.js,
                      "选人要走既有 pickQuickType（含预填示例、聚焦目标框）")
        self.assertIn('sel.dispatchEvent(new Event("change"', self.js,
                      "退化路径也必须派发 change，否则 onTypeChange 不跑")

    def test_current_expert_drives_the_selected_chip(self):
        self.assertRegex(self.js, r'currentFlowId\(\)',
                         "选中态要读 #f-type 的当前值，不能自己记一份")
        self.assertIn("expertAvHtml", self.js)

    def test_tabs_default_to_pinned(self):
        # 默认停「常用」：稀疏组（「对话速办」只有快答一位）首屏不空。
        # 只有当当前专家不在常用里时才回落到它自己那一组。
        self.assertIn('["pinned"].concat(', self.js,
                      "「常用」页签必须排在最前且由 pinnedRows() 生成")
        self.assertIn("pinnedRows", self.js)
        self.assertRegex(self.js, r"active\s*=\s*groupOf\(curFlow\)",
                         "当前专家不在「常用」里时必须回落到它所属分组，"
                         "否则会出现「选了人却看不到高亮」")

    def test_chip_list_does_not_jump_after_picking(self):
        # 点完胶囊不能把页签打回默认：刚点中的那枚会当场从行里跑掉，读起来像点了没反应
        m = re.search(r"function pickFlow\(id\) \{(.*?)\n  \}", self.js, re.S)
        self.assertIsNotNone(m, "找不到 pickFlow 函数体")
        self.assertNotIn('picked = ""', m.group(1),
                         "选人后不要重置 picked —— 行会跳回「常用」，刚点中的胶囊当场消失")

    def test_more_experts_exit_opens_the_real_page(self):
        self.assertIn("window.openExperts", self.js,
                      "「更多专家」要接既有专家页，而不是另做一个列表")

    def test_chip_row_is_one_line_only(self):
        """「默认展示一行吧」（2026-11-03 用户第三条反馈）。

        换行会让首屏高度随分组跳动（切「写作创作」时整块内容下移 38px），
        所以 CSS 锁定 nowrap，放不下的由 foldChips() 折进「更多专家」。
        """
        body = _rule(self.css, r"\.ccx-chips")
        self.assertIsNotNone(body, "缺少 .ccx-chips 基础规则")
        self.assertRegex(body, r"flex-wrap\s*:\s*nowrap",
                         "胶囊行不许换行（用户要求默认一行）")
        self.assertIn("foldChips", self.js,
                      "放不下的胶囊要折进「更多专家」，否则 nowrap 会把末尾裁掉半枚")
        self.assertRegex(self.js, r'addEventListener\("resize"',
                         "窄屏/拖窗口后要重新数能放几枚")


class TestContextBar(unittest.TestCase):
    """② 底部上下文条：目录 + 分支。"""

    def setUp(self):
        self.js = _read("composer-context.js")
        self.css = _read("composer-context.css")

    def test_nodes_are_moved_not_cloned(self):
        self.assertIn("footEl.appendChild(wd)", self.js,
                      "#f-workdir 整块搬进上下文条（id/事件绑定原地保留）")
        self.assertIn("footEl.appendChild(git)", self.js)
        self.assertNotIn("cloneNode", self.js,
                         "复制节点会丢掉 pickFolder / probeGit 挂上的监听")

    def test_cwd_pill_shows_folder_name_not_full_path(self):
        self.assertRegex(self.js, r"split\(/\[\\\\/\]/\)",
                         "目录胶囊只显示目录名（整条绝对路径铺在卡上正是用户要改掉的）")
        self.assertIn("ccx-cwd", self.js)

    def test_pill_keeps_inline_editing(self):
        # 非编辑态输入框缩成 0 宽仍可聚焦：pickFolder 取消后 pkRefocus 会 focus 它
        self.assertIn("ccx-editing", self.css)
        body = _rule(self.css, r':is\(#sub-tasks, #rd-input-slot\) \.ccx-foot \.cmp-wd:not\(\.ccx-editing\) input')
        self.assertIsNotNone(body, "缺少非编辑态输入框收起规则")
        self.assertRegex(body, r"width\s*:\s*0", "非编辑态输入框应收起到 0 宽")
        self.assertNotRegex(body, r"display\s*:\s*none",
                            "不能 display:none —— 那样 pkRefocus 的 focus 收不到，手输能力丢失")

    def test_branch_pill_is_one_row(self):
        bodies = _rules(self.css, r':is\(#sub-tasks, #rd-input-slot\) \.ccx-foot #row-git')
        self.assertTrue(bodies, "#row-git 未纳入上下文条样式")
        # 同一个选择器在文件末尾的窄屏媒体查询里还有一条只改 max-width，取「有 flex-direction 的那条」
        main = [b for b in bodies if "flex-direction" in b]
        self.assertTrue(main, "缺少 #row-git 的主规则（flex-direction 那一层）")
        self.assertRegex(main[0], r"flex-direction\s*:\s*row",
                         "#row-git 带 .field 类（默认 column），不写 row 会折成"
                         "「图标一行、下拉一行」的 56px 高块（2026-11-03 实测）")

    def test_branch_width_measured_from_current_branch(self):
        self.assertIn("fitGitPill", self.js,
                      "原生 select 的固有宽度由最长 option（提交标题）撑开，"
                      "必须按当前分支名量宽，否则小箭头会飘在胶囊中间")

    def test_recents_menu_opens_upward(self):
        body = _rule(self.css, r':is\(#sub-tasks, #rd-input-slot\) \.ccx-foot \.wd-menu')
        self.assertIsNotNone(body, "缺少上下文条内最近目录菜单的定位规则")
        self.assertRegex(body, r"bottom\s*:\s*calc\(100% \+ 6px\)",
                         "上下文条已在卡片底部，菜单必须向上翻（向下会被卡边裁掉）")
        self.assertRegex(body, r"top\s*:\s*auto", "同时要把默认的 top 清掉")


class TestSupersededEntrances(unittest.TestCase):
    """被取代的两处入口必须收掉，避免同一个功能出现两排。"""

    def setUp(self):
        self.css = _read("composer-context.css")

    def test_quick_chips_row_is_hidden_with_enough_specificity(self):
        # 关键：必须带 `#page-settings >` 前缀。desktop-workbench.css 的
        # `#page-settings > :is(#sub-tasks, #rd-input-slot) .cmp-quick{display:flex}`
        # 特异性 2,1,0，只用本层自己的 :is() 前缀（1,1,0）压不住，实测仍是 flex。
        body = _rule(self.css,
                     r'#page-settings > :is\(#sub-tasks, #rd-input-slot\) \.cmp-quick')
        self.assertIsNotNone(
            body,
            "#cmp-quick 的隐藏规则缺 `#page-settings >` 前缀："
            "desktop-workbench.css 那条 2,1,0 的 flex 会赢，快捷行照旧露在页签下面")
        self.assertRegex(body, r"display\s*:\s*none")

    def test_bottom_bar_keeps_one_expert_pill_only(self):
        # 2026-11-03 用户追加「在对话框里面是不是要体现一下」→ 底栏常驻一颗身份胶囊
        # （头像 + 专家名），所以这里不再要求 .cmp-mode display:none；真正要守的是
        # 「不要出现两排专家列表」：旧的 #cmp-quick 快捷行必须继续收掉。
        bodies = _rules(self.css, r':is\(#sub-tasks, #rd-input-slot\) \.cmp-bar \.cmp-mode')
        self.assertTrue(bodies, "底栏身份胶囊缺样式")
        self.assertRegex(bodies[-1], r"display\s*:\s*inline-flex",
                         "底栏那颗专家胶囊要常驻可见（选完人得看得出选了谁）")
        self.assertNotRegex(self.css, r'\.cmp-mode\s*\{\s*display\s*:\s*none',
                            "底栏专家胶囊不能被旧的「隐藏」规则再压回去")

    def test_empty_pills_row_is_collapsed(self):
        body = _rule(self.css, r':is\(#sub-tasks, #rd-input-slot\) \.cmp-pills\.ccx-gone')
        self.assertIsNotNone(
            body,
            "两条上下文搬走后 .cmp-pills 只剩空白文本节点，:empty 不成立，"
            "必须由 JS 打 .ccx-gone 显式收掉（否则卡顶留一条空白带）")
        self.assertRegex(body, r"display\s*:\s*none")


class TestClickLands(unittest.TestCase):
    """「点不动」（2026-11-03 用户反馈）：paint() 在 pointerdown 里重建了节点。

    CDP 实测：pointerdown 之后 `document.contains(chip) === false`，mouseup 落在
    新节点上，浏览器把 click 派给已脱离文档的旧节点 → `closest(".ccx-chip")` 为 null，
    页签/胶囊全都点不动。修法是给 paint() 加指纹，内容没变就一个字节都不写。
    """

    def setUp(self):
        self.js = _read("composer-context.js")

    def test_paint_is_skipped_when_nothing_changed(self):
        self.assertIn("lastSig", self.js,
                      "paint() 必须有指纹门：否则 pointerdown/focusin 里的重绘会把"
                      "按下的那颗按钮换掉，click 落在已脱离文档的节点上（点不动）")
        # 指纹比较必须在写 innerHTML 之前
        body = self.js[self.js.index("function paint()"):]
        self.assertLess(body.index("lastSig"), body.index("innerHTML"),
                        "指纹比较要放在写 innerHTML 之前才拦得住重绘")

    def test_pointerdown_on_own_chips_is_left_alone(self):
        self.assertRegex(self.js, r"pickEl\.contains\(e\.target\)",
                         "pointerdown 落在自己的页签/胶囊上时必须直接返回："
                         "那一下要交给 click 处理，不能在 mouseup 之前重建 DOM")

    def test_fingerprint_covers_language_and_names(self):
        # 换语言/换肤后文案要跟着走，否则英文界面会留着中文胶囊名
        for token in ("langOf()", "flowName(f)"):
            self.assertIn(token, self.js,
                          "指纹要包含语言与每个胶囊的显示名，否则换语言不重绘")


class TestBranchPillFallback(unittest.TestCase):
    """分支胶囊缺席时也要有交代（「当前属于哪个分支也没呈现出来」）。"""

    def setUp(self):
        self.js = _read("composer-context.js")
        self.css = _read("composer-context.css")

    def test_non_git_folder_is_explained(self):
        self.assertIn("ccx-git-no", self.js,
                      "默认工作目录不是 Git 仓库时 probeGit 会把 #row-git 整块隐藏，"
                      "必须补一枚「非 Git 目录」说明胶囊，否则用户以为分支功能丢了")
        self.assertIn("非 Git 目录", self.js)

    def test_fallback_follows_the_real_visibility_flag(self):
        self.assertRegex(self.js, r'classList\.contains\("hidden"\)',
                         "说明胶囊要镜像 #row-git 的 .hidden 真源，不许自己另判一次")
        self.assertRegex(self.js, r'attributeFilter:\s*\["class"\]',
                         "probeGit 只改 class，必须盯属性变化才能同步")

    def test_fallback_chip_is_not_a_button(self):
        body = _rule(self.css, r':is\(#sub-tasks, #rd-input-slot\) \.ccx-foot \.ccx-git-no')
        self.assertIsNotNone(body, "缺少 .ccx-git-no 样式")
        self.assertRegex(body, r"border\s*:\s*1px\s+dashed",
                         "说明胶囊要弱化成虚线圈（它是状态，不是可点的选项）")
        hidden = _rule(self.css, r':is\(#sub-tasks, #rd-input-slot\) \.ccx-foot \.ccx-git-no\[hidden\]')
        self.assertRegex(hidden or "", r"display\s*:\s*none",
                         "[hidden] 要显式 display:none —— 本层给了 display:inline-flex，"
                         "浏览器默认样式压不住")


class TestHomeCanvasHasNoBox(unittest.TestCase):
    """「背景上有个框感觉不安逸」：暖光被 788px 栏宽裁成了一块硬边矩形。"""

    def setUp(self):
        self.css = _read("warmth.css")

    def test_home_section_no_longer_paints_its_own_glow(self):
        bodies = _rules(self.css, r'html\[data-layout="workbench"\]\[data-skin\]:not\(\[data-skin="wb-codebee"\]\) #page-home')
        for b in bodies:
            self.assertNotIn("radial-gradient", b,
                             "#page-home 是 max-width 局部位，渐变会被它裁出硬边方框"
                             "（wb-light 下框内 #E9F2F6 / 框外 #EEF6F9，肉眼可见）")

    def test_glow_moved_to_full_bleed_main(self):
        # 选择器里必须带 :has(> #page-home:not(.hidden))：暖光只属于首页，
        # 别铺到任务详情页；main 是整幅页宽，左右不会露出竖边。
        bodies = _rules(self.css, r'html\[data-layout="workbench"\]\[data-skin\]:not\(\[data-skin="wb-codebee"\]\) main:has\(> #page-home:not\(\.hidden\)\)')
        self.assertTrue(bodies, "暖光要挂到整幅页宽的 main 上（左右天然无缝）")
        self.assertRegex(bodies[-1], r"linear-gradient\(180deg",
                         "竖向线性渐变：横向均匀，不会在左右两侧露出竖边")
        self.assertNotIn("radial-gradient", bodies[-1],
                         "回到径向就会被容器裁出边角")


class TestChatContextBar(unittest.TestCase):
    """对话页（#run-detail）的上下文条：「对话这里面这个也一起改了吧」。"""

    def setUp(self):
        self.js = _read("composer-context.js")
        self.css = _read("composer-context.css")
        # 词条断言用剥注释后的 i18n 源（词典里键=中文原文、值=英文翻译）
        self.flat_i18n = re.sub(r"//[^\n]*", "", _read("i18n.js"))

    def test_chat_bar_mounts_inside_run_detail(self):
        self.assertIn('#run-detail .rd-chat-bar', self.js,
                      "对话页上下文条要挂在对话输入卡里")
        self.assertIn("rd-chat-tools", self.js,
                      "插在 textarea 与工具栏之间，不挤占发送按钮那一行")

    def test_chat_chips_are_read_only(self):
        """三个胶囊都是 state 不是控件：任务开跑后目录/分支改不了，
        /api/runs/<id>/chat 只收 text/attachments/delivery、/api/tasks/<id>/params
        只认 mode/thinking/模型 ——         没有换专家的入口，能点但改不了就是假控件。"""
        self.assertRegex(self.css, r"\.ccx-rd-chip[^{]*\{[^}]*cursor\s*:\s*default",
                         "对话页胶囊是只读状态，不能做成 pointer 手势误导可点")
        for bad in ('onclick="', 'addEventListener("click"'):
            # .ccx-rd-foot 这一段不允许挂点击（首页胶囊行的点击在别的函数里，用段落切分判断）
            seg = self.js[self.js.index("ccx-rd-foot"):] if "ccx-rd-foot" in self.js else ""
            self.assertNotIn(bad, seg.split("function mountRd")[0],
                             "对话页上下文条不许挂点击行为（只读）")

    def test_chat_context_reads_task_not_draft(self):
        self.assertRegex(self.js, r"S\.detailTaskKey",
                         "目录/分支/专家要取当前任务自己的值，不是新任务草稿的 #f-workdir")
        self.assertRegex(self.js, r"/api/git/info\?workdir=",
                         "分支信息用首页同款接口现探（只读），不许瞎编")
        self.assertIn("非 Git 目录", self.js,
                      "任务目录不是仓库时也要把「没有分支」讲清楚")

    def test_chat_css_scoped_with_double_id(self):
        # style.css 的 #run-detail.chat-mode … 是「单 id + 单类」，单 .rd-chat 前缀压不过
        body = _rule(self.css, r'#run-detail #rd-chat \.ccx-rd-foot')
        self.assertIsNotNone(body,
                             "对话页上下文条样式必须写 #run-detail #rd-chat 双 id 前缀")

    def test_chat_expert_chip_shows_team_snapshot(self):
        """带 team_snapshot 的任务：胶囊升级为「主责 X · 团名 · N 位协作」。
        数据只取 task.team_snapshot（创建时钉住的快照），改团不影响旧任务——
        显示快照而不是实时团，与后端 store/pipeline 的口径一致。"""
        self.assertIn("task.team_snapshot", self.js,
                      "专家胶囊要读任务的团队快照")
        self.assertIn("主责 %1 · %2 · %3 位协作", self.js,
                      "带团任务的胶囊文案")
        self.assertIn("snapMembers.slice(1)", self.js,
                      "协作数 = 成员数 - 1（首位是主责）")
        self.assertIn("改团不影响本任务", self.js,
                      "title 里要把「快照已钉住」讲清楚")
        for zh in ("主责 %1 · %2 · %3 位协作",
                   "按「%1」分工执行：主责主导产出，协作在关键节点复核；改团不影响本任务"):
            self.assertIn(zh, self.flat_i18n,
                          f"词条缺 EN 翻译：{zh}")


class TestDesignTokens(unittest.TestCase):

    def test_no_hardcoded_colors(self):
        css = _read("composer-context.css")
        # 去掉注释再扫，注释里写「#rrggbb 案」不算违规
        body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        hits = re.findall(r"#[0-9a-fA-F]{3,8}\b", body)
        self.assertEqual(hits, [],
                         "设计宪法 §2：组件里不许写死十六进制色，命中 %r" % hits[:5])

    def test_new_copy_has_english(self):
        i18n = _read("i18n.js")
        for key in ("选择专家", "查看全部专家", "选择工作目录", "更多专家", "写作创作"):
            self.assertIn('"%s":' % key, i18n,
                          "新增用户可见文案必须进 i18n.js：%s" % key)

    def test_no_native_dialogs(self):
        js = _read("composer-context.js")
        for name in ("alert(", "confirm(", "prompt("):
            self.assertNotIn(name, js,
                             "原生对话框全仓禁止（tests/test_ui_native_dialog_ban.py）")


if __name__ == "__main__":
    unittest.main()
