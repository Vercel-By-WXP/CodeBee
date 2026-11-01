# -*- coding: utf-8 -*-
"""内置功能市场卡片（2026-10-30）：禅道/群摘要/端口扫描以「连接器 → 内置功能」
区呈现在插件市场，开关委托既有 config 链路——本测守住三件事：
  1. core 聚合层只读、状态语义正确（configured/enabled/portscan 特例）；
  2. UI 三件套齐全（DOM 区块 + 渲染函数 + 委托端点不指向 builtin-features 写）；
  3. 新增可见文案有 EN 词条（i18n 扫描器对账）。
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

UI = os.path.join(os.path.dirname(__file__), "..", "app", "ui")


def _read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


def _func(js, marker):
    m = re.search(re.escape(marker) + r"[\s\S]*?\n}", js)
    return m.group(0) if m else ""


class TestBuiltinFeaturesCore(unittest.TestCase):
    """core/builtin_features.py：只读聚合 + 状态语义

    隔离姿势（2026-10-30 全量实案）：**不 reload paths、不 pop sys.modules**。
    全量跑时 core.paths 早被别的测试以仓库 DATA_DIR 加载过，pop 掉 core.zentao
    再 import 依然拿到仓库 data/（真实 zentao.json 已配置 → 本测假红），而且
    reload paths 会把污染泄漏给后续测试。改走两个模块自带的 _test_reset()
    （清空内存态 + _LOADED=True，不再读盘），零副作用。
    """

    def setUp(self):
        from app.core import builtin_features, wxdigest, zentao
        self.bif = builtin_features
        zentao._test_reset()
        wxdigest._test_reset()

    def tearDown(self):
        # 复位：内存态清空 + _LOADED=False，下一个真实使用者重新从 data/ 读盘
        from app.core import wxdigest, zentao
        zentao._test_reset()
        wxdigest._test_reset()
        zentao._LOADED = False
        wxdigest._LOADED = False

    def test_features_shape(self):
        out = self.bif.features()
        self.assertEqual([f["id"] for f in out], ["zentao", "wxdigest", "portscan"])
        for f in out:
            self.assertIn("title", f)
            self.assertIn("note", f)
            self.assertIn("settings_sub", f)
            self.assertIn("toggle_key", f)
            self.assertIn("configured", f)
            self.assertIn("enabled", f)
            self.assertIn("error", f)

    def test_fresh_state_all_unconfigured(self):
        out = {f["id"]: f for f in self.bif.features()}
        self.assertFalse(out["zentao"]["configured"])
        self.assertFalse(out["zentao"]["enabled"])
        self.assertFalse(out["wxdigest"]["configured"])
        self.assertFalse(out["wxdigest"]["enabled"])
        # portscan 特例：按需点扫、无常驻开关 → 不给 toggle_key，恒视为可用
        self.assertEqual(out["portscan"]["toggle_key"], "")
        self.assertTrue(out["portscan"]["configured"])

    def test_state_follows_config_changes(self):
        """聚合层是视图不是快照：config 变了状态跟着变（不落盘，只动内存态）"""
        from app.core import wxdigest, zentao
        before = {f["id"]: f for f in self.bif.features()}
        self.assertFalse(before["wxdigest"]["enabled"])
        wxdigest._STATE["config"]["enabled"] = True
        wxdigest._STATE["config"]["watch_dir"] = "D:\\wx-exports"
        after = {f["id"]: f for f in self.bif.features()}
        self.assertTrue(after["wxdigest"]["enabled"])
        self.assertTrue(after["wxdigest"]["configured"])
        # zentao 不动就还是原样（排除「全跟着变」的假实现）
        self.assertFalse(after["zentao"]["enabled"])
        zentao._STATE["config"]["poll_enabled"] = True
        zentao._STATE["config"]["base_url"] = "https://zt.example.com"
        zentao._STATE["config"]["account"] = "qa"
        final = {f["id"]: f for f in self.bif.features()}
        self.assertTrue(final["zentao"]["enabled"])
        self.assertTrue(final["zentao"]["configured"])

    def test_toggle_endpoints_match_modules(self):
        """卡片声明的开关字段必须与各模块 save_config 的可写字段一致"""
        from app.core import wxdigest, zentao
        zt = {f["id"]: f for f in self.bif.features()}
        self.assertIn(zt["zentao"]["toggle_key"], zentao._UPDATABLE)
        self.assertIn(zt["wxdigest"]["toggle_key"], wxdigest._UPDATABLE)

    def test_settings_sub_targets_exist(self):
        """settings_sub 必须能落到真实设置子页（id="sub-<name>"），否则卡片跳转落空"""
        html = _read(UI, "index.html")
        js = _read(UI, "app.js")
        for f in self.bif.features():
            self.assertIn('id="sub-%s"' % f["settings_sub"], html,
                          "设置页缺 sub-%s" % f["settings_sub"])
            # 左栏直达或「高级设置」卡入口至少占一头（switchTab 只认这两种导航源）
            self.assertTrue(
                'data-sub="%s"' % f["settings_sub"] in html
                or 'sub: "%s"' % f["settings_sub"] in js,
                "%s 既不在设置左栏也不在高级设置卡里" % f["settings_sub"])


class TestBuiltinFeaturesUI(unittest.TestCase):
    """前端三件套：DOM 区块 / 渲染与委托函数 / 端点纪律"""

    @classmethod
    def setUpClass(cls):
        cls.html = _read(UI, "index.html")
        cls.js = _read(UI, "app.js")

    def test_dom_block_in_conn_pane(self):
        self.assertIn('id="mk-grid-builtin"', self.html)
        self.assertIn('id="mk-builtin-reload"', self.html)
        # 区块必须在连接器页签内、且排在本地插件区之前
        start = self.html.index('id="mk-pane-conn"')
        seg = self.html[start:start + 4000]
        self.assertIn('id="mk-grid-conn"', seg, "截段里应有本地插件区")
        self.assertLess(seg.index('id="mk-grid-builtin"'), seg.index('id="mk-grid-conn"'),
                        "内置功能区应在本地插件区之前")

    def test_render_chain_wired(self):
        self.assertIn("async function loadBuiltinFeatures", self.js)
        self.assertIn("function renderBuiltinFeatures", self.js)
        self.assertIn("function builtinTileHtml", self.js)
        self.assertIn("async function builtinFeatureToggle", self.js)
        # 进 conn 页签（loadMarket 与 mkSetTab 两路）都要拉内置功能
        loads = len(re.findall(r"loadBuiltinFeatures\(\); renderMarketConn\(\)", self.js))
        self.assertGreaterEqual(loads, 2, "conn 页签两条进入路径都要刷新内置功能区")
        # 搜索联动（不绑定监听器形态——conn 分支里两个渲染函数必须同现）+ 刷新按钮
        self.assertGreaterEqual(len(re.findall(
            r"renderBuiltinFeatures\(\); renderMarketConn\(\);", self.js)), 1,
            "顶栏搜索/已安装过滤的 conn 分支要同时重绘内置功能区")
        self.assertIn('"mk-builtin-reload"', self.js)
        # 已安装计数把 enabled 的内置功能算进去
        badge = _func(self.js, "function mkSyncInstalledBadge(")
        self.assertIn("builtinFeatures", badge, "conn 页签已安装计数应含内置功能")

    def test_toggle_delegates_legacy_config_api(self):
        """开关只能走既有 /api/{zentao,wxdigest}/config，不得对 builtin-features 写"""
        body = _func(self.js, "async function builtinFeatureToggle(")
        self.assertIn("/api/zentao/config", self.js)
        self.assertIn("/api/wxdigest/config", self.js)
        api_line = re.search(r"const BUILTIN_FEATURE_API = (\{[^\n]+\})", self.js)
        self.assertTrue(api_line, "缺 BUILTIN_FEATURE_API 端点表")
        self.assertNotIn("POST", api_line.group(1))
        # builtin-features 只允许 GET（loadBuiltinFeatures 里没有任何 method:"POST"）
        loader = _func(self.js, "async function loadBuiltinFeatures(")
        self.assertNotIn("POST", loader)
        self.assertIn("builtinFeatureToggle", body)

    def test_go_jumps_to_settings(self):
        body = re.search(r"window\.builtinFeatureGo = function[\s\S]*?\n};", self.js)
        self.assertTrue(body, "缺 builtinFeatureGo")
        self.assertIn("switchTab(", body.group(0))
        # portscan 卡片不给开关（tile 渲染里 portscan 只出「去使用」）
        tile = _func(self.js, "function builtinTileHtml(")
        self.assertIn('"portscan"', tile)
        self.assertIn("去使用", tile)


class TestBuiltinFeaturesI18n(unittest.TestCase):
    def setUp(self):
        self.js = _read(UI, "i18n.js")

    def test_new_entries_exist(self):
        for key in ("内置功能",
                    "这三个能力出厂自带、不可卸载：开关即启用，配置与记录都在设置页。端口扫描无常驻调度，到设置页按需点扫。",
                    "禅道 Bug 自动修复", "微信群聊摘要", "端口扫描",
                    "未配置", "状态未知", "去使用", "去配置",
                    "内置功能状态加载失败", "没有匹配的内置功能"):
            self.assertIn('"%s":' % key, self.js, "缺 EN 词条：%s" % key)

    def test_backend_route_registered(self):
        mainpy = _read(os.path.dirname(__file__), "..", "app", "main.py")
        self.assertIn('"/api/builtin-features"', mainpy)


if __name__ == "__main__":
    unittest.main()
