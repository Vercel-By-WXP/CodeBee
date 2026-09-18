# -*- coding: utf-8 -*-
"""Best-of-N 赛马起草（非连载评审流）测试。

借鉴 freebuff editor-multi-prompt（多策略候选）+ best-of-n-selector（择优+败者精华回收）。
跑法：python -m unittest discover -s tests -p "test_bestof.py" -v
"""
from __future__ import annotations

from base import BaseTest


class BestofVariantNameTests(BaseTest):

    def test_variant_name(self):
        from app.core.pipeline import _variant_name
        self.assertEqual(_variant_name("manuscript.md", 0), "manuscript.v0.md")
        self.assertEqual(_variant_name("manuscript.md", 2), "manuscript.v2.md")
        self.assertEqual(_variant_name("report", 1), "report.v1")
        self.assertEqual(_variant_name("a.b.c", 1), "a.b.v1.c")


class BestofDraftTests(BaseTest):
    """_bestof_draft：候选并发、择优、败者精华回收、失败回落。"""

    def setUp(self):
        super().setUp()
        from app.core import pipeline
        self.pipeline = pipeline
        self._orig_run_step = pipeline._run_step
        self._orig_update = pipeline.store.update_run

    def tearDown(self):
        self.pipeline._run_step = self._orig_run_step
        self.pipeline.store.update_run = self._orig_update
        super().tearDown()

    def _seed_variants(self, texts):
        """预先把候选稿文件写进工作目录（mock 的 _run_step 不真正起草）。"""
        for k, t in texts.items():
            (self.workdir / ("manuscript.v%d.md" % k)).write_text(t, encoding="utf-8")

    def _run(self, best_of=2, sel_text="{}", texts=None, draft_fails=()):
        """跑一次 _bestof_draft；返回 (res, winner, roles, bestof_records)。"""
        p = self.pipeline
        texts = texts if texts is not None else {0: "甲稿", 1: "乙稿"}
        self._seed_variants(texts)
        roles = []
        bestofs = []

        def fake_run_step(run_id, role, agent, prompt, wd, **kw):
            roles.append(role)
            if role == "bestof-select":
                return {"ok": True, "text": sel_text}
            if role in draft_fails:
                return {"ok": False, "text": "", "error": "起草崩了"}
            return {"ok": True, "text": ""}

        p._run_step = fake_run_step
        p.store.update_run = lambda rid, **kw: bestofs.append(kw)
        res, winner = p._bestof_draft(
            "r-bestof", {"goal": "写个故事"}, {"id": "a1", "mode": "real"},
            lambda vf: "P:" + vf, "manuscript.md", str(self.workdir),
            str(self.workdir), None, None, "default", best_of,
            {"id": "critic1"}, lambda t: None)
        return res, winner, roles, bestofs

    def test_picks_and_harvests(self):
        sel = '{"pick": 1, "reason": "更贴合目标", "improvements": "甲稿的画面感"}'
        res, winner, roles, bestofs = self._run(sel_text=sel)
        self.assertTrue(res["ok"])
        self.assertEqual(winner["k"], 1)
        # 候选是并行线程，完成顺序不定；只有选择器必然收尾
        self.assertEqual(sorted(roles[:2]), ["draft-v0", "draft-v1"])
        self.assertEqual(roles[-1], "bestof-select")
        self.assertEqual(len(bestofs), 1)
        self.assertEqual(bestofs[0]["bestof"]["pick"], 1)
        self.assertEqual(bestofs[0]["bestof"]["improvements"], "甲稿的画面感")
        # 败者精华随胜者返回（修订轮注入用）
        self.assertEqual(winner["improvements"], "甲稿的画面感")
        self.assertEqual(winner["reason"], "更贴合目标")

    def test_selector_garbage_falls_back_to_first(self):
        res, winner, roles, bestofs = self._run(sel_text="这不是JSON")
        self.assertTrue(res["ok"])
        self.assertEqual(winner["k"], 0)
        self.assertEqual(bestofs[0]["bestof"]["pick"], 0)
        self.assertEqual(bestofs[0]["bestof"]["reason"], "")

    def test_failed_candidate_dropped(self):
        res, winner, roles, bestofs = self._run(
            sel_text='{"pick": 1, "reason": "r", "improvements": ""}',
            draft_fails=("draft-v1",))
        self.assertTrue(res["ok"])
        self.assertEqual(winner["k"], 0)          # 1 号失败被弃，只能选 0 号
        self.assertEqual(sorted(roles[:2]), ["draft-v0", "draft-v1"])
        self.assertEqual(roles[-1], "bestof-select")

    def test_all_failed(self):
        res, winner, roles, bestofs = self._run(
            draft_fails=("draft-v0", "draft-v1"))
        self.assertFalse(res["ok"])
        self.assertIsNone(winner)
        self.assertEqual(bestofs, [])


if __name__ == "__main__":
    unittest.main()
