# -*- coding: utf-8 -*-
"""借镜轮回归（2026-10-08 第 3/4 步落地件守卫）：经验库蒸馏入库通道。

第 2 步巡检唯一落地提案=非代码蒸馏入库：「落地提案先对账既有守卫」
（skills.upsert_lesson，category=流程规范，scope=code，source=borrow-log 2026-10-08）。
本文件守卫该入口在入库场景的行为契约——正常入库、重复蒸馏幂等（防重是
「69→70 零重复」验收的机制面）、近似题合并、空输入拒绝；末条只读锚定
真实 data/skills.json 恰好一条（全新克隆无该文件则跳过，不挡 discover）。

跑法：python -m unittest discover -s tests -p "test_borrow_round_regression.py" -v
"""
from __future__ import annotations

from base import BaseTest

# 与 2026-10-08 报告「落地提案 1」逐字同源：改这里=改入库口径，两处必须同步。
DISTILL = {
    "scope": "code",
    "title": "落地提案先对账既有守卫",
    "content": ("改代码/提提案前先 grep 既有守卫测试与豁免集"
                "（本班三候选三撞实证——重复建设最好的解药是先查守卫）"),
    "source": "borrow-log 2026-10-08",
    "category": "流程规范",
}


class BorrowRoundRegressionTests(BaseTest):

    def _distill(self, skills, **over):
        payload = dict(DISTILL)
        payload.update(over)
        return skills.upsert_lesson(
            payload["scope"], payload["title"], payload["content"],
            source=payload["source"], category=payload["category"])

    def test_distill_normal_upsert(self):
        from app.core import skills
        les = self._distill(skills)
        self.assertIsNotNone(les, "合法蒸馏不得被拒绝")
        hit = next(x for x in skills.list_lessons("code") if x["id"] == les["id"])
        self.assertEqual(hit["title"], DISTILL["title"])
        self.assertEqual(hit["category"], "流程规范")
        self.assertEqual(hit["scope"], "code")
        self.assertEqual(hit["source"], "borrow-log 2026-10-08")
        self.assertEqual(hit["kind"], "lesson")
        self.assertTrue(hit["enabled"])
        self.assertEqual(hit["seen"], 1, "首次入库不得计入重放")

    def test_distill_replay_is_idempotent(self):
        from app.core import skills
        first = self._distill(skills)
        again = self._distill(skills)
        self.assertEqual(first["id"], again["id"], "同题重放应命中同一条")
        titles = [x["title"] for x in skills.list_lessons("code")
                  if x["title"] == DISTILL["title"]]
        self.assertEqual(len(titles), 1, "重复蒸馏不得分裂成两条（零重复验收的防重面）")
        hit = next(x for x in skills.list_lessons("code") if x["id"] == first["id"])
        self.assertEqual(hit["seen"], 2, "重放应走合并（seen+1）而非新增")
        self.assertNotIn("merged_titles", hit, "同题精确重放不应记变体标题")

    def test_distill_variant_title_merges(self):
        # 近似题（原题加后缀，bigram 包含度 1.0）必须并入同一条而非新增
        from app.core import skills
        first = self._distill(skills)
        variant = DISTILL["title"] + "再查一遍"
        merged = self._distill(skills, title=variant)
        self.assertEqual(first["id"], merged["id"], "近似题应并入原条")
        lessons = [x for x in skills.list_lessons("code")]
        self.assertEqual(len(lessons), 1, "近似题蒸馏后仍应只有一条")
        self.assertIn(variant, merged["merged_titles"], "被吸收的变体题应留痕可检索")

    def test_distill_boundary_empty_rejected(self):
        from app.core import skills
        self.assertIsNone(self._distill(skills, title="   "), "空题应拒绝")
        self.assertIsNone(self._distill(skills, content=""), "空正文应拒绝")
        self.assertEqual(skills.list_lessons("code"), [],
                         "被拒输入不得留下半条记录")

    def test_real_store_has_exactly_one_distilled_lesson(self):
        # 只读锚定真实数据侧验收（69→70、title 精确对账零重复）。
        # data/ 系 gitignored：全新克隆无此文件 → 跳过，不挡 discover。
        import json
        from base import ROOT
        real = ROOT / "data" / "skills.json"
        if not real.is_file():
            self.skipTest("本机无真实 data/skills.json（全新克隆），跳过只读对账")
        data = json.loads(real.read_text(encoding="utf-8"))
        hits = [x for x in (data.get("lessons") or [])
                if x.get("title") == DISTILL["title"] and x.get("scope") == "code"]
        self.assertEqual(len(hits), 1, "蒸馏条在真实库必须恰好一条（多了=重复入库）")
        self.assertEqual(hits[0].get("category"), "流程规范")
        self.assertEqual(hits[0].get("source"), "borrow-log 2026-10-08")
        self.assertEqual(hits[0].get("seen"), 1, "真实库应为净单次入库态")
