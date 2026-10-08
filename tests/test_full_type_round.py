# -*- coding: utf-8 -*-
"""全类型回归（2026-10-04 全类型竞品调研轮，第 3/4 步落地核验）：

  1. 正常：全部预置任务类型（BUILTIN_FLOWS 实数 18，2026-10-05 加演示文稿）
      注册可用——list/get 解析、
     三展示字段齐、引擎合法、review 引擎四默认参数在位；
  2. 回归：用户 14 场景 → 注册表 id 映射零缺失（任务类型矩阵口径锁定），
      注册表额外 doc/resume/bid_doc/presentation 四类型如实记录；
  3. 边界：本轮落地件（serial_novel 签约门禁 note / rank_scan A-E 证据分级
      note）与 i18n EN 键严格相等、键恰好一次、旧版文案残留为零；
  4. 本轮新增：翻译起草侧常量带「翻译腔自查」条（yomiyasu 借鉴，此前翻译腔
     只靠评审 rubric 事后抓）；经验库蒸馏走 upsert_lesson 入库路径——闭集
     分类落位、同题再沉淀合并不分裂（隔离数据目录，不碰真实经验库）。
  5. 2026-10-06 轮：连载起草「相关历史章节推荐」（_related_chapters_note，
     story_tracking 记录行与本章大纲要点的纯本地 bigram 重叠计分，零 LLM
     调用零存储写入）——命中/排序/封顶、零匹配静默与自章排除、起草前情
     组装的接线在位。
  6. 2026-10-09 轮：review 引擎经验库/知识库注入补全——13 个 review 引擎
     类型此前起草/评审零注入而 learn_async 全类型收割（闭环半开），按 serial
     同款接入；块生成行为（空库零噪音/命中成块/全类型覆盖）+ 起草与评审
     两侧接线守卫。

跑法：python -m unittest discover -s tests -p "test_full_type_round.py" -v
17×3 字段 EN 键全量守卫在 test_i18n_dups.test_builtin_flow_fields_have_en_keys，
本文件不重复该断言，只对选中两键做跨端一致性与残留核查。
"""
from __future__ import annotations

import io
import os
import re

from base import BaseTest
from test_i18n_dups import _en_block

_I18N = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "app", "ui", "i18n.js")

# 任务类型矩阵（2026-10-05 落地班实读 flows.py）：18 = 用户 14 场景 + 额外 4。
EXPECTED_IDS = frozenset((
    "direct", "code", "novel", "serial_novel", "article", "video_script",
    "doc", "translation", "rank_scan", "defect_retro", "research", "speech",
    "weekly_report", "email", "tech_proposal", "resume", "bid_doc",
    "presentation",
))

# 用户 14 场景（列举口径）→ 注册表 id；改名/删类型时此处当 consciously 更新
USER_SCENARIOS = (
    ("直接执行", "direct"), ("代码", "code"), ("小说", "novel"),
    ("连载", "serial_novel"), ("自媒体文章", "article"), ("调研报告", "research"),
    ("短视频脚本", "video_script"), ("技术方案", "tech_proposal"),
    ("翻译", "translation"), ("演讲稿", "speech"), ("工作汇报", "weekly_report"),
    ("商务邮件", "email"), ("扫榜选材", "rank_scan"), ("禅道工单", "defect_retro"),
)
EXTRA_REGISTRY_TYPES = ("doc", "resume", "bid_doc", "presentation")

# 本轮改版前的旧文案（英文界面静默旧话术的残留哨兵）
_STALE_COPY = (
    "大纲 → 逐章起草 → 每章多维评审修订 → 全局一致性评审 → 合并（可断点续跑）",
    "抓取七猫/番茄/起点/纵横四平台排行榜公开数据 → AI 提炼跨平台热门题材/人设/"
    "差异化切入点（快档直出报告）",
)


def _pairs_of(block, key):
    """EN 块里 key 的全部（值）命中——条数即该键出现次数。"""
    pat = r'(?:^|[,{]\s*)"' + re.escape(key) + r'"\s*:\s*"((?:[^"\\]|\\.)*)"'
    return re.findall(pat, block)


class FullTypeRoundTests(BaseTest):

    def test_all_builtin_types_register_and_resolve(self):
        """正常路径：17 预置类型逐一注册、解析、默认参数齐备。"""
        from app.core import flows
        ids = [f["id"] for f in flows.BUILTIN_FLOWS]
        self.assertEqual(set(ids), set(EXPECTED_IDS),
                         "预置类型矩阵变化——consciously 更新 EXPECTED_IDS 与矩阵记录")
        self.assertEqual(len(ids), len(set(ids)), "注册表 id 重复")
        listed = {f["id"] for f in flows.list_flows()}
        for fid in sorted(EXPECTED_IDS):
            flow = flows.get_flow(fid)
            self.assertIsNotNone(flow, fid)
            self.assertIn(fid, listed, fid)
            self.assertTrue(flow.get("builtin"), fid)
            self.assertIn(flow.get("engine"), flows.ENGINES, fid)
            for field in ("name", "goal_hint", "note"):
                self.assertTrue((flow.get(field) or "").strip(),
                                "%s.%s 空文案" % (fid, field))
            if flow["engine"] == "review":
                self.assertTrue(str(flow.get("manuscript") or "").strip(),
                                "%s 缺 manuscript 默认" % fid)
                self.assertTrue(flow.get("rubric"), "%s 缺 rubric 默认" % fid)
                self.assertTrue(flow.get("rounds"), "%s 缺 rounds 默认" % fid)

    def test_user_scenario_matrix_zero_missing(self):
        """回归：用户 14 场景映射零缺失 + 注册表额外类型在位。"""
        from app.core import flows
        by_id = {f["id"] for f in flows.BUILTIN_FLOWS}
        missing = [(label, fid) for label, fid in USER_SCENARIOS
                   if fid not in by_id]
        self.assertEqual(missing, [],
                         "用户场景映射缺失（界面有入口但注册表无类型）: %r" % missing)
        for fid in EXTRA_REGISTRY_TYPES:
            self.assertIn(fid, by_id,
                          "注册表额外类型漂移（矩阵记录需同步）: %s" % fid)

    def test_selected_copy_revision_matches_en_keys(self):
        """边界：落地件两键与 flows.py 文案严格相等、恰好一次、旧版残留零。"""
        from app.core.flows import BUILTIN_FLOWS
        with io.open(_I18N, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("const EN", src, "i18n.js 字典形态异常：解析失效")
        block = _en_block()
        by_id = {f["id"]: f for f in BUILTIN_FLOWS}
        for fid in ("serial_novel", "rank_scan"):
            text = by_id[fid]["note"]
            hits = _pairs_of(block, text)
            self.assertEqual(len(hits), 1,
                             "%s.note 应恰有一条 EN 译（0=缺键回落中文，>1=重复键）" % fid)
            self.assertTrue(hits[0].strip(), "%s.note EN 译为空" % fid)
        for stale in _STALE_COPY:
            self.assertEqual(_pairs_of(block, stale), [],
                             "旧版文案残留 EN 键: %r" % stale)

    def test_translation_appendix_carries_tone_selfcheck(self):
        """翻译腔自查前置到起草侧：常量含自查条，术语表约定不被挤掉。"""
        from app.core.pipeline import TRANSLATION_APPENDIX
        self.assertIn("翻译腔", TRANSLATION_APPENDIX,
                      "翻译起草侧缺翻译腔自查（缺陷复现：只靠评审事后抓）")
        self.assertIn("## 翻译要求", TRANSLATION_APPENDIX)
        self.assertIn("术语表", TRANSLATION_APPENDIX,
                      "术语表先行约定被挤掉")
        # 常量是唯一消费点（draft prompt 追加），形态保持「\n 开头的追加块」
        self.assertTrue(TRANSLATION_APPENDIX.startswith("\n"))

    def test_lesson_distillation_path_dedups_and_categorizes(self):
        """经验库蒸馏入库路径：upsert_lesson 闭集分类落位、同题合并不分裂。"""
        from app.core import skills
        title = ("全类型竞品雷达三问接入判据：重合度/可直读性/用户搜索意图，"
                 "三问全过才接、不过只跟踪")
        first = skills.upsert_lesson("*", title, "三问判据正文",
                                     source="borrow-log 2026-10-04",
                                     category="流程规范")
        self.assertIsNotNone(first)
        self.assertEqual(first["category"], "流程规范",
                         "闭集分类未按入参落位")
        before = len(skills.list_lessons("*"))
        again = skills.upsert_lesson("*", title, "三问判据正文（更新）",
                                     source="borrow-log 2026-10-04",
                                     category="流程规范")
        after = len(skills.list_lessons("*"))
        self.assertEqual(after, before, "同题再沉淀应合并而非新增")
        self.assertEqual(again["seen"], 2, "合并语义应 seen+1")


def _chapter_rec(no, foreshadowing=None, characters=None, facts=None):
    """story_tracking.load 形态的最小章记录（只带消费字段，story_tracking
    自身的读写回归在 test_story_tracking*，此处不重复）。"""
    return {"chapter": no, "outline": "章纲占位", "facts": list(facts or []),
            "characters": list(characters or []),
            "foreshadowing": [{"id": "f%d" % k, "text": t, "status": "active",
                               "planted_chapter": no, "payoff_chapter": None}
                              for k, t in enumerate(foreshadowing or [])]}


_CH = {"beats": "沈青梧查玉佩裂纹的来历", "hook": "当铺掌柜半夜来敲门",
       "highlight": "玉佩当众碎成两半"}


class RelatedChaptersNoteTests(BaseTest):
    """连载起草「相关历史章节推荐」（2026-10-06 轮提案 1）：命中/排序/封顶、
    零匹配静默、自章排除、起草前情组装接线。"""

    def test_related_chapters_note_hits_orders_and_caps(self):
        """正常路径：重叠章检出并按命中行数排序，非重叠章零噪音，max_n 封顶。"""
        from app.core.pipeline import _related_chapters_note
        state = {"version": 1, "chapters": [
            _chapter_rec(2, foreshadowing=["玉佩在当铺失而复得，沈青梧起疑"]),
            _chapter_rec(5, characters=["沈青梧"]),
            _chapter_rec(8, facts=["北境商队压价收粮"]),
            _chapter_rec(12, foreshadowing=["玉佩裂纹暗合族徽",
                                            "玉佩裂纹里藏着半张地图"]),
        ]}
        note = _related_chapters_note(state, _CH, current=20)
        self.assertIn("相关历史章节", note)
        self.assertIn("第 12 章", note)
        self.assertIn("玉佩裂纹暗合族徽", note)
        self.assertIn("第 2 章", note)
        self.assertIn("第 5 章", note)
        self.assertNotIn("第 8 章", note, "零重叠章不得出现")
        # 排序：命中行数多者在前（12 有两行），同分按章号升序（2 先于 5）
        self.assertLess(note.index("第 12 章"), note.index("第 2 章"))
        self.assertLess(note.index("第 2 章"), note.index("第 5 章"))
        # 封顶：max_n=2 只留前两名（12、2），第 5 章落榜
        capped = _related_chapters_note(state, _CH, current=20, max_n=2)
        self.assertIn("第 12 章", capped)
        self.assertIn("第 2 章", capped)
        self.assertNotIn("第 5 章", capped)

    def test_related_chapters_note_silent_and_self_excluded(self):
        """边界：无状态/空章纲/零重叠返回空串；current 排除当章及之后记录。"""
        from app.core.pipeline import _related_chapters_note
        self.assertEqual(_related_chapters_note(None, _CH), "")
        self.assertEqual(_related_chapters_note({"version": 1, "chapters": []}, _CH), "")
        self.assertEqual(_related_chapters_note({"version": 1, "chapters": [
            _chapter_rec(3, foreshadowing=["玉佩下落不明"])], }, {}), "")
        fresh = {"version": 1, "chapters": [_chapter_rec(4, facts=["玉佩裂纹来历"])]}
        self.assertEqual(_related_chapters_note(fresh, _CH, current=4), "",
                         "重跑已提交章不得跟自己的记录自证")
        self.assertIn("第 4 章", _related_chapters_note(fresh, _CH),
                      "不传 current 时历史记录照常可命中")
        self.assertEqual(_related_chapters_note(fresh, _CH, current=4, max_n=0), "")

    def test_serial_draft_wires_related_note_into_prev(self):
        """回归：连载起草前情组装消费该推荐（prev 尾部注入位在位，防接线被改丢）。"""
        import inspect
        from app.core import pipeline
        src = inspect.getsource(pipeline)
        self.assertIn("_related_chapters_note(tracking_state, ch, current=i)", src)
        self.assertIn("相关历史章节", src)


class ReviewEngineSkillInjectionTests(BaseTest):
    """review 引擎经验库/知识库注入补全（2026-10-09 轮提案 1）：learn_async
    从全部 run 收割教训，注入此前只在连载链（大纲/起草/评审三处），13 个
    review 引擎类型的起草与评审零注入——「全类型收割、单链注入」闭环半开。
    覆盖：块生成行为（空库零噪音/命中成块/13 类型全覆盖）+ 起草与评审两侧
    接线守卫（serial 同款，防接线被改丢）。"""

    def test_review_engine_blocks_hit_and_zero_noise(self):
        """行为：空库零返回（零噪音基线）；沉淀在库时命中成块。"""
        from app.core import knowledge, skills
        # 空用户库（BaseTest 隔离目录）：skills 仍注入内置包（产品自带规范，
        # 3A 口径设计如此），但零用户教训时无「本项目经验记录」节、零 id
        # 登记（outcome 归因不受污染）；knowledge 零 approved 条目零返回
        task0 = {"type": "novel", "goal": "写一个当铺题材的悬疑故事"}
        sk0, _ = skills.block_for(task0, stable_order=True, run_id="r0")
        self.assertNotIn("本项目经验记录", sk0)
        # 归因污染面：零教训命中时 run 不进 outcome 登记表（used 混含内置包
        # id 属既有语义，教训 id 走 lesson_ids 才计 hits/归因）
        self.assertNotIn("r0", skills._INJECTED, "零教训不得登记 outcome 归因")
        self.assertEqual(knowledge.block_for(task0), "")
        # 造沉淀：scope=* 教训 + novel 域 approved 知识（upsert_lesson /
        # upsert_entry 既有入库通道，与生产同路径）
        lesson = skills.upsert_lesson("*", "悬疑开篇三句内埋钩子",
                                      "第一段末尾留未解释异象，别急着解释",
                                      source="test")
        self.assertIsNotNone(lesson)
        entry = knowledge.upsert_entry("novel", "当铺赎当期事实",
                                       "1990 年代民间当铺赎当期约十八个月",
                                       source="test", status="approved")
        self.assertIsNotNone(entry)
        task = {"type": "novel", "goal": "当铺掌柜与玉佩裂纹的悬疑故事"}
        sk, ids = skills.block_for(task, stable_order=True, run_id="r1")
        self.assertIn("## 经验库", sk, "skills 块应带注入 header")
        self.assertIn("悬疑开篇三句内埋钩子", sk)
        self.assertTrue(ids, "命中教训应登记 id（outcome 归因依赖）")
        kb = knowledge.block_for(task)
        self.assertIn("## 知识库", kb, "knowledge 块应带注入 header")
        self.assertIn("当铺赎当期事实", kb)
        # knowledge 按类型 scope 过滤：novel 条目不进 email 任务（零跨域噪音）
        self.assertEqual(knowledge.block_for(
            {"type": "email", "goal": "当铺掌柜与玉佩裂纹的悬疑故事"}), "")

    def test_all_review_engine_types_get_blocks(self):
        """全类型覆盖：engine=review 的全部预置类型逐一命中注入块，无遗漏。"""
        from app.core import flows, knowledge, skills
        skills.upsert_lesson("*", "标题即钩子", "首段三句内埋悬念", source="test")
        knowledge.upsert_entry("*", "平台通用规范", "标题长度以平台上限为准",
                               source="test", status="approved")
        review_ids = [f["id"] for f in flows.BUILTIN_FLOWS
                      if f["engine"] == "review"]
        self.assertGreaterEqual(len(review_ids), 13,
                                "review 引擎类型覆盖面缩水（注册表漂移？）")
        for fid in review_ids:
            task = {"type": fid, "goal": "面向大众的成稿任务"}
            sk, ids = skills.block_for(task, stable_order=True, run_id="r-%s" % fid)
            self.assertIn("标题即钩子", sk, "%s 未命中经验块" % fid)
            self.assertTrue(ids, fid)
            self.assertIn("平台通用规范", knowledge.block_for(task),
                          "%s 未命中知识块" % fid)

    def test_review_engine_wires_injection_into_draft_and_critique(self):
        """接线守卫：起草侧在 _draft_prompt_for 外组装 ctx_block（bestof 多稿件
        共用一份）、空块守卫追加；评审侧 serial 同款锚点注入。"""
        import inspect
        from app.core import pipeline
        src = inspect.getsource(pipeline)
        # 起草侧：块组装在 _draft_prompt_for 定义之前 + 空块守卫消费
        self.assertIn(
            'ctx_block = "\\n\\n".join(p for p in (sk_lessons, kb_block) if p)', src)
        self.assertIn('p += "\\n\\n" + ctx_block', src)
        self.assertIn("if ctx_block:", src)
        # 评审侧：serial 逐章评审同款（「## 待评审稿件」锚点前插，sk+kb 两处）
        self.assertEqual(src.count("crit_prompt = crit_prompt.replace("), 2,
                         "评审侧经验/知识注入锚点数漂移")
        self.assertIn(
            '"## 待评审稿件", "%s\\n\\n## 待评审稿件" % sk, 1)', src)
        self.assertIn(
            '"## 待评审稿件", "%s\\n\\n## 待评审稿件" % kb, 1)', src)


if __name__ == "__main__":
    import unittest
    unittest.main()
