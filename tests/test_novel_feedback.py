"""Rejection provenance, sample experiments, and prompt reuse without real LLM calls."""
import copy
import json
from unittest.mock import patch

from base import BaseTest


class NovelFeedbackTests(BaseTest):
    def row(self):
        from app.core import novel_feedback as nf
        return copy.deepcopy(nf.list_feedback()[0])

    def task(self, goal="七猫鉴宝，优化情节推进"):
        return {"type": "serial_novel", "title": "新小说", "goal": goal,
                "serial": {"chapters": 1}}

    def trial(self):
        return {"id": "trial-1", "date": "2026-10-16", "baseline_version": "v1",
                "revision_version": "v2", "change": "让代价进入交割选择",
                "method": "人工连读对照", "read_scope": "第一章前一千字",
                "baseline_excerpt": "他记下编号以后决定等到第二天再去问。",
                "revision_excerpt": "他付清验货费，先把自己的押金递了过去。",
                "baseline_quote": "他记下编号以后决定等到第二天再去问。",
                "revision_quote": "他付清验货费，先把自己的押金递了过去。",
                "assessment": "本小样的选择代价已可见，整章效果仍需验证。",
                "result": "improved"}

    def test_seed_preserves_unknown_version_and_no_fake_trials(self):
        from app.core import novel_feedback as nf
        rows = nf.list_feedback()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(not r["trials"] for r in rows))
        self.assertIn("未留存", rows[0]["submission_version"])
        self.assertFalse(rows[0]["can_reapply"])

    def test_recall_filters_platform_genre_and_task_type(self):
        from app.core import novel_feedback as nf
        self.assertEqual(nf.block_for(self.task("番茄鉴宝，优化情节推进")), "")
        self.assertEqual(nf.block_for(dict(self.task("番茄鉴宝"), context="七猫曾指出情节推进弱")), "")
        self.assertEqual(nf.block_for(dict(self.task(), platform="fanqie")), "")
        self.assertEqual(nf.block_for(self.task("七猫历史种田")), "")
        self.assertEqual(nf.block_for({"type": "code", "goal": "七猫鉴宝"}), "")
        self.assertEqual([r["work"] for r in nf.relevant(self.task())], [self.row()["work"]])
        block = nf.block_for(self.task())
        self.assertIn("待完成", block)
        self.assertIn("无法再次申请", block)
        self.assertIn("非平台规则", block)
        self.assertLessEqual(len(block), nf.MAX_BLOCK_CHARS)

    def test_feedback_and_trial_append_preserve_original(self):
        from app.core import novel_feedback as nf
        original = self.row()
        trial = nf.record_trial(original["id"], self.trial())
        self.assertEqual(len(trial["revision_sha256"]), 64)
        nf.record_feedback(original)
        row = next(r for r in nf.list_feedback() if r["id"] == original["id"])
        self.assertEqual(row["original_quote"], original["original_quote"])
        self.assertEqual(len(row["trials"]), 1)
        self.assertIn("内部 improved", nf.block_for(self.task()))
        self.assertIn("不等于平台接受", nf.block_for(self.task()))
        self.assertIn(self.trial()["read_scope"], nf.block_for(self.task()))
        self.assertIn(self.trial()["baseline_quote"], nf.block_for(self.task()))
        self.assertIn(self.trial()["revision_quote"], nf.block_for(self.task()))
        with self.assertRaises(ValueError):
            nf.record_trial(original["id"], self.trial())
        changed = dict(original, original_quote="改掉旧原话")
        with self.assertRaises(ValueError):
            nf.record_feedback(changed)
        for key in ("source", "feedback_date", "submission_scope", "channel"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                nf.record_feedback(dict(original, **{key: "改写后的来源"}))
        with self.assertRaises(ValueError):
            nf.record_feedback(dict(original, judgment_type="adaptation_hypothesis"))
        with self.assertRaises(ValueError):
            nf.record_feedback(dict(original, can_reapply=True))

    def test_recheck_rejects_missing_or_fabricated_evidence(self):
        from app.core import novel_feedback as nf
        for field, value in (("baseline_quote", "这是没有出现在旧文中的虚构引文"),
                             ("revision_version", "v1"), ("result", "平台接受"),
                             ("assessment", "")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                nf.record_trial(self.row()["id"], dict(self.trial(), **{field: value}))
        with self.assertRaises(ValueError):
            nf.record_trial("missing", self.trial())

    def test_record_validates_boundary_and_keeps_platforms_independent(self):
        from app.core import novel_feedback as nf
        row = dict(self.row(), id="nf-fanqie-test", platform="fanqie",
                   work="测试鉴宝书", judgment_type="adaptation_hypothesis",
                   original_quote="尚无番茄拒稿原话，仅验证能力参与行动的适配假设。", can_reapply=None)
        nf.record_feedback(row)
        block = nf.block_for(self.task("番茄鉴宝"))
        self.assertIn("平台适配假设", block)
        self.assertNotIn("nf-qimao-", block)
        for field, value in (("judgment_type", "官方规则"), ("can_reapply", "false"),
                             ("source", ""), ("tags", [])):
            with self.subTest(field=field), self.assertRaises(ValueError):
                nf.record_feedback(dict(row, **{field: value}))

    def test_corrupt_optional_ledger_does_not_stop_writing(self):
        from app.core import novel_feedback as nf
        nf._file().write_text("broken", encoding="utf-8")
        self.assertEqual(nf.block_for(self.task()), "")
        with self.assertRaises(ValueError):
            nf.list_feedback()
        row = self.row_from_seed()
        for trial in ({"result": "improved"}, self.trial()):
            row["trials"] = [trial]
            nf._file().write_text(json.dumps([row]), encoding="utf-8")
            self.assertEqual(nf.block_for(self.task()), "")
        trial = nf._validate_trial(self.trial())
        trial["revision_sha256"] = "伪造的摘要"
        row["trials"] = [trial]
        nf._file().write_text(json.dumps([row]), encoding="utf-8")
        self.assertEqual(nf.block_for(self.task()), "")

    def row_from_seed(self):
        from app.core import novel_feedback as nf
        return json.loads(nf.SEED_FILE.read_text(encoding="utf-8"))[0]

    def test_feedback_survives_large_capability_blocks_without_scoring_lessons(self):
        from app.core import novel_feedback as nf, skills
        row = self.row()
        lesson = skills.upsert_lesson("serial_novel", "测试节奏教训", "测试", category="节奏爽点")
        with patch.object(skills, "pack_text", return_value="通用规则" * 10000):
            block, _ = skills.block_for(self.task())
        self.assertTrue(block.startswith("### 【相关小说拒稿复盘"))
        self.assertIn(row["original_quote"], block[:2200])
        self.assertIn("测试节奏教训", block)
        self.assertLessEqual(len(block), skills.MAX_INJECT_CHARS)
        before = skills.list_lessons()
        nf.record_trial(row["id"], self.trial())
        self.assertEqual(before, skills.list_lessons())
        self.assertTrue(lesson)

    def test_outline_and_rebuild_receive_relevant_records(self):
        from app.core import planner
        prompts = []
        def chat(*args, **kwargs):
            prompts.append(args[2])
            return {"ok": True, "text": json.dumps({"book_title": "书", "chapters": [
                {"title": "交割", "beats": "验底付款，迫使对手改变交割条件", "hook": "下一步"}]}),
                "cost_usd": 0, "tokens": 0}
        with patch.object(planner, "_orchestrator", return_value=({"id": "test"}, "test")), \
                patch.object(planner.modelhub, "chat", side_effect=chat), \
                patch.object(planner, "_log_usage"):
            outline = planner.make_serial_outline(self.task())
            planner.redesign_opening(self.task(), outline, critique="情节推进较弱")
        self.assertEqual(len(prompts), 2)
        self.assertTrue(all("nf-qimao-shentong-20261015" in p for p in prompts))


if __name__ == "__main__":
    import unittest
    unittest.main()
