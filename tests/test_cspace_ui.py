# -*- coding: utf-8 -*-
"""空间管家前端覆盖层的静态守测。

覆盖层的价值是「删两行即回滚」，所以挂载点、加载顺序、双语词条覆盖、
颜色 token 化、禁用原生对话框都要能被静态检查锁住，不必起浏览器。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "app" / "ui"
HTML = (UI / "index.html").read_text(encoding="utf-8")
JS = (UI / "cspace.js").read_text(encoding="utf-8")
CSS = (UI / "cspace.css").read_text(encoding="utf-8")


def _dict_keys():
    start = JS.find("const EN_DICT = {")
    end = JS.find("};", start)
    self_start = start
    assert self_start >= 0 and end > start, "找不到 EN_DICT 块"
    block = JS[start:end]
    # 一行里可能并列好几对词条，不能加 ^ 锚点只抓第一个
    return set(re.findall(r'"((?:[^"\\]|\\.)*)"\s*:', block))


class CspaceUiTest(unittest.TestCase):
    def test_mount_points_and_load_order(self):
        link = HTML.find('href="cspace.css')
        script = HTML.find('src="cspace.js')
        self.assertGreater(link, 0, "index.html 缺 cspace.css 引用")
        self.assertGreater(script, 0, "index.html 缺 cspace.js 引用")
        self.assertLess(link, script, "样式必须在脚本之前")
        self.assertLess(HTML.find('src="i18n.js"'), script, "必须在 i18n.js 之后")
        self.assertLess(HTML.find('src="app.js"'), script, "必须在 app.js 之后")
        self.assertLess(HTML.find('src="borrow-ui.js"'), script,
                        "审批卡来自 borrow-ui.js，必须在其后")
        self.assertEqual(1, HTML.count('href="cspace.css'), "重复挂载")
        self.assertEqual(1, HTML.count('src="cspace.js'), "重复挂载")

    def test_no_native_dialogs(self):
        for name, text in (("cspace.js", JS), ("cspace.css", CSS)):
            hits = re.findall(r"(?<![\w.])(?:window\.)?(?:alert|confirm|prompt)\s*\(",
                              text, re.I)
            self.assertEqual([], hits, "%s 用了原生对话框：%s" % (name, hits))
        self.assertIn("uiApproveCard", JS, "高危动作必须走应用内审批卡")

    def test_css_uses_tokens_only(self):
        # 设计宪法：组件里不写死十六进制色，一律消费 wb-* token。
        # 断言源码必须先剥注释行——文件头注释里会提到被禁用的属性名。
        code = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
        hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", code)
        self.assertEqual([], hexes, "cspace.css 写死了颜色：%s" % hexes)
        self.assertIn("var(--wb-", code)
        self.assertNotIn("line-clamp", code,
                         "新版 Edge 已弃用 -webkit-line-clamp，卡行数用 max-height: Nlh")

    def test_dynamic_copy_has_both_languages(self):
        used = set(re.findall(r'_t\("((?:[^"\\]|\\.)*)"\)', JS))
        self.assertGreater(len(used), 50, "动态文案抽取异常，可能是正则失配")
        registered = _dict_keys()
        missing = sorted(used - registered)
        self.assertEqual([], missing, "这些文案缺英文词条：%s" % missing)
        self.assertIn("I18N_EN", JS, "词条必须注册进同一份字典，t() 才查得到")

    def test_every_write_action_requires_confirm(self):
        # 前端四个写动作，提交体里必须带 confirm: true，且必须经审批卡
        for tail in ("clean/run", "migrate/run", "migrate/restore", "dedup/replace"):
            self.assertIn(tail, JS, "缺写动作 %s" % tail)
        self.assertGreaterEqual(JS.count("confirm: true"), 4,
                                "写动作没有显式 confirm")
        self.assertGreaterEqual(JS.count("approve("), 4,
                                "写动作没有二次确认")

    def test_default_is_readonly(self):
        self.assertIn("readonly", JS)
        self.assertIn('post("settings", { readonly: false })', JS)
        self.assertIn("只读模式下不执行", JS)

    def test_sidebar_entry_reuses_existing_nav(self):
        # 入口复用 .set-item + data-search：设置搜索只扫 .side-settings .set-item，
        # 分组归属（面包屑）只看 .active 项的前一个 .set-group，都不依赖 data-sub。
        self.assertIn("injectNav", JS)
        self.assertIn('nav.className = "set-item cs-nav"', JS)
        self.assertIn("nav.dataset.search", JS)
        self.assertIn('window.switchTab("data", "settings")', JS,
                      "覆盖层在 app.js 通用绑定之后注入，必须自己切页")
        self.assertIn("box.open = true", JS, "侧栏入口要直接展开面板")
        self.assertIn('data-sub="data"', HTML, "侧栏锚点消失会让入口静默失效")
        self.assertIn("cs-flash", CSS, "切页后要有一眼可见的定位反馈")

    def test_sidebar_entry_is_not_double_highlighted(self):
        # 2026-11-05 实拍：注入项和原生「数据与备份」都带 data-sub="data"，
        # switchTab 按 data-sub 给所有匹配项加 active → 两个胶囊同时高亮。
        # 跨文件前提锁在 app.js：一旦它改成只高亮首个匹配项，这条约束要重评。
        app_js = (UI / "app.js").read_text(encoding="utf-8")
        self.assertRegex(
            app_js,
            r'\.set-item"\)\.forEach\(\(b\) => b\.classList\.toggle\("active", !inMain && b\.dataset\.sub === name\)\)',
            "app.js switchTab 的高亮机制变了，覆盖层的互斥处理要跟着重评")
        self.assertNotIn("nav.dataset.sub", JS,
                         "注入项一旦带 data-sub 就会与原生数据页入口同时高亮")
        self.assertIn('.set-item[data-sub="data"]', JS,
                      "点自己时必须把原生项的 active 收敛掉")
        self.assertIn('nav.classList.add("active")', JS)

    def test_no_upstream_product_name_in_source(self):
        # 产品只叫 CodeBee；来源归属放仓库根 THIRD_PARTY_NOTICES.md
        targets = [UI / "cspace.js", UI / "cspace.css"]
        targets += list((ROOT / "app" / "core" / "cspace").glob("*.py"))
        for p in targets:
            text = p.read_text(encoding="utf-8")
            self.assertNotIn("C-Space", text, "%s 出现了外部产品名" % p.name)


if __name__ == "__main__":
    unittest.main()
