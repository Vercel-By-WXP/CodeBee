# -*- coding: utf-8 -*-
"""过签体检（P2-A）单测：把判据结论提前到「稿子写完时」，而不是「按发布按钮时」。

守五件事：

1. **一屏能读懂**：逐章一行给状态（ok / empty / short），右栏 188 宽也放得下；
   `verdict` 四档（pass / warn / blocked / unknown）与界面徽标一一对应。
2. **适用边界不放宽**：不是长篇、没目录、没章节稿、认不出题材——一律 unknown
   带原因，**不出 blocker**（体检卡在文档上乱报一次就再没人看）。
3. **字数是短板不是硬伤**：字数不达标记 short / warn，不进 blockers。
4. **「没人替你查」要说出来**：没有机器判据的承诺（如「快速进山」这类节奏承诺）
   单独列 `unjudged`，不能悄悄跳过让作者以为查过了。
5. **报告不带正文**：只带段号/句号与 ≤40 字摘录；正文顺着 API 出去就是第二个泄稿口。
"""
from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from core.quality import appeal, health


def _result(chapters, genre="hunt_1980s"):
    """造一份 `appeal.analyze()` 形状的结果（不跑判据，只验组装）。"""
    return {"enabled": True, "genre": genre, "genre_name": "年代打猎文",
            "chapter_count": len(chapters), "chapters": chapters,
            "promises": appeal._promise_table(
                appeal.profile_for(genre), chapters),
            "blockers": [b for c in chapters for b in c.get("blockers") or ()],
            "warnings": [w for c in chapters for w in c.get("warnings") or ()],
            "model_used": False, "model_note": ""}


def _chapter(number, gains=1, words=2600, gated=True, blockers=(), warnings=()):
    rows = [{"type": "hunt", "label": "猎获", "paragraph": 4, "sentence": 2,
             "text": "沈砚一枪放倒那头野猪，把猎物抬上爬犁。"}][:gains]
    return {"chapter": number, "title": "", "words": words, "gains": rows,
            "gated": gated, "blockers": list(blockers), "warnings": list(warnings)}


class ReportShapeTest(unittest.TestCase):
    def _report(self, result, task=None):
        with unittest.mock.patch.object(appeal, "check_task", return_value=result):
            return health.report(task or {"type": "serial_novel"}, now=1700000000)

    def test_pass_when_every_gated_chapter_has_gain(self):
        data = self._report(_result([_chapter(1), _chapter(2), _chapter(3)]))
        self.assertEqual(data["verdict"], health.VERDICT_PASS)
        self.assertEqual([c["status"] for c in data["chapters"]],
                         [health.STATUS_OK] * 3)
        self.assertIn("前 3 章都兑现了", data["headline"])
        self.assertEqual(data["blockers"], [])
        self.assertEqual(data["checked_at"], 1700000000)

    def test_blocked_when_gated_chapter_has_no_gain(self):
        empty = _chapter(1, gains=0, blockers=["第 1 章没有兑现任何「获得」"])
        data = self._report(_result([empty, _chapter(2), _chapter(3)]))
        self.assertEqual(data["verdict"], health.VERDICT_BLOCKED)
        self.assertEqual(data["chapters"][0]["status"], health.STATUS_EMPTY)
        self.assertEqual(data["chapters"][0]["gains"], 0)
        self.assertIn("第 1 章", data["headline"])
        self.assertEqual(len(data["blockers"]), 1)

    def test_single_empty_chapter_past_gate_is_warn_not_blocked(self):
        # 门禁外的单章空窗：读者能接受过渡章，这里只记短板，不拦。
        gap = _chapter(4, gains=0, gated=False,
                       warnings=["第 4 章没有兑现「获得」（单章空窗，未达连续线）"])
        data = self._report(_result([_chapter(1), _chapter(2), _chapter(3), gap]))
        self.assertEqual(data["verdict"], health.VERDICT_WARN)
        self.assertEqual(data["blockers"], [])
        self.assertEqual(data["chapters"][3]["status"], health.STATUS_EMPTY)
        self.assertIn("单章空窗", data["headline"])

    def test_short_chapter_is_short_but_has_gains(self):
        thin = _chapter(2, words=1200, warnings=["第 2 章仅 1200 字（低于 2000 字）"])
        data = self._report(_result([_chapter(1), thin, _chapter(3)]))
        row = data["chapters"][1]
        self.assertEqual(row["status"], health.STATUS_SHORT)
        self.assertEqual(row["gains"], 1)          # 有获得，只是短
        self.assertIn("1200", row["note"])
        self.assertEqual(data["verdict"], health.VERDICT_WARN)
        self.assertEqual(data["blockers"], [])     # 字数不进 blockers

    def test_headline_only_names_really_blocked_chapters(self):
        # 第 1 章被拦 + 第 4 章只是单章空窗：一句话里不能把两件事说成一样重。
        gap = _chapter(4, gains=0, gated=False,
                       warnings=["第 4 章没有兑现「获得」（单章空窗，未达连续线）"])
        first = _chapter(1, gains=0, blockers=["第 1 章没有兑现任何「获得」"])
        data = self._report(_result([first, _chapter(2), _chapter(3), gap]))
        self.assertEqual(data["verdict"], health.VERDICT_BLOCKED)
        self.assertIn("第 1 章", data["headline"])
        self.assertNotIn("第 4 章", data["headline"])
        self.assertFalse(data["chapters"][3]["blocked"])
        self.assertTrue(data["chapters"][0]["blocked"])

    def test_headline_is_short_enough_for_the_rail(self):
        many = [_chapter(n, gains=0, blockers=["第 %d 章没有兑现" % n])
                for n in range(1, 7)]
        data = self._report(_result(many))
        self.assertLessEqual(len(data["headline"]), health.HEADLINE_MAX)


class UnknownTest(unittest.TestCase):
    """不适用：一律 unknown + 原因，绝不出 blocker。"""

    def _unknown(self, reason):
        with unittest.mock.patch.object(
                appeal, "check_task", return_value={"enabled": False,
                                                    "reason": reason}):
            return health.report({"type": "code"})

    def test_each_reason_has_human_text(self):
        for reason in ("not_serial", "no_workdir", "no_text", "unknown_genre"):
            data = self._unknown(reason)
            self.assertEqual(data["verdict"], health.VERDICT_UNKNOWN, reason)
            self.assertFalse(data["enabled"])
            self.assertEqual(data["chapters"], [])
            self.assertEqual(data["blockers"], [])
            self.assertTrue(data["headline"])
            self.assertIn(data["headline"], data["reason_text"])

    def test_detail_task_is_unknown_not_pass(self):
        # 「pass」会被界面画成绿标——判不了的时候给绿标等于骗人。
        data = self._unknown("unknown_genre")
        self.assertNotEqual(data["verdict"], health.VERDICT_PASS)


class UnjudgedTest(unittest.TestCase):
    def test_pace_promise_is_listed_as_unjudged(self):
        result = _result([_chapter(1), _chapter(2), _chapter(3)])
        with unittest.mock.patch.object(appeal, "check_task", return_value=result):
            data = health.report({"type": "serial_novel"})
        # 内置画像里「快速进山」是 pace 类，没有机器判据。
        self.assertIn("快速进山", data["unjudged"])
        # 有判据的三条不进 unjudged。
        self.assertNotIn("收获有来路", data["unjudged"])


class RealChainTest(unittest.TestCase):
    """真链路：真 workdir + 真判据，字段要能贯通；报告不许带正文。"""

    WORDS = "沈砚一枪放倒那头野猪，把猎物抬上爬犁。" * 120     # > 2000 字

    def _task(self, tmp, body, name="chapter-01.md"):
        root = Path(tmp)
        (root / name).write_text(body, encoding="utf-8")
        return {"id": "t1", "type": "serial_novel", "workdir": str(root),
                "appeal_genre": "hunt_1980s"}

    def test_real_run_reports_status_per_chapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = self._task(tmp, self.WORDS)
            (Path(tmp) / "chapter-02.md").write_text(
                "沈砚看着任务单，没有说话。\n\n" + "他想起父亲留下的那把老枪。\n\n" * 60,
                encoding="utf-8")
            data = health.report(task)
        self.assertTrue(data["enabled"])
        self.assertEqual(data["genre"], "hunt_1980s")
        self.assertEqual(data["chapters"][0]["status"], health.STATUS_OK)
        self.assertEqual(data["chapters"][1]["status"], health.STATUS_EMPTY)
        self.assertEqual(data["verdict"], health.VERDICT_BLOCKED)
        self.assertTrue(data["chapters"][0]["first_gain"]["paragraph"])

    def test_report_does_not_carry_chapter_body(self):
        marker = "这一段只应留在原稿里不要出现在接口"
        body = self.WORDS + "\n\n" + marker + "\n\n" + "他数了数猎物，三头。\n"
        with tempfile.TemporaryDirectory() as tmp:
            task = self._task(tmp, body)
            data = health.report(task)
        blob = json.dumps(data, ensure_ascii=False)
        self.assertNotIn(marker, blob)
        for row in data["chapters"]:
            self.assertLessEqual(len(row["first_gain"].get("text") or ""), 40)

    def test_non_novel_task_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = self._task(tmp, self.WORDS)
            task["type"] = "doc"
            data = health.report(task)
        self.assertEqual(data["verdict"], health.VERDICT_UNKNOWN)
        self.assertEqual(data["reason"], "not_serial")
