# -*- coding: utf-8 -*-
"""连载 branches（多线剧情推演）UI 入口回归——本轮 borrow 落地件。

后端 branches 全链早已支持（store.create_task 钳位 1-3、branching.plan_branches
写前推演、continue_task 随链沿用），但前端任务表单与流程编辑器都没有输入口，
且任务级 payload.serial 整体替换流程默认（字段级不合并）——UI 建的任务永远
读不到流程里配好的 branches，多线推演只能靠手拼 API（22 时班 C/G 专项实录
缺口）。本文件锁定补齐后的契约：index.html 输入框、app.js 两处 payload 接线
（与 variants 同口径 1-3 钳位）、i18n EN 键，以及 create_task 的 branches 钳位。

2026-10-06 第 3/4 步落地班追加：18 型注册表跨类型回归（见
AllTypesRegistryContractTests——本轮选定零代码件，把 C/G 专项核验结论机械化）。
"""
from __future__ import annotations

import io
import os
import re

from base import BaseTest

_UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "app", "ui")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 过时类型数/市场模式文案（G 专项口径：以源码实数 18 为准，这些说法都曾翻车）。
_STALE_COPY = ("13 种", "14 种", "15 种", "17 种", "11 个预置", "单源模式")
_STALE_SCAN_FILES = (
    os.path.join(_UI, "index.html"),
    os.path.join(_UI, "app.js"),
    os.path.join(_UI, "i18n.js"),
    os.path.join(_ROOT, "app", "core", "flows.py"),
    os.path.join(_ROOT, "app", "core", "pipeline.py"),
    os.path.join(_ROOT, "README.md"),
)

_BRANCH_PH = "1 = 关闭（2-3 写前剧情分支推演择优）"


def _read(name):
    with io.open(os.path.join(_UI, name), encoding="utf-8") as f:
        return f.read()


class BranchesUiContractTests(BaseTest):
    """前端接线契约：输入框在位、payload 钳位口径与后端一致（1-3）。"""

    def test_task_form_has_branches_input(self):
        """任务表单连载区块有 f-branches 输入框（1-3 + i18n 属性齐全）。"""
        html = _read("index.html")
        m = re.search(r'<input id="f-branches"[^>]*>', html)
        self.assertIsNotNone(m, "f-branches 输入框缺失")
        tag = m.group(0)
        self.assertIn('type="number"', tag)
        self.assertIn('min="1"', tag)
        self.assertIn('max="3"', tag)
        self.assertIn('data-i18n="多线剧情推演数"', html)
        self.assertIn('data-i18n-ph="%s"' % _BRANCH_PH, html)

    def test_task_payload_wires_branches_clamp(self):
        """任务表单与流程编辑器 payload 都带 branches（1-3 钳位同 variants）。"""
        src = _read("app.js")
        self.assertIn(
            'branches: Math.max(1, Math.min(3, '
            'parseInt($("f-branches").value, 10) || 1)),', src)
        self.assertIn('id="fl-branches"', src)
        self.assertIn(
            'branches: Math.max(1, Math.min(3, '
            'parseInt($("fl-branches").value, 10) || 1)),', src)
        # 切换流程清空 + 任务详情回填两路径都认得新字段
        self.assertIn('"f-branches"', src)
        self.assertIn(
            '$("f-branches").value = tk.serial && tk.serial.branches', src)

    def test_i18n_en_keys_for_branches(self):
        """两个新文案键都有非空 EN 译（英文界面不再静默回落中文）。"""
        en = _read("i18n.js")
        for key in ("多线剧情推演数", _BRANCH_PH):
            self.assertRegex(en, r'"%s"\s*:\s*"[^"]+"' % re.escape(key))


class CreateTaskBranchesTests(BaseTest):
    """后端旧行为锁定：create_task 的 branches 钳位（1-3，1=不启用不落键）。"""

    def _create(self, branches):
        from app.core import store
        serial = {"chapters": 3, "words_per_chapter": 1000}
        if branches is not None:
            serial["branches"] = branches
        return store.create_task({
            "type": "serial_novel",
            "goal": "branches 钳位回归",
            "workdir": str(self.workdir),
            "serial": serial,
        })

    def test_branches_pass_through(self):
        """branches=2 原样落任务（UI 新通路打开后端既有能力）。"""
        got = self._create(2)
        self.assertEqual(got["serial"].get("branches"), 2)

    def test_branches_clamp_out_of_range(self):
        """branches=9 越界钳到 3（与 variants 同口径）。"""
        got = self._create(9)
        self.assertEqual(got["serial"].get("branches"), 3)

    def test_branches_off_omits_key(self):
        """branches=1/缺省（不启用）不落键——与 store.create_task 口径一致。"""
        self.assertNotIn("branches", self._create(1)["serial"])
        self.assertNotIn("branches", self._create(None)["serial"])


class AllTypesRegistryContractTests(BaseTest):
    """18 型注册表跨类型回归（2026-10-06 第 3/4 步落地班锚定件）。

    本轮第 2/4 步巡检选定零代码件（代码级积压清账、队列活项均攒批/拍板/远期），
    落地=把 C/G 专项核验结论机械化：14 指令项一一映射注册表、review 型共享
    参数不变量（rubric 4-5 维/threshold≥7.0/rounds≥2/文案齐全）、非 review 型
    不带评审参数、过时文案六模式零残留——日后加型改参或文案回潮在此先红，
    倒逼同步 knowledge.md/current-round.md 台账。
    """

    def test_instruction_items_all_registered(self):
        """14 指令项（含禅道工单→defect_retro）一一映射注册表，实数 18。"""
        from app.core import flows
        ids = {f["id"] for f in flows.BUILTIN_FLOWS}
        self.assertEqual(len(flows.BUILTIN_FLOWS), 18)
        self.assertEqual(ids, {
            "direct", "code", "novel", "serial_novel", "article",
            "video_script", "doc", "translation", "rank_scan", "defect_retro",
            "research", "speech", "presentation", "weekly_report", "email",
            "tech_proposal", "resume", "bid_doc",
        })

    def test_review_param_invariants_across_review_types(self):
        """14 个 review 型共享契约：rubric 4-5 维、threshold≥7.0、rounds≥2。

        边界：bid_doc threshold=7.5（标书合规面刻意从严）同过 ≥7.0 口径；
        serial_novel 独有 serial{chapters:8, words_per_chapter:2500} 默认在位。
        """
        from app.core import flows
        review = [f for f in flows.BUILTIN_FLOWS if f["engine"] == "review"]
        self.assertEqual(len(review), 14)
        for f in review:
            self.assertTrue(4 <= len(f["rubric"]) <= 5, f["id"])
            self.assertGreaterEqual(f["threshold"], 7.0, f["id"])
            self.assertGreaterEqual(f["rounds"], 2, f["id"])
            for field in ("name", "goal_hint", "note", "manuscript"):
                self.assertTrue(f.get(field), "%s 缺 %s" % (f["id"], field))
        serial = next(f for f in review if f["id"] == "serial_novel")
        self.assertEqual(serial["serial"],
                         {"chapters": 8, "words_per_chapter": 2500})

    def test_non_review_types_carry_no_review_params(self):
        """direct/code/rank_scan/defect_retro 四型快档直出，不带评审参数。"""
        from app.core import flows
        non_review = set()
        for f in flows.BUILTIN_FLOWS:
            if f["engine"] != "review":
                non_review.add(f["id"])
                self.assertFalse(
                    {"rubric", "threshold", "rounds", "manuscript"} & set(f),
                    "%s 不应带评审参数" % f["id"])
        self.assertEqual(non_review,
                         {"direct", "code", "rank_scan", "defect_retro"})

    def test_no_stale_type_count_copy(self):
        """过时文案六模式在 UI 三件+核心两件+README 零残留（G 专项机械化）。"""
        for path in _STALE_SCAN_FILES:
            with io.open(path, encoding="utf-8") as f:
                text = f.read()
            for pattern in _STALE_COPY:
                self.assertNotIn(pattern, text,
                                 "%s 残留过时文案「%s」" % (path, pattern))


if __name__ == "__main__":
    import unittest
    unittest.main()
