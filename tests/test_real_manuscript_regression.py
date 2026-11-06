# -*- coding: utf-8 -*-
"""真稿全链回归台账：一份样本 + 预期结论 + 真被判过的样本数（决策 18）。

2026-11-06 度量基线（先量再写，见当日日志）：
  唯一真稿 `tests/fixtures/appeal-yitou-liewu-ch1-5.md`，**5/5 章真被判过**；
  appeal：blockers 2（第 1/2 章无获得）、warnings 2（第 4 章单章空窗 + 短章）；
  health：verdict=blocked、5 章 `[empty, empty, ok, empty, ok]`、unjudged 1
  （「快速进山」这类节奏承诺没有机器判据，必须说出来而不是悄悄跳过）；
  revise：2 个章锚点，只给被拦的章。

为什么还要这一条：此前 3 个守测（`test_quality_appeal.py` /
`test_quality_genre_scope.py` / `test_signing_chain_e2e.py`）各自冻结局部结论，
没人保证「写完时的体检卡」与「按发布时的闸门」对同一份稿子说同一句话。
体检卡给绿灯、发布被拦，是过签闭环里最贵的漂移。

**不造合成样本**：第二份真样本必须来自真实驳回 / 过签，额度见底期拿不到；
在拿到之前，这份台账就是全部真稿覆盖，别拿绿灯当覆盖率。
"""
from __future__ import annotations

import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from core import paths, skills
    from core.quality import appeal, health, revise
except ImportError:  # 仓库根在 path 的布局
    from app.core import paths, skills
    from app.core.quality import appeal, health, revise

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "appeal-yitou-liewu-ch1-5.md"
CONSTRAINTS = HERE.parent / "data" / "novel" / "constraints-hunt_1980s.json"
TASK_EXTRA = {"title": "一头猎物，三笔账",
              "goal": "写一本一九八九年东北农村的故事：签单、进山、交货。"}

# 已冻结的真稿结论（2026-11-06 实跑值，不是期望出来的）
EXPECTED_STATUS = ["empty", "empty", "ok", "empty", "ok"]
EXPECTED_GAINS = [0, 0, 2, 0, 4]


def _split(text):
    out, cur = [], None
    for line in text.splitlines():
        found = re.match(r"^#\s*第\s*(\d+)\s*章", line)
        if found:
            cur = {"chapter": int(found.group(1)), "lines": []}
            out.append(cur)
        elif cur is not None:
            cur["lines"].append(line)
    for item in out:
        item["text"] = "\n".join(item["lines"]).strip("\n")
    return out


class RealManuscriptChainTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not FIXTURE.is_file():
            raise unittest.SkipTest("缺少真稿样本 tests/fixtures/appeal-yitou-liewu-ch1-5.md")
        if not CONSTRAINTS.is_file():
            raise unittest.SkipTest("缺少约束文件 data/novel/constraints-hunt_1980s.json")
        cls.chapters = _split(FIXTURE.read_text(encoding="utf-8"))
        if len(cls.chapters) < 5:
            raise unittest.SkipTest("真稿样本不足五章")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.work, self.data = root / "work", root / "data"
        self.work.mkdir()
        (self.data / "novel").mkdir(parents=True)
        shutil.copy(CONSTRAINTS, self.data / "novel" / CONSTRAINTS.name)
        (self.data / "skills.json").write_text("{}", encoding="utf-8")
        for ch in self.chapters:
            (self.work / ("chapter-%02d.md" % ch["chapter"])).write_text(
                "# 第%d章\n\n%s\n" % (ch["chapter"], ch["text"]), encoding="utf-8")
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

    def task(self, **extra):
        base = {"id": "t-real-manuscript", "type": "serial_novel",
                "workdir": str(self.work)}
        base.update(TASK_EXTRA)
        base.update(extra)
        return base

    def _chain(self):
        task = self.task()
        result = appeal.check_task(task, use_model=False)
        report = health.report(task, now=1700000000)
        brief = revise.build(result, task)
        return result, report, brief

    def test_every_real_chapter_was_actually_judged(self):
        """决策 18：报真被判过的样本数，零样本绿灯不算数。"""
        result, report, _ = self._chain()
        rows = result.get("chapters") or []
        judged = sum(1 for c in rows
                     if c.get("gated") or c.get("gains") or
                     c.get("warnings") or c.get("blockers"))
        print("真稿真被判过的章数: %d / %d" % (judged, len(rows)))
        self.assertEqual(judged, 5, "真稿有章没被判据碰过")
        self.assertEqual(len(report.get("chapters") or []), 5)
        self.assertTrue(all(c.get("words") for c in rows), "有章连字数都没量")

    def test_frozen_full_chain_verdict(self):
        """一份真稿 → 全链预期结论，改判据必须先改这里。"""
        result, report, brief = self._chain()
        self.assertTrue(result.get("enabled"))
        self.assertEqual(result.get("genre"), "hunt_1980s")
        self.assertEqual(len(result["blockers"]), 2)
        self.assertIn("第 1 章", result["blockers"][0])
        self.assertIn("第 2 章", result["blockers"][1])
        self.assertEqual(len(result["warnings"]), 2)
        self.assertTrue(any("单章空窗" in w for w in result["warnings"]))
        self.assertTrue(any("低于 2000 字" in w for w in result["warnings"]))

        self.assertEqual(report["verdict"], health.VERDICT_BLOCKED)
        self.assertEqual([c["status"] for c in report["chapters"]], EXPECTED_STATUS)
        self.assertEqual([c["gains"] for c in report["chapters"]], EXPECTED_GAINS)
        self.assertEqual(len(report["unjudged"]), 1)
        self.assertLessEqual(len(report["headline"]), health.HEADLINE_MAX)
        self.assertEqual(len(brief["chapters"]), 2, "改稿单只该给被拦的两章")

    def test_health_card_and_release_gate_say_the_same_thing(self):
        """体检卡与发布闸门必须同源：别出现绿灯发布被拦。"""
        result, report, _ = self._chain()
        self.assertEqual(report["blockers"], result["blockers"])
        self.assertEqual(report["warnings"], result["warnings"])
        self.assertNotIn(health.VERDICT_PASS, [report["verdict"]])

    def test_two_read_paths_agree_on_short_chapter(self):
        """整篇 analyze 1406 字 vs 章节文件 check_task 1410 字（补写标题行 +4）。

        两条路径都低于 2000 字、都只 warn 不 block；差值只是标题行计数，
        不许哪天把标题算进正文后再悄悄改掉短章结论。
        """
        whole = appeal.analyze(FIXTURE.read_text(encoding="utf-8"))
        result, _, _ = self._chain()
        whole_four = next(c for c in whole["chapters"] if c["chapter"] == 4)
        file_four = next(c for c in result["chapters"] if c["chapter"] == 4)
        self.assertLess(whole_four["words"], appeal.DEFAULT_MIN_WORDS)
        self.assertLess(file_four["words"], appeal.DEFAULT_MIN_WORDS)
        self.assertLessEqual(abs(whole_four["words"] - file_four["words"]), 6,
                             "两条读稿路径的字数口径漂移超出标题行")
        self.assertEqual(whole_four["gains"], file_four["gains"])
        self.assertFalse(any("第 4 章" in b for b in result["blockers"]))
