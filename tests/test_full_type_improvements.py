# -*- coding: utf-8 -*-
"""2026-10-08 全类型轮落地件行为测试（两件）：
1) rank_scan/defect_retro 推荐面补齐 + 类型建议文案按引擎分流——此前
   direct 档输入「扫榜看热门题材」「复盘这些bug」不弹类型切换建议，两型的
   专用数据链路（四平台抓榜/CSV 三视角复盘）被整个旁路，快档只能凭空作答。
2) goal_hint 引导词与推荐规则对齐（presentation「演示场合」/serial_novel
   「女频/男频/签约平台」/article「头条/知乎」/research 收敛出裸「调研」）
   ——照占位提示输入此前不弹类型建议（同 video_script 补词第 3 例缝隙）。

只锁本轮行为，不重复邻接守卫的职责：
  - 规则覆盖/首中顺序总账/防截胡回归 → test_recommend_rules.py；
  - app.js t() 字面量键 ↔ i18n.js 全量对账 → test_borrow_iteration.py；
  - 18 型注册矩阵与默认参数 → test_full_type_round.py。

跑法：python -m unittest discover -s tests -p "test_full_type_improvements.py" -v
"""
from __future__ import annotations

import os
import re

from base import BaseTest
from test_recommend_rules import _rules

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP = os.path.join(_ROOT, "app", "ui", "app.js")

_COPY_DIRECT = ("这个需求更适合「{0}」流程，能使用对应的专用数据链路与产出模板。"
                "是否切换后执行？")
_COPY_GATES = ("这个需求更适合「{0}」流程，能使用对应的规划与质量门禁。"
               "是否切换后执行？")

# 建议文案按引擎分流的接线形态（app.js createTask 类型建议块）：
# direct 引擎目标走数据链路口径，review/code 目标维持门禁口径（旧文案不丢）。
_TERNARY = re.compile(
    r'flow\.engine === "direct"\s*\?\s*t\("%s"\)\s*:\s*t\("%s"\)'
    % (re.escape(_COPY_DIRECT), re.escape(_COPY_GATES)))


class FullTypeImprovementTests(BaseTest):

    def test_new_types_are_direct_engine(self):
        """正常路径：两型在注册表是 direct 引擎快档流程——「专用数据链路」
        文案只会弹给真的无门禁、有专用链路的类型（engine 漂移则口径失真）。"""
        from app.core.flows import BUILTIN_FLOWS
        engines = {f["id"]: f.get("engine") for f in BUILTIN_FLOWS}
        self.assertEqual(engines.get("rank_scan"), "direct")
        self.assertEqual(engines.get("defect_retro"), "direct")

    def test_suggestion_copy_splits_by_engine(self):
        """正常+回归：建议文案按 flow.engine 分流接线在位——direct 走数据
        链路口径，其余引擎维持门禁口径；缺任一分支即口径失真或旧文案漂移。"""
        with open(_APP, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIsNotNone(
            _TERNARY.search(src),
            "类型建议未按 flow.engine 分流（direct 数据链路口径缺失，"
            "或 review/code 门禁旧文案被改丢）")

    def test_direct_engine_rules_exactly_new_two(self):
        """边界：direct 引擎上有推荐规则的类型恰好是本轮两型——「专用数据
        链路」文案的可达面就此锁定（后续给其他 direct 引擎流程加规则须
        consciously 更新本断言）。direct 自身无规则（不自荐）由
        test_recommend_rules 锁定，此处不重复。"""
        from app.core.flows import BUILTIN_FLOWS
        engines = {f["id"]: f.get("engine") for f in BUILTIN_FLOWS}
        direct_with_rules = sorted(
            fid for fid, _ in _rules() if engines.get(fid) == "direct")
        self.assertEqual(direct_with_rules, ["defect_retro", "rank_scan"])

    def test_rank_scan_broad_word_known_tradeoff(self):
        """边界（已知取舍锚定）：「排行榜」宽词在末位兜底，罕见目标
        「把数据做成排行榜图表」会弹一次建议——可拒绝、会话内仅一次
        （S.typeSuggestionDone）。日后收窄词形须 consciously 翻转本断言。"""
        rules = _rules()

        def hit(goal):
            for fid, pat in rules:
                if pat.search(goal):
                    return fid
            return ""

        self.assertEqual(hit("把数据做成排行榜图表"), "rank_scan")

    # ---- 落地件 2：goal_hint 引导词与推荐规则对齐（2026-10-08 第二轮）----

    # 对齐契约：类型 → 规则新增词必须出自该型 flows.py 的 goal_hint 引导文案
    # （照占位提示输入就该命中；hint 改词须 consciously 同步规则与本表）。
    _HINT_WORDS = {
        "presentation": ["演示场合"],
        "serial_novel": ["女频", "签约平台"],
        "article": ["头条", "知乎"],
        "research": ["调研"],
    }

    def test_goal_hint_alignment_contract(self):
        """正常路径：四型的对齐词都能在各自 flows.py goal_hint 文案里找到
        ——hint↔规则同源对账，防规则词脱离引导文案自行漂移。"""
        from app.core.flows import BUILTIN_FLOWS

        hints = {f["id"]: f.get("goal_hint", "") for f in BUILTIN_FLOWS}
        for fid, words in self._HINT_WORDS.items():
            for w in words:
                self.assertIn(w, hints.get(fid, ""),
                              "%s 的规则词「%s」不在其 goal_hint 引导文案中"
                              % (fid, w))
            # 规则表侧同步存在（覆盖总账在 test_recommend_rules），且每个
            # 引导词本身能被该型规则命中
            rule = dict(_rules()).get(fid)
            self.assertIsNotNone(rule, "%s 缺推荐规则" % fid)
            for w in words:
                self.assertTrue(rule.search(w),
                                "%s 的规则不再命中引导词「%s」" % (fid, w))

    def test_research_rewrite_keeps_old_hits(self):
        """回归：research 规则改写 (调研报告|竞品调研|市场调研|深度研究) →
        (调研|深度研究) 必须是行为超集——四种旧词形一个都不能丢。"""
        rules = dict(_rules())

        def hit(goal):
            pat = rules.get("research")
            return bool(pat and pat.search(goal))

        for goal in ("写一份调研报告", "做个竞品调研", "市场调研：奶茶行业",
                     "深度研究 RAG 检索方案"):
            self.assertTrue(hit(goal), "旧词形丢失：%s" % goal)

    def test_aligned_words_first_hit_order(self):
        """边界：对齐词的首中顺序——research 在 article 之前（「知乎+调研」
        共现归 research，不被「知乎」截走）；serial_novel 在 novel 之前
        （「女频+短篇」共现归 serial_novel，不被「短篇」截走）。"""
        rules = _rules()

        def hit(goal):
            for fid, pat in rules:
                if pat.search(goal):
                    return fid
            return ""

        self.assertEqual(hit("在知乎上调研一下协作工具的优劣"), "research")
        self.assertEqual(hit("女频向的短篇故事"), "serial_novel")


if __name__ == "__main__":
    import unittest
    unittest.main()
