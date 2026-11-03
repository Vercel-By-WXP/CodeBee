# -*- coding: utf-8 -*-
"""专家团前端接线（2026-11-03，静态断言）：子页签 / 排序 / 团卡 / 提示条 / 提交带
team_id / 存删入口 / 样式与词条。

为什么不点浏览器：这条链的「真行为」已经由 scratch/probe-mk-teams.mjs（端到端，
含提交 payload 拦截）验过；这里守的是**别在后续改动里被搬走或删掉**——DOM 钩子、
提交前的类型校验、启动加载、i18n 词条、CSS 段。测试只读源码，不启服务。
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UI = REPO / "app" / "ui"


def _read(*parts):
    return (UI.joinpath(*parts)).read_text(encoding="utf-8")


class MarketTeamsMarkupTests(unittest.TestCase):
    """index.html：专家 / 专家团两套视图都常驻，靠 hidden 切。"""

    @classmethod
    def setUpClass(cls):
        cls.html = _read("index.html")

    def test_subtabs_have_both_views(self):
        self.assertIn('id="mk-exp-tabs"', self.html)
        self.assertIn('data-x="expert"', self.html)
        self.assertIn('data-x="team"', self.html)
        self.assertIn('id="mk-exp-view-expert"', self.html)
        self.assertIn('id="mk-exp-view-team"', self.html)
        # 团队视图初始隐藏，专家视图初始可见（切页签只翻 hidden）
        self.assertRegex(self.html, r'id="mk-exp-view-team"\s+class="hidden"')

    def test_sort_controls_only_offer_real_data(self):
        # 没有「最热/最新」的数据源就不摆这两个钮（不造假控件）
        self.assertRegex(self.html, r'id="mk-sort-expert"[^>]*>[\s\S]*?data-s="default"[\s\S]*?data-s="name"')
        self.assertNotIn('data-s="hot"', self.html)
        self.assertNotIn('data-s="new"', self.html)
        self.assertRegex(self.html, r'id="mk-sort-skill"[^>]*>[\s\S]*?data-s="chars"')

    def test_team_bar_sits_above_input_card(self):
        bar = self.html.index('id="mk-team-bar"')
        card = self.html.index('<div class="rd-input-card">')
        self.assertLess(bar, card, "团提示条必须在输入卡之前（卡上方一条，不塞进卡里）")
        self.assertIn('class="mk-team-bar hidden"', self.html)

    def test_team_actions_exist(self):
        self.assertIn('id="mk-teams"', self.html)
        self.assertIn('id="mk-team-save"', self.html)
        self.assertIn('id="mk-empty-team"', self.html)


class MarketTeamsScriptTests(unittest.TestCase):
    """app.js：选团 / 提示条 / 提交条件 / 启动加载 / 事件委托。"""

    @classmethod
    def setUpClass(cls):
        cls.js = _read("app.js")
        cls.flat = re.sub(r"\s+", " ", cls.js)

    def test_team_choice_persists_across_reload(self):
        self.assertIn('localStorage.getItem("orch.team.v1")', self.js)
        self.assertIn('localStorage.setItem("orch.team.v1", id)', self.js)

    def test_submit_only_attaches_team_when_type_matches(self):
        # team_id 必须和 payload.type 一起判断，否则会把对不上的分工钉进任务
        self.assertRegex(
            self.flat,
            r"const _tid = cmpTeamId\(\); .{0,220}?payload\.team_id = _team\.id;",
        )
        self.assertIn("_team.members[0].flow_id === payload.type", self.flat)

    def test_use_team_switches_type_and_returns_home(self):
        self.assertRegex(self.flat, r"function mkUseTeam\(id\) \{.{0,600}?showHome\(\); pickType\(head\.flow_id\);")

    def test_bar_warns_instead_of_silently_dropping(self):
        # 类型对不上时要说出来，不能默默不带 team_id
        self.assertIn('t("当前类型与团主责不同，本次不会按团执行")', self.js)
        self.assertIn('bar.classList.toggle("warn", !ok)', self.js)

    def test_bar_reacts_to_type_change_and_startup(self):
        self.assertIn("loadMarketTeams,", self.js)  # 启动加载，刷新后复原上次选的团
        self.assertRegex(self.flat, r"refreshEstimate\(\); //[^!]{0,80}\s*mkRenderTeamBar\(\);")

    def test_events_are_delegated(self):
        # 团卡与提示条都是重渲染出来的节点，只能委托
        self.assertRegex(self.flat, r'\$\("mk-teams"\) && \$\("mk-teams"\)\.addEventListener\("click"')
        self.assertRegex(self.flat, r'\$\("mk-team-bar"\) && \$\("mk-team-bar"\)\.addEventListener\("click"')
        self.assertIn("closeTeamBar", self.js) if "closeTeamBar" in self.js else self.assertIn("#mk-team-clear", self.js)

    def test_dot_toggle_does_not_rebuild_grid(self):
        # 点一下 ＋ 只翻那一刻的钮：整块 renderMarketExpert() 会把刚点的钮换掉
        self.assertIn("function mkDotSync(fid, on)", self.js)
        anchor = 'const dot = e.target.closest(".mk-add-dot[data-add]");'
        self.assertIn(anchor, self.flat)
        seg = self.flat.split(anchor, 1)[1].split("return;", 1)[0]
        self.assertIn("mkDotSync(fid, on)", seg)
        self.assertNotIn("renderMarketExpert()", seg)


class MarketTeamsStyleTests(unittest.TestCase):
    """market-v4.css：新段的类名齐全，且只用 token（不写死色值）。"""

    @classmethod
    def setUpClass(cls):
        cls.css = _read("market-v4.css")

    def test_new_classes_exist(self):
        for sel in (".mk-subtabs", ".mk-subtab", ".mk-sort", ".mk-teams", ".mk-team-top",
                    ".mk-team-avs", ".mk-tav", ".mk-team-name", ".mk-team-meta", ".mk-team-tag",
                    ".mk-team-roles", ".mk-team-ops", ".mk-team-bar", ".mk-team-bar-note"):
            self.assertIn(sel, self.css, "缺样式：%s" % sel)

    def test_no_hardcoded_colors(self):
        # 允许 var(--wb-x, #fallback) 里的兜底色，但不能有裸色值
        stripped = re.sub(r"var\([^()]*(?:\([^()]*\)[^()]*)*\)", "TOKEN", self.css)
        leftovers = re.findall(r"#[0-9a-fA-F]{3,8}\b", stripped)
        self.assertEqual([], leftovers, "覆盖层里出现写死色值：%s" % leftovers[:5])

    def test_team_card_hover_does_not_move(self):
        # DESIGN §4：hover 只变描边，不位移不投影
        seg = self.css.split("#sub-market .mk-team:hover", 1)[1].split("}", 1)[0]
        self.assertNotIn("transform", seg)
        self.assertNotIn("box-shadow", seg)


class MarketTeamsCopyTests(unittest.TestCase):
    """i18n：新增界面对话在英文模式下不能整片回落中文。"""

    @classmethod
    def setUpClass(cls):
        cls.i18n = _read("i18n.js")

    def test_team_strings_translated(self):
        for key in ("专家团", "专家视图", "把我的专家存成团", "主责", "用这个团开任务",
                    "提交后任务会按这份分工执行", "当前类型与团主责不同，本次不会按团执行",
                    "长篇小说团", "代码工程团", "主笔", "复盘"):
            self.assertIn('"%s":' % key, self.i18n, "i18n 缺词条：%s" % key)

    def test_no_concatenated_sentences(self):
        # 拼接句在英文里语序不通，一律用 {0}{1} 占位
        js = _read("app.js")
        self.assertIn('t("已按「{0}」准备：主责 {1}，输入目标即可"', js)
        self.assertIn('t("删除专家团「{0}」？已建任务不受影响。"', js)


class ExpertTeamsApiTests(unittest.TestCase):
    """接口层：三个路由都在（服务无 DELETE 动词，删走 POST /delete）。"""

    @classmethod
    def setUpClass(cls):
        cls.main = (REPO / "app" / "main.py").read_text(encoding="utf-8")

    def test_routes_registered(self):
        self.assertIn('"/api/expert-teams"', self.main)
        self.assertIn('"/api/expert-teams/delete"', self.main)


if __name__ == "__main__":
    unittest.main()
