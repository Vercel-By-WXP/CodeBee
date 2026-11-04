# -*- coding: utf-8 -*-
"""签约评估必须逐条回应「平台原话驳回」（outcome 台账 → feedback_check）。

背景：平台结果回流（`core/publish/outcome.py`）一直只作为**事实块**进提示词，模型
可以整段无视；而从复盘台账（`novel_feedback`）进来的驳回记录是必须逐条回答的——
答不出就判这次评估失败。同一句驳回原话，作者手工抄进复盘台账要被核验，自动回流
进来反而不用，等于「作者少点一次鼠标，判据就松一格」。这里把两条来源拉齐，并锁住
四条边界：

1. 只有**本书自己**、**带原话**的**「被判」**条目才立核验项；
2. 别书的同分类类比样本、没有原话的、与复盘台账同一句原话的，都不立；
3. 台账坏了 = 这次评估判不了（**不是**「这本书没有平台事实」）；
4. 答了 `not_addressed` 会变成带「平台驳回」归类的修复项，和复盘项一样能进改稿单。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import novel_feedback, novel_quality, paths, pipeline_review
from core.publish import outcome

QUOTE = "开篇缺乏吸引力，第一章没有给出任何实质进展"
TEXT = "沈砚把猎物抬上爬犁，雪地里留下一道深沟。他数了三遍铜钱，还是少了两枚。"


def _task(tid="t-mine", type_="serial_novel", platform="fanqie", title="一头猎物"):
    return {
        "id": tid,
        "type": type_,
        "title": title,
        "platform": platform,
        "goal": "悬疑 民俗",
        "book_meta": {platform: {"status": "done",
                                 "data": {"category": "悬疑惊悚",
                                          "tags_theme": ["民俗"]}}},
    }


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._saved = paths.PUBLISH_DIR
        paths.PUBLISH_DIR = self.root
        # 复盘台账默认返回空：本组测的是「平台事实」这条来源，别让真实种子
        # 数据把 records 撑起来（否则断言就会依赖磁盘上有什么）。
        self.nf = mock.patch.object(novel_feedback, "relevant", return_value=[])
        self.nf_mock = self.nf.start()

    def tearDown(self):
        self.nf.stop()
        paths.PUBLISH_DIR = self._saved
        self._tmp.cleanup()

    def write_ledger(self, data):
        (self.root / "outcomes.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")


class OutcomeFeedbackRecordTests(_Base):
    def test_rejection_with_quote_becomes_must_answer_item(self):
        task = _task()
        # 走真实写入路径：保证 fixture 形状与线上一致（字段少一个就会在这里先红）。
        outcome.record(task, "fanqie", "rejected", quote=QUOTE)

        records, error = pipeline_review._signing_feedback_records(task)

        self.assertIsNone(error)
        self.assertEqual([r["id"] for r in records], ["平台事实-fanqie"])
        record = records[0]
        self.assertEqual(record["dim"], "平台驳回")
        self.assertEqual(record["original_quote"], QUOTE)
        self.assertIn(QUOTE, record["recheck_question"])
        self.assertEqual(record["experiment"], "")
        self.assertTrue(record["feedback_date"])

    def test_signing_rejection_is_included_as_well(self):
        outcome.record(_task(), "qimao", "signing_rejected", quote=QUOTE)
        records, _ = pipeline_review._signing_feedback_records(
            _task(platform="qimao"))
        self.assertEqual([r["id"] for r in records], ["平台事实-qimao"])
        self.assertIn("签约被拒", records[0]["recheck_question"])

    def test_progress_only_entry_is_not_a_check_item(self):
        # 自动校准只写章节进度，没有 stage / 原话；它不是结论，不该要人回答。
        outcome.note_progress("t-mine", "fanqie", live=12, pending=2, total=14)
        records, error = pipeline_review._signing_feedback_records(_task())
        self.assertIsNone(error)
        self.assertEqual(records, [])

    def test_bad_stage_without_quote_is_left_to_the_fact_block(self):
        # 平台只说「不符合规范」时没有可核验的对象：不立核验项（否则模型只能
        # 凭空编一句「已处理」），但事实块仍会告诉作者「这本书被拒过」。
        task = _task()
        outcome.record(task, "fanqie", "rejected", quote="")
        records, _ = pipeline_review._signing_feedback_records(task)
        self.assertEqual(records, [])
        self.assertIn("审核未通过", outcome.block_for(task))

    def test_good_stage_is_not_a_check_item(self):
        task = _task()
        outcome.record(task, "fanqie", "signed", quote=QUOTE)
        records, _ = pipeline_review._signing_feedback_records(task)
        self.assertEqual(records, [])

    def test_other_books_records_are_not_this_manuscripts_burden(self):
        self.write_ledger({"t-other": {"fanqie": {
            "stage": "rejected", "at": "2026-10-01 10:00:00",
            "quote": QUOTE, "category": "悬疑惊悚"}}})
        records, _ = pipeline_review._signing_feedback_records(_task())
        self.assertEqual(records, [])

    def test_same_quote_already_in_the_review_ledger_is_not_duplicated(self):
        task = _task()
        outcome.record(task, "fanqie", "rejected", quote=QUOTE)
        self.nf_mock.return_value = [{
            "id": "nf-fanqie-1", "platform": "fanqie", "work": "一头猎物",
            "original_quote": QUOTE + "。请调整开篇",
            "recheck_question": "开篇是否已给出实质进展？", "experiment": "重写第一章",
        }]

        records, error = pipeline_review._signing_feedback_records(task)

        self.assertIsNone(error)
        self.assertEqual([r["id"] for r in records], ["nf-fanqie-1"])

    def test_platform_facts_come_before_review_ledger_rows(self):
        task = _task()
        outcome.record(task, "fanqie", "rejected", quote=QUOTE)
        self.nf_mock.return_value = [{
            "id": "nf-qimao-1", "platform": "qimao", "work": "别书",
            "original_quote": "另一句完全不同的问题", "recheck_question": "？",
            "experiment": "改",
        }]
        records, _ = pipeline_review._signing_feedback_records(task)
        self.assertEqual([r["id"] for r in records],
                         ["平台事实-fanqie", "nf-qimao-1"])

    def test_non_novel_task_never_gets_platform_facts(self):
        task = _task(type_="review")
        outcome.record(_task(), "fanqie", "rejected", quote=QUOTE)
        self.assertEqual(pipeline_review._outcome_feedback_records(task), [])


class BrokenLedgerTests(_Base):
    def test_corrupt_ledger_aborts_the_evaluation(self):
        (self.root / "outcomes.json").write_text("{ not json", encoding="utf-8")

        records, error = pipeline_review._signing_feedback_records(_task())

        self.assertEqual(records, [])
        self.assertEqual(error, "平台结果台账不可用，签约评估未执行")
        # 界面读路径保持宽容：一个坏文件不能让结果卡整页崩。
        self.assertEqual(outcome.entries("t-mine"), {})
        with self.assertRaises(ValueError):
            outcome.own_entries("t-mine")

    def test_missing_ledger_is_not_an_error(self):
        records, error = pipeline_review._signing_feedback_records(_task())
        self.assertEqual((records, error), ([], None))


class PromptAndIssueTests(_Base):
    def _records(self):
        task = _task()
        outcome.record(task, "fanqie", "rejected", quote=QUOTE)
        records, error = pipeline_review._signing_feedback_records(task)
        self.assertIsNone(error)
        return records

    def test_prompt_asks_for_each_platform_fact(self):
        prompt = pipeline_review._feedback_check_prompt(self._records(), {1: TEXT})
        self.assertIn("## 拒稿反馈与平台原话逐条核验", prompt)
        self.assertIn("平台事实-fanqie", prompt)
        self.assertIn(QUOTE, prompt)
        # 平台事实没有我们写的「建议实验」，不许渲染成 `建议实验=""` 的空壳
        self.assertNotIn('建议实验=""', prompt)

    def test_unanswered_platform_fact_fails_the_check(self):
        errors = novel_quality.feedback_check_errors({}, {1: TEXT}, self._records())
        # 文案不套「拒稿复盘 XX」：平台事实的 id 自带来源，叠一层就成了
        # 「平台事实 平台事实-fanqie」。
        self.assertIn("平台事实-fanqie 缺少逐条处理结论", errors)

    def test_unaddressed_platform_fact_becomes_a_platform_issue(self):
        review = {"feedback_check": [{
            "id": "平台事实-fanqie", "status": "not_addressed", "chapter": 1,
            "quote": "他数了三遍铜钱，还是少了两枚",
            "analysis": "开篇仍以清点猎物开场，没有出现平台要求的进展事件。",
        }]}
        issues = novel_quality.feedback_check_issues(
            review, self._records(), chapters={1: TEXT})
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["dim"], "平台驳回")
        self.assertEqual(issues[0]["severity"], "major")
        self.assertIn("平台事实-fanqie", issues[0]["note"])
        self.assertIn("平台审核未通过", issues[0]["revision_direction"])


if __name__ == "__main__":
    unittest.main()
