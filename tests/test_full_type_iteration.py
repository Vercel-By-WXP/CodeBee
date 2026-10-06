# -*- coding: utf-8 -*-
"""全类型迭代落地核验（2026-10-05 全类型轮，第 3/4 步）：

  1. 正常：新增「演示文稿」（presentation）类型注册可用——注册表字段齐、
     dispatch 归 writing 维度、编译规格正确；
  2. 流程参数：不进 light/deep 集合，走标准单评审短链（评审稿「零额外
     改动」口径的回归锚）；交付契约接线（test_content_contracts 要求
     每个非连载 review 类型必须有契约，缺了全量红）；
  3. 回归：i-preview 图标在精灵表有定义（菜单不空白）；i18n EN 三字段
     键恰好一条（英文界面不回落中文）；
  4. 边界：帮助页「600+ 技能」静态数字去数字化——四条字符串同步后
     旧文案残留为零、新键跨端一致（app.js 中文原文 = i18n.js EN 键）；
  5. 21 时班第 3/4 步落地件机检（交接结论=零代码件，落地走文档/数据通道）：
     路线图清账四处锚点（去AI味起草+评审双接线 / 扫榜四源聚合 / 剧情模块库
     注入+模块边界截断 / 封面图生成链路）与 D 蒸馏 scope=article 路由边界
     ——「引用前须对代码实证」方法论机械化：锚点漂移即红，倒逼同步 knowledge.md。

跑法：python -m unittest discover -s tests -p "test_full_type_iteration.py" -v
"""
from __future__ import annotations

import inspect
import io
import os
import re

from base import BaseTest
from test_i18n_dups import _en_block

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP_JS = os.path.join(_ROOT, "app", "ui", "app.js")
_I18N_JS = os.path.join(_ROOT, "app", "ui", "i18n.js")
_INDEX_HTML = os.path.join(_ROOT, "app", "ui", "index.html")


def _pairs_of(block, key):
    """EN 块里 key 的全部（值）命中——条数即该键出现次数。"""
    pat = r'(?:^|[,{]\s*)"' + re.escape(key) + r'"\s*:\s*"((?:[^"\\]|\\.)*)"'
    return re.findall(pat, block)


class PresentationFlowTests(BaseTest):

    def test_flow_registered_with_full_meta(self):
        """正常路径：presentation 注册、字段齐、如实声明不生成 PPT 二进制。"""
        from app.core import flows
        f = flows.get_flow("presentation")
        self.assertIsNotNone(f)
        self.assertTrue(f["builtin"])
        self.assertEqual(f["engine"], "review")
        self.assertEqual(f["manuscript"], "presentation.md")
        self.assertEqual(f["icon"], "i-preview")
        self.assertEqual(f["rubric"], ["结构逻辑", "内容密度", "视觉呈现", "演讲适配"])
        self.assertEqual(f["threshold"], 7.0)
        self.assertEqual(f["rounds"], 2)
        self.assertTrue(f["goal_hint"] and f["note"])
        self.assertIn("不生成 PPT", f["note"], "note 必须如实声明产出形态（防预期错位）")

    def test_dimension_writing_and_compile_spec(self):
        """dispatch 归 writing 维度；编译规格引擎/难度/产出文件正确。"""
        from app.core import dispatch, task_compile
        self.assertEqual(dispatch.TYPE_DIMENSIONS.get("presentation"), "writing")
        spec = task_compile.compile_task({"type": "presentation", "goal": "产品评审演示"})
        self.assertEqual(spec["engine"], "review")
        self.assertEqual(spec["dimension"], "writing")
        self.assertEqual(spec["difficulty"], "default")   # 非研究/方案类，不默认 hard
        self.assertEqual(spec["deliverable"], "presentation.md")
        self.assertEqual(spec["quality_dimensions"][0], "结构逻辑")

    def test_standard_review_lane_not_light_not_deep(self):
        """流程参数：标准难度走单评审短链（不进 light/deep 集合的口径锚）。"""
        from app.core import task_compile
        wf = task_compile.content_workflow({"type": "presentation", "rounds": 2}, "default")
        self.assertFalse(wf["outline"])
        self.assertEqual(wf["reviewers"], 1)
        self.assertEqual(wf["reason"], "标准内容使用单评审短链")

    def test_delivery_contract_wired(self):
        """交付契约接线：角色非兜底、约束块含逐页产出与不生成 PPT 声明。"""
        from app.core import pipeline
        role = pipeline._content_role({"type": "presentation"})
        self.assertNotEqual("内容交付专家", role)
        c = pipeline._content_contract({"type": "presentation"})
        self.assertIn("本类型交付约束", c)
        self.assertIn("逐页", c)
        self.assertIn("不生成 PPT", c)

    def test_icon_symbol_defined_in_sprite(self):
        """回归：i-preview 在 index.html 精灵表有 symbol 定义（菜单图标不空白）。"""
        with io.open(_INDEX_HTML, encoding="utf-8") as fh:
            html = fh.read()
        self.assertGreaterEqual(len(re.findall(r'symbol id="i-preview"', html)), 1)

    def test_i18n_en_keys_exactly_once(self):
        """回归：name/goal_hint/note 三字段 EN 键各恰好一条（缺=英文界面回落中文）。"""
        from app.core.flows import BUILTIN_FLOWS
        block = _en_block()
        by_id = {f["id"]: f for f in BUILTIN_FLOWS}
        for field in ("name", "goal_hint", "note"):
            text = by_id["presentation"][field]
            hits = _pairs_of(block, text)
            self.assertEqual(len(hits), 1,
                             "presentation.%s 应恰有一条 EN 译: %r" % (field, text))
            self.assertTrue(hits[0].strip())


class MarketplaceCopyDequantifiedTests(BaseTest):
    """候选 2：帮助页「600+ 技能」静态数字去数字化（六源缓存实数 810 且随上游
    浮动，静态下限必然持续过期——G 项巡检唯一观察项）。"""

    def test_no_stale_number_in_app_js(self):
        with io.open(_APP_JS, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("600+ 技能", src, "app.js 残留「600+ 技能」静态数字")

    def test_new_cn_key_matches_en_exactly_once(self):
        with io.open(_APP_JS, encoding="utf-8") as fh:
            app_src = fh.read()
        with io.open(_I18N_JS, encoding="utf-8") as fh:
            i18n_src = fh.read()
        self.assertIn("t(\"技能一键安装\")", app_src, "app.js 应使用新文案键")
        hits = _pairs_of(_en_block(), "技能一键安装")
        self.assertEqual(len(hits), 1, "新键应恰有一条 EN 译")
        self.assertEqual(hits[0], "Skills install in one click")
        # 旧键两条不得残留（后值覆盖前值的隐形文案事故形态）
        self.assertNotIn("600+ 技能，一键安装", i18n_src)
        self.assertNotIn("600+ 技能一键安装", i18n_src)


_MAIN_PY = os.path.join(_ROOT, "app", "main.py")


class RoadmapClearingAnchorTests(BaseTest):
    """路线图清账（knowledge.md 2026-10-05 21 时班）四处锚点机检 + D 蒸馏路由。

    旧问题复现：路线图复选框入库后长期挂账，且历史上有「引用未实证先划掉」
    事故（07 时班方法论）——本组把清账引用的代码锚点锁进测试：日后重构改名
    导致 knowledge.md 引用失效时在此先红，而不是留下一份说谎的台账。
    """

    def test_aiflavor_wired_into_draft_and_review(self):
        """去AI味：确定性检测器可用、注入位置正确，且起草/评审两处都接线。"""
        from app.core import aiflavor, pipeline
        for fn in ("analyze", "narrative_analyze", "pacing_analyze",
                   "inject_into_prompt"):
            self.assertTrue(callable(getattr(aiflavor, fn, None)), fn)
        tpl = "评审要求：套话密度纳入参考。\n\n## 待评审稿件\n他推门进来。"
        hit = aiflavor.inject_into_prompt(tpl, "值得注意的是，" * 40 + "他走了。")
        self.assertIn("## 确定性检测结果（供评审参考）", hit, "命中时必须下发检测块")
        self.assertEqual(hit.count("## 待评审稿件"), 1)
        self.assertLess(hit.index("## 确定性检测结果"), hit.index("## 待评审稿件"),
                        "检测块固定插在稿件段之前（前缀稳定纪律）")
        # 零噪音边界：无命中原样返回（邮件类非叙事文体不注入节奏统计）
        clean = aiflavor.inject_into_prompt(tpl, "今天天气很好。", kind="email")
        self.assertEqual(clean, tpl)
        # 接线锚点：起草与评审两个消费点都在（缺一处=旗舰场景漏挂的事故形态）
        src = inspect.getsource(pipeline)
        self.assertIn('aiflavor.inject_into_prompt(tpl, text, task.get("type"))', src,
                      "起草侧未接 aiflavor")
        self.assertIn(
            'aiflavor.inject_into_prompt(crit_prompt, manuscript, task.get("type"))',
            src, "评审侧未接 aiflavor")

    def test_rank_scan_wired_to_four_platform_fetchers(self):
        """扫榜选材：rank_scan 注册为 direct 引擎，paihang 四源聚合接线在位。"""
        import inspect
        from app.core import flows, paihang
        f = flows.get_flow("rank_scan")
        self.assertIsNotNone(f)
        self.assertEqual(f["engine"], "direct")
        fetchers = ("fetch_qimao_rank", "fetch_fanqie_rank",
                    "fetch_qidian_rank", "fetch_zongheng_rank")
        for fn in fetchers + ("fetch_rank_items", "rank_scan_prompt"):
            self.assertTrue(callable(getattr(paihang, fn, None)), fn)
        # 聚合器按 _SOURCES 名单间接派发（globals()[fname]）——四源名单必须齐
        src_names = [fname for _label, fname in paihang._SOURCES]
        for fn in fetchers:
            self.assertIn(fn, src_names, "聚合名单缺 %s（四平台口径残缺）" % fn)

    def test_plot_modules_injection_and_boundary_truncation(self):
        """剧情模块库：约定式注入（无文件零噪音）+ 超预算按模块边界截断不腰斩。"""
        from app.core import pipeline
        empty = os.path.join(self.tmp, "no-modules-work")
        os.mkdir(empty)
        self.assertEqual(pipeline._plot_modules(empty), "",
                         "无模块库必须零噪音（约定式功能）")
        with io.open(os.path.join(self.workdir, pipeline.MODULES_FILE),
                     "w", encoding="utf-8") as fh:
            fh.write("## 模块01\n反转揭底。MOD01_END\n\n## 模块02\n误会消解。MOD02_END\n")
        mods = pipeline._plot_modules(self.workdir)
        self.assertIn("## 剧情模块库", mods)
        for tag in ("MOD01_END", "MOD02_END"):
            self.assertIn(tag, mods)
        src = inspect.getsource(pipeline)
        self.assertIn("mods = _plot_modules(workdir)", src, "连载注入块未接模块库")
        # 边界：超预算触发「模块库→边界截断」，且截断只落在 "\n## " 模块边界——
        # 保留的每个模块必须完整（哨兵在=未腰斩），被截掉的整模块消失
        head = "## 世界观\n大陆设定。\n\n"
        parts = [head, "## 剧情模块库"]
        for i in range(1, 17):
            parts.append("\n## 模块%02d\n%sMOD%02d_END\n"
                         % (i, ("素材%02d。" % i) * 220, i))
        bible = "".join(parts)
        sk = "x" * 200
        self.assertGreater(len(sk) + len(bible), 12000, "样例必须真的超预算")
        sk2, bible2, notes = pipeline._shrink_context_block(sk, bible)
        self.assertIn("模块库→边界截断", notes)
        self.assertIn("（模块库已因上下文容量限制精简）", bible2)
        kept = re.findall(r"## 模块(\d\d)", bible2)
        self.assertLess(len(kept), 16, "截断必须真的发生")
        for nn in kept:
            self.assertIn("MOD%s_END" % nn, bible2,
                          "模块%s 被腰斩（截断未落在模块边界）" % nn)
        self.assertEqual(sk2, sk, "经验块不该被动")

    def test_cover_generation_chain_mounted(self):
        """封面图：模块可导入、/cover 路由挂载、封面卡与文案三端在位。"""
        from app.core import covergen
        self.assertTrue(callable(getattr(covergen, "start", None)))
        with io.open(_MAIN_PY, encoding="utf-8") as fh:
            main_src = fh.read()
        self.assertIn(r"^/api/tasks/([^/]+)/cover$", main_src, "路由未挂载")
        self.assertIn("from core import covergen", main_src)
        self.assertIn("covergen.start(", main_src)
        with io.open(_APP_JS, encoding="utf-8") as fh:
            app_src = fh.read()
        self.assertIn("封面卡（covergen", app_src)
        self.assertIn("生成竖版封面插画，产出 cover.png", app_src)
        hits = _pairs_of(_en_block(), "生成竖版封面插画，产出 cover.png")
        self.assertEqual(len(hits), 1, "封面文案 EN 键应恰好一条")

    def test_distilled_lesson_scopes_to_article_only(self):
        """D 蒸馏（wenzi-xhs 选题/标题/排期复盘）：scope=article 路由边界——
        只进文章类注入，不漏进小说类（本班选 article 而非 "*" 的理由）。"""
        from app.core import skills
        self.assertIn("文笔风格", skills.LESSON_CATEGORIES, "闭集外分类")
        lid = skills.upsert_lesson(
            "article",
            "测试：小红书选题标题排期复盘要领（蒸馏通道回归样本）",
            "选题从账号定位出发；标题兼顾钩子与关键词双通道。",
            source="borrow-log 2026-10-05 蒸馏清账",
            category="文笔风格")
        self.assertIsNotNone(lid)
        self.assertEqual(lid["scope"], "article")
        self.assertTrue(lid.get("enabled", True))
        in_article = [x["id"] for x in skills.list_lessons("article")]
        self.assertIn(lid["id"], in_article)
        in_novel = [x["id"] for x in skills.list_lessons("novel")]
        self.assertNotIn(lid["id"], in_novel, "article 作用域不得漏进小说注入")


class LessonMergeChannelTests(BaseTest):
    """经验库管理通道（2026-10-07 全类型轮第 3/4 步，04 时巡检班提案 1）：

    自动去重只拦沉淀时的近似题（bigram 包含度 ≥0.8），语义近措辞散的存量
    聚簇（章末钩子 4 条/开篇钩子 2 条/爽点 2 条）此前没有并类通道——D 项
    体检「存量合并须绕开既有接口」转成本通道（skills.merge_lessons）。
    锁三件事：历史不丢（merged_titles/revisions 留痕）、召回证据不缩水
    （hits/won/seen 并入主条）、错误路径不动数据。
    """

    def _mk(self, title, content):
        from app.core import skills
        it = skills.upsert_lesson("serial_novel", title, content)
        self.assertIsNotNone(it, title)
        return it

    def test_merge_keeps_history_and_drops_duplicates(self):
        """正常路径：条数收缩、正文以主条为准、被并条标题与正文双留痕。"""
        from app.core import skills
        keep = self._mk("每章结尾必须留悬念", "结尾落在未解决问题或反转上。")
        dup1 = self._mk("章尾钩子必须挖坑断章", "章尾挖坑或断章，保证读者有翻页冲动。")
        dup2 = self._mk("结尾平淡要重写", "平缓过渡章尾重写，吸引力达标再提交。")
        self.assertEqual(len(skills.list_lessons()), 3, "夹具不得被自动去重误并")
        self.assertIsNone(skills.merge_lessons(keep["id"], [dup1["id"], dup2["id"]]))
        items = skills.list_lessons()
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it["id"], keep["id"])
        self.assertEqual(it["content"], keep["content"], "正文以主条为准（不批量重写）")
        for dup in (dup1, dup2):
            self.assertIn(dup["title"], it["merged_titles"], "被并标题须留痕")
        by_src = {r.get("merged_from"): r for r in it.get("revisions") or []}
        self.assertEqual(set(by_src), {dup1["id"], dup2["id"]}, "被并正文须进 revisions")
        self.assertEqual(by_src[dup1["id"]]["content"], dup1["content"])

    def test_merge_carries_evidence_so_recall_not_damaged(self):
        """验收「召回不受损」：被并条的 hits/won 证据并入主条，排序不缩水。"""
        from app.core import skills
        weak = self._mk("开篇钩子规范", "每章第一屏抛出未解决冲突或新悬念。")
        strong = self._mk("章首钩子不足", "前100字内必须出现新信息、冲突或悬念。")
        plain = self._mk("无关话题的其他教训", "完全不同的话题内容样本。")
        with skills._LOCK:
            data = skills._load()
            for it in data["lessons"]:
                if it["id"] == strong["id"]:
                    it["hits"] = 2
                    it["won"] = 1
            skills._save(data)
        self.assertIsNone(skills.merge_lessons(weak["id"], [strong["id"]]))
        it = next(x for x in skills.list_lessons() if x["id"] == weak["id"])
        self.assertEqual(int(it.get("hits") or 0), 2, "被并条注入证据须并入主条")
        self.assertEqual(int(it.get("won") or 0), 1, "被并条结局背书须并入主条")
        ids = [x["id"] for x in skills.list_lessons()]
        self.assertEqual(ids[0], weak["id"], "并入证据后主条应排到无证据条目之前")

    def test_merge_error_paths_leave_data_untouched(self):
        """边界：主条缺失/自并/被并缺失/空清单各报错，且数据原样。"""
        from app.core import skills
        a = self._mk("标题样本一", "内容一。")
        b = self._mk("标题样本二", "内容二。")
        self.assertEqual(skills.merge_lessons("sk-nope", [b["id"]]), "主条不存在")
        self.assertEqual(skills.merge_lessons(a["id"], [a["id"]]),
                         "主条不能同时是被并条目")
        self.assertEqual(skills.merge_lessons(a["id"], ["sk-nope"]),
                         "被并条目不存在 sk-nope")
        self.assertEqual(skills.merge_lessons(a["id"], []), "缺少被并条目")
        self.assertEqual(skills.merge_lessons(a["id"], None), "缺少被并条目")
        self.assertEqual(len(skills.list_lessons()), 2, "错误路径不得动数据")

    def test_recategorize_via_upsert_explicit_category(self):
        """改类走既有 upsert_lesson（显式 category 覆盖旧类）——存量 1 条
        「做法：小红书…」文笔风格→流程规范的数据手术锚点；同文重写零 revision。"""
        from app.core import skills
        it = skills.upsert_lesson("article", "做法：小红书选题复盘要领",
                                  "选题-标题-数据台账复盘。", category="文笔风格")
        self.assertEqual(it["category"], "文笔风格")
        it2 = skills.upsert_lesson("article", "做法：小红书选题复盘要领",
                                   "选题-标题-数据台账复盘。", category="流程规范")
        self.assertEqual(it2["id"], it["id"])
        self.assertEqual(it2["category"], "流程规范")
        self.assertEqual(it2["content"], it["content"])
        self.assertEqual(it2.get("revisions") or [], [],
                         "同文重写不得产生 revision 噪音")


if __name__ == "__main__":
    import unittest
    unittest.main()
