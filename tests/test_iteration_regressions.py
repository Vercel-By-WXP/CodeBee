# -*- coding: utf-8 -*-
"""知识库注入预算回归（2026-10-07 整条装箱落地件）。

block_for 旧实现超预算时对整块 text[:3000] 拦腰硬截，且 used 在截断前收满
条目 id、被截条目照常 bump hits——「从未被读到」的内容反而升权、继续挤占
预算的反馈回路缺陷。本节覆盖该行为的正常/边界/回归三面。

另（2026-10-07 属性级 i18n 落地班追加，见文件尾 IndexHtmlAttrI18nTests）：
index.html 中文 title/aria-label/placeholder 缺 data-i18n-* 钩子致英文界面
中文裸奔的缺口修复回归——「中文属性必有钩子+词条」锁成契约。
"""
from __future__ import annotations

from base import BaseTest

HEADER = "## 知识库（已确认的领域知识，供参考）\n\n"


class TestKnowledgeBlockBudget(BaseTest):
    def runTest(self):
        from app.core import knowledge
        knowledge._FILE = self.data_dir / "knowledge.json"
        today = knowledge._now()[:10]

        # —— 正常：预算内输出与改动前逐字节一致，hits 照常 +1 ——
        a = knowledge.upsert_entry("code", "构建缓存目录约定",
                                   "缓存统一放 build/.cache，清理不碰源码。",
                                   status="approved")
        b = knowledge.upsert_entry("code", "依赖升级流程",
                                   "先跑单测再合入，升级后必须回归冒烟。",
                                   status="approved")
        ents = {x["id"]: x for x in (a, b)}
        expected = HEADER + "\n".join(
            "- **%s**（事实截至 %s）：%s" % (ents[i]["title"], today, ents[i]["body"])
            for i in sorted(ents))
        text = knowledge.block_for({"type": "code"})
        self.assertEqual(text, expected)
        self.assertLessEqual(len(text), knowledge.KNOWLEDGE_BUDGET)
        self.assertNotIn("未注入", text)
        self.assertNotIn("已截断", text)
        for x in knowledge.list_entries(scope="code"):
            self.assertEqual(x["hits"], 1)

        # —— 边界：3 条 body 各 1400 字，任两条在预算内、第 3 条整条丢弃 ——
        body = "知识条目正文。" * 200   # 恰 1400 字
        t1 = knowledge.upsert_entry("novel", "连载更新节奏", body, status="approved")
        t2 = knowledge.upsert_entry("novel", "读者爽点安排", body, status="approved")
        t3 = knowledge.upsert_entry("novel", "断章钩子写法", body, status="approved")
        titles = {t1["id"]: t1["title"], t2["id"]: t2["title"], t3["id"]: t3["title"]}
        text2 = knowledge.block_for({"type": "novel"})
        self.assertLessEqual(len(text2), knowledge.KNOWLEDGE_BUDGET)
        self.assertTrue(text2.startswith(HEADER))
        self.assertTrue(text2.endswith("…（1 条超出预算未注入）"))
        self.assertEqual(text2.count("- **"), 2)   # 整条装箱恰好 2 条
        injected = [t for t in titles.values() if ("- **%s**" % t) in text2]
        dropped = [t for t in titles.values() if t not in injected]
        self.assertEqual(len(injected), 2)
        self.assertEqual(len(dropped), 1)
        # 丢弃条整条缺席（无拦腰半句）
        self.assertNotIn(dropped[0], text2)
        # hits：注入的 +1，被丢弃的不动
        hits = {x["title"]: int(x.get("hits") or 0)
                for x in knowledge.list_entries(scope="novel")}
        self.assertEqual(hits[dropped[0]], 0)
        for t in injected:
            self.assertEqual(hits[t], 1)

        # —— 回归：重复调用下被丢弃条目 hits 恒 0（切断自增强回路）——
        # 无 goal → 按 id 排序装箱，两次调用注入同一批
        knowledge.block_for({"type": "novel"})
        hits2 = {x["title"]: int(x.get("hits") or 0)
                 for x in knowledge.list_entries(scope="novel")}
        for t in injected:
            self.assertEqual(hits2[t], 2)
        self.assertEqual(hits2[dropped[0]], 0)

        # —— 边界：首条本身超过预算也必须整条丢弃，不能放行后超预算或记 hits ——
        old_budget = knowledge.KNOWLEDGE_BUDGET
        knowledge.KNOWLEDGE_BUDGET = 80
        oversized = knowledge.upsert_entry(
            "code", "单条超预算", "超长知识。" * 200, status="approved")
        try:
            text3 = knowledge.block_for({"type": "code"})
            self.assertLessEqual(len(text3), knowledge.KNOWLEDGE_BUDGET)
            self.assertNotIn("单条超预算", text3)
            self.assertEqual(
                next(x["hits"] for x in knowledge.list_entries("code")
                     if x["id"] == oversized["id"]), 0)
        finally:
            knowledge.KNOWLEDGE_BUDGET = old_budget


# ======================================================================
# 属性级 i18n 缺口修复回归（2026-10-07 落地班·第 3/4 步）
#
# index.html 部分元素的中文 title / aria-label / placeholder 挂了文案却
# 没挂 data-i18n-title / data-i18n-aria / data-i18n-ph 钩子——applyI18n
# （i18n.js）只按钩子属性重译，缺钩子的属性在英文界面恒为中文（浏览器
# 工具栏、侧栏导航、验收标准字段悬停/读屏中文裸奔）。本节把「中文属性
# 必有钩子且值一致」锁成契约，并点名锁定本轮修复面与新增词条。
# ======================================================================
import html as _html
import io as _io
import os as _os
import re as _re

_UI_DIR = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                        "app", "ui")

# 标签切分容忍引号内出现的 > 与换行（属性可跨行；注释 <!-- 不匹配）
_TAG = _re.compile(r"<[a-zA-Z](?:[^>\"']|\"[^\"]*\"|'[^']*')*>")
_ATTR = _re.compile(r'([a-zA-Z0-9-]+)="([^"]*)"')
_CJK = _re.compile(r"[一-鿿]")
_HOOK_FOR = {"title": "data-i18n-title", "aria-label": "data-i18n-aria",
             "placeholder": "data-i18n-ph"}

# 本轮修复面点名（元素锚, 钩子属性, 键原文）——防部分回退
_REPAIRED_HOOKS = (
    ('class="side-rail"', "data-i18n-aria", "主导航"),
    ('class="rail-btn" title="设置"', "data-i18n-title", "设置"),
    ('class="rail-btn" title="设置"', "data-i18n-aria", "设置"),
    ('id="btn-brand"', "data-i18n-title", "切换工作区"),
    ('id="btn-brand"', "data-i18n-aria", "切换工作区"),
    ('id="btn-cmdk"', "data-i18n-aria", "搜索"),
    ('id="brand-menu"', "data-i18n-aria", "工作区"),
    ('id="f-acceptance"', "data-i18n-ph",
     "例如：测试全部通过&#10;例如：产出文件存在"),
    ("验收标准（每行一条）</label>", "data-i18n", "验收标准（每行一条）"),
    ('id="browser-tabs"', "data-i18n-aria", "浏览器标签页"),
    ('id="browser-new-tab"', "data-i18n-title", "新建标签页"),
    ('id="browser-new-tab"', "data-i18n-aria", "新建标签页"),
    ('id="browser-back"', "data-i18n-title", "后退"),
    ('id="browser-forward"', "data-i18n-title", "前进"),
    ('id="browser-reload"', "data-i18n-title", "刷新"),
    ('id="browser-home"', "data-i18n-title", "主页"),
    ('id="browser-address"', "data-i18n-aria", "网址"),
    ('class="browser-go"', "data-i18n-title", "打开"),
    ('id="browser-open-external"', "data-i18n-title", "在系统浏览器打开"),
    ('id="browser-frame"', "data-i18n-title", "CodeBee 浏览器"),
)

# 本轮新增 EN 词条（改前全缺：英文界面 t() 未命中回退键本身）
_NEW_EN_KEYS = (
    "主导航", "切换工作区", "工作区", "验收标准（每行一条）",
    "例如：测试全部通过\n例如：产出文件存在",
    "浏览器标签页", "新建标签页", "后退", "前进", "主页", "网址",
    "在系统浏览器打开", "CodeBee 浏览器",
)


def _read_ui(name):
    with _io.open(_os.path.join(_UI_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def _i18n_entry_count(i18n, key):
    """词条出现次数：裸形态 + JS 转义形态（引号/反斜杠/\\n）。"""
    bare = i18n.count('"%s":' % key)
    if bare:
        return bare
    esc = (key.replace("\\", "\\\\").replace('"', '\\"')
              .replace("\n", "\\n"))
    return i18n.count('"%s":' % esc)


class IndexHtmlAttrI18nTests(BaseTest):
    """index.html 中文属性必有 i18n 钩子；英文界面不回退中文裸奔。"""

    def test_chinese_attrs_have_i18n_hooks(self):
        """全量对账：每个含中文的 title/aria-label/placeholder 必挂同值钩子。"""
        html_src = _read_ui("index.html")
        problems = []
        for tag in _TAG.findall(html_src):
            attrs = dict(_ATTR.findall(tag))
            for attr, hook in _HOOK_FOR.items():
                value = attrs.get(attr)
                if not value or not _CJK.search(value):
                    continue
                hooked = attrs.get(hook)
                if hooked is None:
                    problems.append("%s=%r 缺 %s" % (attr, value[:20], hook))
                elif _html.unescape(hooked) != _html.unescape(value):
                    problems.append("%s=%r 与 %s=%r 值不一致"
                                    % (attr, value[:20], hook, hooked[:20]))
        self.assertEqual(problems, [],
                         "中文属性缺 i18n 钩子（英文界面中文裸奔）：%s"
                         % " | ".join(problems[:5]))

    def test_repaired_hooks_and_entries_locked(self):
        """本轮修复面点名：16 处元素钩子在位、13 条新词条恰一条（防部分回退）。"""
        html_src = _read_ui("index.html")
        for anchor, hook, key in _REPAIRED_HOOKS:
            with self.subTest(anchor=anchor, hook=hook):
                self.assertIn(anchor, html_src)
                self.assertIn('%s="%s"' % (hook, key), html_src)
        i18n = _read_ui("i18n.js")
        for key in _NEW_EN_KEYS:
            with self.subTest(key=key):
                self.assertEqual(_i18n_entry_count(i18n, key), 1, key)


if __name__ == "__main__":
    import unittest
    unittest.main()
