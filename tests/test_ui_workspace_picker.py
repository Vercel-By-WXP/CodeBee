# -*- coding: utf-8 -*-
"""输入卡「工作空间」选择面板（2026-11-03 用户两张桌面端观感截图）。

诉求原话：「我们的选择文件夹也按照附件样式做」。附件是一张工作区选择面板：
顶部搜索框 → 工作空间列表（文件夹图标 + 名称，当前项右侧打勾）→ 分割线 →
三行动作（＋ 新建工作空间 / 打开本地文件夹 / 不使用工作空间）；配套一张
「新建工作空间」命名弹框。

实现是覆盖层 workspace-picker.{js,css}（接管 window.toggleWdMenu）+ 一条新后端
路由 /api/workdir/create。守测盯住六件事：
  1. 接入位置：CSS 压得住旧 .wd-menu（特异性 2,0,0），JS 必须在 app.js 之后；
  2. **搜索不重绘搜索框**：整块 innerHTML 只写一次，按键只重绘列表 —— 否则输入
     节点在按键途中被换掉，焦点与输入法组词一起丢（2026-11-03 点击血账同源）；
  3. 真源不变：#f-workdir 仍是唯一数据源，回填必须派发 input/change；
  4. 动作复用既有能力：打开文件夹走 pickFolder，不另造文件选择器；
  5. 弹框走应用内 openModal，不用原生 alert/confirm/prompt；
  6. 后端只允许单段目录名（会真写盘），越权字符/保留名一律拒绝。
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, "app", "ui")


def _read(name):
    with open(os.path.join(UI_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


def _src(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as fh:
        return fh.read()


class TestWorkspacePickerWiring(unittest.TestCase):

    def test_overlay_files_are_wired(self):
        html = _read("index.html")
        self.assertIn('href="workspace-picker.css', html,
                      "workspace-picker.css 未接入 index.html —— 面板会退化成旧下拉")
        self.assertIn('src="workspace-picker.js', html,
                      "workspace-picker.js 未接入 index.html")
        # JS 必须在 app.js 之后：app.js 用 window 属性赋值定义 toggleWdMenu，
        # 先加载会被它顶掉（这正是覆盖层生效的前提）
        self.assertGreater(html.index('src="workspace-picker.js'),
                           html.index('src="app.js"'),
                           "workspace-picker.js 必须在 app.js 之后加载")
        # CSS 必须排在 style.css / desktop-workbench.css 之后
        self.assertGreater(html.index('href="workspace-picker.css'),
                           html.index('href="style.css"'))

    def test_panel_rule_beats_old_wd_menu(self):
        css = _read("workspace-picker.css")
        # style.css 的 `.wd-menu` 是 (1,1,0)，本层必须抬到 (2,0,0)：:is(#id,#id) + #wd-menu
        self.assertIn(":is(#sub-tasks, #rd-input-slot) #wd-menu", css,
                      "面板规则必须用双 id 前缀，否则压不住 style.css 的 .wd-menu")
        rule = re.search(r":is\(#sub-tasks, #rd-input-slot\) #wd-menu\s*\{([^}]*)\}", css)
        self.assertIsNotNone(rule)
        body = rule.group(1)
        self.assertIn("border-radius", body)
        self.assertIn("var(--panel)", body)
        # 不写死十六进制色（DESIGN.md §2）
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", css),
                          "workspace-picker.css 里出现了写死的十六进制色")


class TestPanelStructure(unittest.TestCase):
    """① 面板：搜索框 + 列表（当前项打勾）+ 三行动作。"""

    def setUp(self):
        self.js = _read("workspace-picker.js")

    def test_search_and_actions_exist(self):
        self.assertIn("搜索工作空间", self.js)
        for act in ("new", "open", "none"):
            self.assertIn('actHtml("%s"' % act, self.js,
                          "缺少动作行 %s" % act)
        self.assertIn("新建工作空间", self.js)
        self.assertIn("打开本地文件夹", self.js)
        self.assertIn("不使用工作空间", self.js)

    def test_current_row_is_checked(self):
        self.assertIn("wsp-check", self.js)
        self.assertIn("#i-check", self.js)
        self.assertIn("is-on", self.js)

    def test_search_never_rerenders_input(self):
        # 整块 innerHTML 只能出现在 renderShell 里一次；按键只重绘 #wsp-list
        self.assertEqual(self.js.count("menu.innerHTML"), 1,
                         "整块面板只允许渲染一次：按键重绘会把搜索框节点换掉，焦点/输入法丢")
        self.assertIn("list.innerHTML = html", self.js)
        self.assertIn('$("wsp-list")', self.js)

    def test_search_input_escapes_collapsed_input_rule(self):
        # composer-context.css 把 .cmp-wd 里非编辑态的 input 压成 0 宽/透明，
        # 面板挂在 .cmp-wd 里、搜索框是它的后代 input —— 必须用更高的特异性放回来
        css = _read("workspace-picker.css")
        rule = re.search(r":is\(#sub-tasks, #rd-input-slot\) \.cmp-wd:not\(\.ccx-editing\) "
                         r"\.wsp-search > input\.wsp-q[^{]*\{([^}]*)\}", css)
        self.assertIsNotNone(rule, "搜索框必须有针对「胶囊 0 宽 input」陷阱的高特异性恢复规则")
        body = rule.group(1)
        self.assertIn("width: 100%", body)
        self.assertIn("opacity: 1", body)
        self.assertRegex(body, r"flex:\s*1 1 auto")


class TestBehaviorSources(unittest.TestCase):
    """②③④ 行为：真源回填、复用 pickFolder、新建走新路由。"""

    def setUp(self):
        self.js = _read("workspace-picker.js")

    def test_workdir_value_is_the_only_truth(self):
        self.assertIn('$("f-workdir")', self.js)
        self.assertIn('new Event("input", { bubbles: true })', self.js)
        self.assertIn('new Event("change", { bubbles: true })', self.js)

    def test_open_folder_reuses_pick_folder(self):
        self.assertIn('window.pickFolder("f-workdir")', self.js,
                      "打开本地文件夹必须复用既有 pickFolder（原生对话框 + 网页回落）")

    def test_create_posts_to_new_route(self):
        self.assertIn('"/api/workdir/create"', self.js)
        self.assertIn('method: "POST"', self.js)

    def test_overrides_toggle_wd_menu(self):
        self.assertIn("window.toggleWdMenu = function", self.js)
        # 行不用 .wd-item：app.js 的 .wd-item 委托会再处理一遍（重复回填 + 抢焦点）
        self.assertNotIn('class="wd-item', self.js)

    def test_no_native_dialogs(self):
        for bad in ("alert(", "confirm(", "prompt("):
            self.assertNotIn("window." + bad, self.js)
            self.assertIsNone(re.search(r"(?<![\w.])%s" % re.escape(bad), self.js),
                              "覆盖层里出现原生对话框：%s" % bad)
        self.assertIn("openModal", self.js)


class TestCreateDialog(unittest.TestCase):

    def test_create_dialog_uses_in_app_modal_and_is_narrow(self):
        js = _read("workspace-picker.js")
        css = _read("workspace-picker.css")
        self.assertIn('"wsp-modal"', js)
        self.assertIn("请先输入名称", js)
        # Enter 提交 / Esc 关闭（弹框里的键盘闭环）
        self.assertIn('e.key === "Enter"', js)
        self.assertIn('e.key === "Escape"', js)
        # openModal 的第 4 参挂在 .modal-wrap 上：选择器必须是「后代」，不能写成
        # `.modal.wsp-modal`（同一元素）——那样永远不命中，弹框回到 620px（2026-11-03 实测）
        rule = re.search(r"\.wsp-modal \.modal\s*\{([^}]*)\}", css)
        self.assertIsNotNone(rule, "命名弹框必须有独立窄宽度，不能沿用 620px 大弹框")
        width = int(re.search(r"max-width:\s*(\d+)px", rule.group(1)).group(1))
        self.assertLessEqual(width, 460)

    def test_known_workspaces_are_remembered(self):
        # 清空当前值后，刚创建还没跑过任务的目录不能从列表里消失
        js = _read("workspace-picker.js")
        self.assertIn('"orch.wsKnown"', js)
        self.assertIn("rememberDir", js)
        self.assertIn("knownDirs().forEach", js,
                      "列表必须合并本机记忆的目录（任务表底账覆盖不到「建了还没用」的目录）")


class TestAppJsStillRollbackable(unittest.TestCase):
    """删掉覆盖层必须能回到旧菜单：app.js 的原实现一行未动。"""

    def test_legacy_menu_logic_untouched(self):
        app = _read("app.js")
        self.assertIn("window.toggleWdMenu = function (ev)", app)
        self.assertIn("window.wdMenuPick = function (item)", app)
        self.assertIn('!e.target.closest("#wd-menu, #f-workdir-menu-btn")', app,
                      "app.js 的「点外面收起」不能丢 —— 覆盖层依赖它")


class TestBackendCreateRoute(unittest.TestCase):

    def test_route_registered(self):
        main_py = _src(os.path.join("app", "main.py"))
        self.assertIn('if path == "/api/workdir/create":', main_py)
        self.assertIn("self._api_workdir_create()", main_py)

    def test_name_validation(self):
        import sys
        sys.path.insert(0, os.path.join(ROOT, "app"))
        from main_handlers import workdir_name_error

        # 合法：普通名字、中文、带空格、点号
        for ok in ("我的项目", "demo-app", "release notes", "v1.2"):
            self.assertEqual(workdir_name_error(ok), "", "误判合法名：%s" % ok)
        # 越权：分隔符 / 上跳 / 保留字符 / Windows 保留设备名 / 空 / 过长
        for bad in ("", "  ", "../x", "a/b", "a\\b", "a:b", "a*b", "a?b",
                    'a"b', "a<b", "a>b", "a|b", ".hidden", ".", ".."):
            self.assertNotEqual(workdir_name_error(bad), "",
                                "应当拒绝的名称被放过：%r" % bad)
        for reserved in ("CON", "com1", "LPT9", "nul"):
            self.assertNotEqual(workdir_name_error(reserved), "",
                                "Windows 保留名被放过：%s" % reserved)
        self.assertNotEqual(workdir_name_error("x" * 65), "")

    def test_handler_is_local_only(self):
        src = _src(os.path.join("app", "main_handlers.py"))
        start = src.index("def _api_workdir_create")
        body = src[start:start + 2200]
        self.assertIn('if ip not in ("127.0.0.1", "::1") or fw:', body,
                      "会写盘的端点必须只允许本机调用")
        self.assertIn("workdir_name_error(name)", body)


class TestI18nCoverage(unittest.TestCase):

    def test_english_entries_present(self):
        i18n = _read("i18n.js")
        for key in ("搜索工作空间", "新建工作空间", "打开本地文件夹",
                    "不使用工作空间", "输入工作空间名称"):
            self.assertIn('"%s":' % key, i18n,
                          "新增用户可见文案必须进 i18n.js（中英双份）：%s" % key)


if __name__ == "__main__":
    unittest.main()
