# -*- coding: utf-8 -*-
"""实案串链回归（P2-D）：真实五章稿走完整条过签链，锁住每一环的结论。

第 7 节技术债第 2 条写的是「50 次实案修复没有对应回归用例——事故驱动而非用例
闭环」。这条链此前各环都有单测，但**都用合成文本**：真实稿件只有 `appeal` 一份
fixture 在用。于是改一次词表，体检的逐章状态、改稿单的段号锚点、提示词里的块序
都可能静默变样，没人发现。

样本：`tests/fixtures/appeal-yitou-liewu-ch1-5.md`（《一头猎物，三笔账》投稿原稿
前五章，原样截取）。任务字段按**界面新建**的样态给：没有 `appeal_genre`，标题与
目标都不含词表两票。

串起来守五件事：

1. **判据**：第 1、2 章零获得被拦；第 3 章给出真实坐标（P48 S2）；第 4 章单章空窗
   + 字数偏少进 warnings 而**不进 blockers**；第 5 章「欠项清了」被认成获得。
2. **体检**：`blocked` + 一句 ≤26 字结论只点被拦的两章；第 4 章 `blocked=False`。
3. **改稿单**：只给被拦的两章出锚点，段号与原稿一致（P4/P6、P59/P60），
   章末仍停在「若」上要说出来。
4. **提示词**：平台事实 → 开工约束 → 改稿单 → 经验库，这个顺序不许乱；
   总量不超过注入上限。
5. **改完立刻复判**：第 1 章补一次兑现，体检只报第 2 章、改稿单只剩第 2 章 ——
   结论是现算的，不是快照。
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import paths, skills
from core.quality import appeal, constraints, health, revise

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "appeal-yitou-liewu-ch1-5.md"
REAL_CONSTRAINTS = HERE.parent / "data" / "novel" / "constraints-hunt_1980s.json"

# 界面新建任务时作者手写的样态：没有题材字段，标题/目标都够不到词表两票。
TASK_EXTRA = {"title": "一头猎物，三笔账",
              "goal": "写一本一九八九年东北农村的故事：签单、进山、交货。"}

# 第 1 章补一次兑现：东西（野猪）+ 动作（放倒、抬上）同句。
PATCH_LINE = "沈砚一枪放倒那头野猪，喊赵铁柱抬上爬犁，过秤结了十八元补助。"


def _split(text):
    out, cur = [], None
    for line in text.splitlines():
        found = re.match(r"^#\s*第\s*(\d+)\s*章", line)
        if found:
            cur = {"chapter": int(found.group(1)),
                   "title": line.strip("# ").strip(), "lines": []}
            out.append(cur)
        elif cur is not None:
            cur["lines"].append(line)
    for item in out:
        item["text"] = "\n".join(item["lines"]).strip("\n")
    return out


class ChainTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not FIXTURE.is_file():
            raise unittest.SkipTest("缺少回归样本 tests/fixtures/appeal-yitou-liewu-ch1-5.md")
        if not REAL_CONSTRAINTS.is_file():
            raise unittest.SkipTest("缺少约束文件 data/novel/constraints-hunt_1980s.json")
        cls.chapters = _split(FIXTURE.read_text(encoding="utf-8"))
        if len(cls.chapters) < 5:
            raise unittest.SkipTest("回归样本不足五章")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.work = root / "work"
        self.work.mkdir()
        for chapter in self.chapters:
            self._write(chapter["chapter"], chapter["text"])
        self.data = root / "data"
        (self.data / "novel").mkdir(parents=True)
        shutil.copy(REAL_CONSTRAINTS, self.data / "novel" / REAL_CONSTRAINTS.name)
        (self.data / "skills.json").write_text("{}", encoding="utf-8")
        self._patches = [
            mock.patch.object(paths, "DATA_DIR", self.data),
            mock.patch.object(skills, "_FILE", self.data / "skills.json"),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self):
        for item in reversed(self._patches):
            item.stop()
        self._tmp.cleanup()

    def _write(self, number, body):
        (self.work / ("chapter-%02d.md" % number)).write_text(
            "# 第%d章\n\n%s\n" % (number, body), encoding="utf-8")

    def task(self, **extra):
        base = {"id": "t-signing-chain", "type": "serial_novel",
                "workdir": str(self.work)}
        base.update(TASK_EXTRA)
        base.update(extra)
        return base


class CriterionTest(ChainTest):
    """第 1 环：判据在真稿上的逐章结论。"""

    def test_two_opening_chapters_are_blocked(self):
        result = appeal.check_task(self.task(), use_model=False)
        self.assertTrue(result["enabled"])
        self.assertEqual(result["genre"], "hunt_1980s")
        self.assertEqual(len(result["blockers"]), 2)
        self.assertIn("第 1 章", result["blockers"][0])
        self.assertIn("第 2 章", result["blockers"][1])

    def test_fourth_chapter_is_a_warning_not_a_blocker(self):
        result = appeal.check_task(self.task(), use_model=False)
        joined = "\n".join(result["warnings"])
        self.assertIn("第 4 章", joined)
        self.assertIn("单章空窗", joined)
        self.assertIn("低于 2000 字", joined)
        self.assertFalse([b for b in result["blockers"] if "第 4 章" in b])

    def test_third_chapter_gain_has_a_real_coordinate(self):
        result = appeal.check_task(self.task(), use_model=False)
        third = next(c for c in result["chapters"] if c["chapter"] == 3)
        self.assertTrue(third["gains"])
        first = third["gains"][0]
        self.assertEqual(first["paragraph"], 48)      # 原稿真实段号，漂了就是判据改了
        self.assertIn("查验目标", first["text"])

    def test_fifth_chapter_debt_payoff_counts_as_gain(self):
        result = appeal.check_task(self.task(), use_model=False)
        fifth = next(c for c in result["chapters"] if c["chapter"] == 5)
        self.assertTrue(fifth["gains"], "第 5 章「欠项清了」必须被认成获得")


class HealthTest(ChainTest):
    """第 2 环：体检把判据翻成一屏能读懂的结论。"""

    def test_verdict_names_only_the_blocked_chapters(self):
        data = health.report(self.task(), now=1700000000)
        self.assertEqual(data["verdict"], health.VERDICT_BLOCKED)
        self.assertEqual(data["headline"], "第 1、2 章没给读者东西")
        self.assertLessEqual(len(data["headline"]), health.HEADLINE_MAX)
        self.assertEqual([c["chapter"] for c in data["chapters"]], [1, 2, 3, 4, 5])
        self.assertEqual([c["status"] for c in data["chapters"]],
                         [health.STATUS_EMPTY, health.STATUS_EMPTY,
                          health.STATUS_OK, health.STATUS_EMPTY, health.STATUS_OK])

    def test_gate_outside_chapter_is_not_flagged_as_blocked(self):
        data = health.report(self.task(), now=1700000000)
        fourth = data["chapters"][3]
        self.assertFalse(fourth["blocked"])
        self.assertEqual(fourth["status"], health.STATUS_EMPTY)
        self.assertIn("单章空窗", fourth["note"])

    def test_unjudged_promise_is_spelled_out(self):
        data = health.report(self.task(), now=1700000000)
        self.assertIn("快速进山", data["unjudged"])


class RevisionTest(ChainTest):
    """第 3 环：改稿单只给被拦的章，锚点与原稿段号一致。"""

    def _brief(self):
        result = appeal.check_task(self.task(), use_model=False)
        return revise.build(result, self.task())

    def test_only_blocked_chapters_get_anchors(self):
        brief = self._brief()
        self.assertTrue(brief["enabled"])
        self.assertEqual([c["chapter"] for c in brief["chapters"]], [1, 2])

    def test_first_chapter_anchor_is_the_real_paragraph(self):
        chapter = self._brief()["chapters"][0]
        marks = [(a["paragraph"], a["mark_text"]) for a in chapter["anchors"]]
        self.assertTrue(marks)
        self.assertEqual(marks[0][0], 4)
        self.assertIn("只提到东西", marks[0][1])
        self.assertIn(6, [p for p, _ in marks])

    def test_second_chapter_future_tense_and_pending_ending(self):
        chapter = self._brief()["chapters"][1]
        marks = [(a["paragraph"], a["mark_text"]) for a in chapter["anchors"]]
        self.assertIn(59, [p for p, _ in marks])
        self.assertIn(60, [p for p, _ in marks])
        self.assertTrue(chapter["closing"]["pending"])
        self.assertEqual(chapter["closing"]["marker"], "若")
        self.assertTrue(any("计划而不是结果" in a for a in chapter["advice"]))

    def test_rendered_brief_is_not_empty_and_says_which_chapters(self):
        text = revise.render(self._brief())
        self.assertIn("第 1 章", text)
        self.assertIn("第 2 章", text)
        self.assertNotIn("### 第 3 章", text)
        self.assertGreater(len(text), 400)


class PromptTest(ChainTest):
    """第 4 环：提示词里四块的顺序与预算。"""

    def test_blocks_are_ordered_fact_then_rule_then_draft(self):
        text, _used = skills.block_for(self.task())
        self.assertIn("驳回约束", text)
        self.assertIn("## 改稿单", text)
        self.assertLess(text.index("驳回约束"), text.index("## 改稿单"))
        self.assertLess(text.index("## 改稿单"), text.index("## 经验库"))

    def test_prompt_stays_within_budget(self):
        text, _used = skills.block_for(self.task())
        self.assertTrue(text)
        self.assertLessEqual(len(text), skills.MAX_INJECT_CHARS)
        self.assertNotIn("…（已截断）", text)

    def test_constraints_block_is_the_real_file(self):
        block = constraints.block_for(self.task())
        self.assertIn("签约评估未通过", block)
        self.assertIn("综合质量一般", block)
        rules = re.findall(r"^\d+\. \[", block, re.M)
        self.assertGreaterEqual(len(rules), 5)


class RecheckTest(ChainTest):
    """第 5 环：改完立刻复判，结论跟着变（不落盘、不缓存）。"""

    def test_fixing_chapter_one_moves_the_verdict(self):
        before = health.report(self.task(), now=1700000000)
        self.assertEqual(before["headline"], "第 1、2 章没给读者东西")

        chapter_one = next(c for c in self.chapters if c["chapter"] == 1)["text"]
        self._write(1, PATCH_LINE + "\n\n" + chapter_one)

        after = health.report(self.task(), now=1700000000)
        self.assertEqual(after["chapters"][0]["status"], health.STATUS_OK)
        self.assertGreaterEqual(after["chapters"][0]["gains"], 1)
        self.assertEqual(after["verdict"], health.VERDICT_BLOCKED)   # 第 2 章还拦着
        self.assertNotIn("第 1 章", after["headline"])
        self.assertIn("第 2 章", after["headline"])

        brief = revise.build(appeal.check_task(self.task(), use_model=False), self.task())
        self.assertEqual([c["chapter"] for c in brief["chapters"]], [2])


if __name__ == "__main__":
    unittest.main()
