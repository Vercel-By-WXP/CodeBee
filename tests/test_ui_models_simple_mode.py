# -*- coding: utf-8 -*-
"""模型接入页「简单模式」守测（源码级，不跑浏览器）。

2026-10-30 用户拍板「模型接入也一起优化，现在的太复杂了，普通傻瓜用户搞不懂」：
与智能体目录页同一套 简单/高级 分段（orch.ui-level，默认 simple）——
简单模式只留「选厂商 → 贴密钥 → 点连接」三步流；高级视图（供应商主从矩阵）原样保留。
"""

import os
import re
import unittest

UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "app", "ui")


def _read(name):
    with open(os.path.join(UI_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


def _func(src, signature):
    """从源码里切出指定签名的函数体（到下一个顶格 '}' 为止）。"""
    i = src.find(signature)
    if i < 0:
        return ""
    j = src.find("\n}", i)
    return src[i:j if j > 0 else len(src)]


class TestModelsSimpleModeHtml(unittest.TestCase):
    def setUp(self):
        self.html = _read("index.html")

    def test_mode_segment_present(self):
        seg = re.search(r'<div class="seg" id="models-mode-seg".*?</div>',
                        self.html, re.S)
        self.assertIsNotNone(seg, "模型接入页缺 #models-mode-seg 分段控件")
        self.assertIn('data-ui-level="simple"', seg.group(0))
        self.assertIn('data-ui-level="advanced"', seg.group(0))
        self.assertIn("setUiLevel('simple')", seg.group(0))
        self.assertIn("setUiLevel('advanced')", seg.group(0))

    def test_simple_container_and_advanced_layout_coexist(self):
        self.assertIn('<div id="models-simple" class="hidden">', self.html)
        m = self.html.find('id="sub-models"')
        self.assertGreater(m, 0)
        sub = self.html[m: self.html.find('id="sub-bindings"')]
        self.assertIn('class="prov-layout"', sub, "高级视图（供应商主从）不能丢")
        self.assertIn('id="prov-detail"', sub)
        self.assertIn('id="prov-list"', sub)


class TestModelsSimpleModeJs(unittest.TestCase):
    def setUp(self):
        self.js = _read("app.js")

    def test_render_models_branches_on_ui_level(self):
        body = _func(self.js, "function renderModels()")
        self.assertIn('getUiLevel() === "simple"', body)
        self.assertIn("renderModelsSimple()", body)
        self.assertIn('classList.toggle("hidden", simple)', body)

    def test_default_level_is_simple(self):
        body = _func(self.js, "function getUiLevel()")
        self.assertIn('"advanced" ? "advanced" : "simple"', body,
                      "未设置过档位时应默认 simple（傻瓜用户第一眼）")
        self.assertNotIn('=== "simple" ? "simple"', body,
                         "旧的「默认 advanced」判定应已移除")

    def test_set_ui_level_refreshes_models_page(self):
        body = _func(self.js, "function setUiLevel(")
        self.assertIn("renderModels()", body)
        self.assertIn("S.modelsSimpleSig = null", body)

    def test_seg_sync_covers_both_segments(self):
        body = _func(self.js, "function syncCliModeSeg(")
        self.assertIn("#cli-mode-seg .seg-btn", body)
        self.assertIn("#models-mode-seg .seg-btn", body)

    def test_brand_presets(self):
        block = self.js[self.js.find("const SIMPLE_BRANDS"):
                        self.js.find("function _normProvUrl")]
        for key in ("zhipu", "deepseek", "moonshot", "dashscope",
                    "openai", "anthropic", "openrouter"):
            self.assertIn('key: "%s"' % key, block, "缺品牌预设 %s" % key)
        urls = re.findall(r'url: "([^"]+)"', block)
        self.assertEqual(len(urls), 7)
        for u in urls:
            self.assertTrue(u.startswith("https://"), "预设地址必须 https：%s" % u)
            self.assertFalse(u.endswith("/"), "预设地址不能带尾斜杠（与后端落盘格式一致）")
        # 协议不预填：后端默认 auto，保存后 wire 适配探测自动补齐
        self.assertNotIn("protocol:", block)

    def test_connect_flow_updates_and_autotests(self):
        body = _func(self.js, "async function simpleConnectBrand(")
        self.assertIn("/api/models/provider", body)
        self.assertIn("if (prev) body.id = prev.id", body,
                      "同址已存在时应带 id 就地更新，不能复制出重复供应商")
        self.assertIn("refreshProviderModels(", body, "连接后应自动拉模型列表")
        self.assertIn("testProv(", body, "连接后应自动测连通")
        self.assertIn("请先粘贴这家的 API 密钥", body, "无密钥新连接必须拦下")

    def test_simple_view_blocks_and_sections(self):
        body = _func(self.js, "function renderModelsSimple()")
        for anchor in ("simpleBrandCard", "simpleOtherCard", "openImportDialog()",
                       "simpleManualAdd()", "models-simple"):
            self.assertIn(anchor, body)
        brand = _func(self.js, "function simpleBrandCard(")
        for anchor in ("sb-key-", "simpleConnectBrand", "已连接", "还差密钥"):
            self.assertIn(anchor, brand)
        other = _func(self.js, "function simpleOtherCard(")
        for anchor in ("simpleProvToggle", "simpleGotoAdvanced", "测试连接"):
            self.assertIn(anchor, other)

    def test_goto_advanced_selects_provider(self):
        body = _func(self.js, "function simpleGotoAdvanced(")
        self.assertIn("S.selProv = id", body)
        self.assertIn('S.provStatus = "all"', body,
                      "停用中的供应商需先切「全部」过滤，否则高级列表里找不到它")
        self.assertIn('setUiLevel("advanced")', body)

    def test_host_peer_hinted_not_hijacked(self):
        body = _func(self.js, "function simpleBrandHostPeer(")
        self.assertIn("indexOf(host)", body)
        card = _func(self.js, "function simpleBrandCard(")
        self.assertIn("另有同厂接入", card,
                      "同厂不同址只提示，不改已有供应商的地址")

    def test_key_input_draft_preserved(self):
        body = _func(self.js, "function renderModelsSimple()")
        self.assertIn("simpleKeyDraft", body, "轮询重绘不得清掉正在输入的密钥")


class TestModelsSimpleModeI18n(unittest.TestCase):
    def setUp(self):
        self.js = _read("i18n.js")

    def test_new_entries_exist(self):
        for key in ("粘贴 API 密钥", "高级管理", "还差密钥", "连接中…", "更新连接",
                    "常见厂商——贴上密钥点「连接」", "其他已接入的模型服务",
                    "从本机 AI 工具导入", "手动添加（高级）",
                    "内置免费模型（开箱即用）", "高级"):
            self.assertIn('"%s":' % key, self.js, "缺 EN 词条：%s" % key)


if __name__ == "__main__":
    unittest.main()
