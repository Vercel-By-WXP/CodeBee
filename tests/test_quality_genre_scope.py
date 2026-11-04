# -*- coding: utf-8 -*-
"""题材识别口径（P2-D）：判据与开工约束必须认出同一个题材。

2026-11-04 复核发现两处各认各的，同一条链上会给出互相矛盾的结论：

1. **样本不同**：`appeal.check_task` 会看正文；`quality.constraints` 只看标题 /
   目标 / 背景。界面新建一本打猎文，标题《一头猎物，三笔账》里一个词表词都没有
   （词表收的是「猎获」，不是「猎物」），目标写「年代打猎文」也只有 1 票、够不到
   2 票门槛 —— 于是体检卡说「第 1、2 章没给读者东西」，提示词里却没有那份开工
   约束。作者照着改，也只改到判据那一半。
2. **宽分类挡死正文**：原 `_auto_genre` 只要题材字段非空就拿去查画像，认不出就
   返回 None 并放弃，正文检测永远轮不到 —— 一本打猎文只要分类填成「都市」，
   判据就彻底不判（`unknown_genre`）。

守五件事：

1. **界面新建样态也要认出来**：无题材字段、标题/目标都不含词表词，靠正文认。
2. **平台分类不挡正文**：`genre` 是平台/产品分类，与内置画像不是同一套命名。
3. **显式题材优先且严格**：`appeal_genre` 点了名却认不出时，判据不拿正文改判；
   约束也不许偷偷兜底（「判不了」的书不该收到打猎约束）。
4. **不误判**：非打猎正文两边都不认，正文只命中 1 个词也不算。
5. **两边一致**：对每一种任务样态，`appeal.enabled` 与「约束块非空」必须同真假。
"""
from __future__ import annotations

import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import paths
from core.quality import appeal, constraints

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "appeal-yitou-liewu-ch1-5.md"
REAL_CONSTRAINTS = HERE.parent / "data" / "novel" / "constraints-hunt_1980s.json"

# 界面新建任务时作者手写的样态：既没有 appeal_genre，标题里也没有词表词。
UI_TITLE = "一头猎物，三笔账"
UI_GOAL = "写一本一九八九年东北农村的故事：签单、进山、交货。"

CITY_BODY = ("# 第1章 天台\n\n"
             + "林晚在天台上听见老板打电话，说要把项目组整个裁掉。"
               "她想起陈默那件灰衬衫，心里发紧。\n\n" * 60)

ONE_WORD_BODY = ("# 第1章 签单\n\n"
                 + "沈砚在秤房里坐着，铅笔尖悬在签名栏上，窗外全是雪。\n\n" * 60)


def _split(text):
    """按 `# 第N章` 切成章节（与 fixture 的写法对齐，不猜别的标题格式）。"""
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


class _FixtureChain(unittest.TestCase):
    """真稿五章 + 真约束文件；数据目录整体重定向到临时目录（不碰真实库）。"""

    @classmethod
    def setUpClass(cls):
        if not FIXTURE.is_file():
            raise unittest.SkipTest("缺少回归样本 tests/fixtures/appeal-yitou-liewu-ch1-5.md")
        if not REAL_CONSTRAINTS.is_file():
            raise unittest.SkipTest("缺少约束文件 data/novel/constraints-hunt_1980s.json")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.work = root / "work"
        self.work.mkdir()
        for chapter in _split(FIXTURE.read_text(encoding="utf-8")):
            (self.work / ("chapter-%02d.md" % chapter["chapter"])).write_text(
                "# " + chapter["title"] + "\n\n" + chapter["text"] + "\n",
                encoding="utf-8")
        self.data = root / "data"
        (self.data / "novel").mkdir(parents=True)
        shutil.copy(REAL_CONSTRAINTS, self.data / "novel" / REAL_CONSTRAINTS.name)
        self._patch = mock.patch.object(paths, "DATA_DIR", self.data)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def task(self, **extra):
        base = {"id": "t-genre-scope", "type": "serial_novel",
                "title": UI_TITLE, "goal": UI_GOAL, "workdir": str(self.work)}
        base.update(extra)
        return base


class UiCreatedNovelTest(_FixtureChain):
    """界面新建样态：没有题材字段，靠正文认出打猎文；判据与约束都不许缺席。"""

    def test_ui_created_novel_is_recognised_from_body(self):
        task = self.task()
        self.assertEqual(appeal.genre_of(task), "hunt_1980s")
        result = appeal.check_task(task, use_model=False)
        self.assertTrue(result["enabled"])
        self.assertEqual(result["genre"], "hunt_1980s")

    def test_ui_created_novel_gets_the_rejection_constraints_too(self):
        # 改前：标题「猎物」不在词表、目标只 1 票 → 约束块为空；判据却已经拦了第 1、2 章。
        block = constraints.block_for(self.task())
        self.assertTrue(block, "判据认了题材，开工约束却没下发")
        self.assertIn("驳回约束", block)
        self.assertIn("签约评估未通过", block)      # 平台原话，不是二次解读
        self.assertGreaterEqual(block.count("\n1. "), 1)

    def test_platform_category_does_not_block_body_detection(self):
        # 改前：genre="都市" → profile_for 认不出 → 直接 unknown_genre，正文检测不执行。
        task = self.task(genre="都市")
        result = appeal.check_task(task, use_model=False)
        self.assertTrue(result["enabled"])
        self.assertEqual(result["genre"], "hunt_1980s")
        self.assertTrue(constraints.block_for(task))


class ExplicitGenreTest(_FixtureChain):
    """`appeal_genre` 是「这本书是什么题材」的明示：认不出就是认不出。"""

    def test_explicit_unknown_genre_is_not_rescued_by_body(self):
        task = self.task(appeal_genre="悬疑推理")
        result = appeal.check_task(task, use_model=False)
        self.assertFalse(result["enabled"])
        self.assertEqual(result["reason"], "unknown_genre")
        # 判据说「认不出题材」，提示词里就不许出现打猎约束——两处结论必须同向。
        self.assertEqual(constraints.block_for(task), "")

    def test_explicit_known_genre_wins_over_body(self):
        task = self.task(appeal_genre="hunt_1980s")
        self.assertTrue(appeal.check_task(task, use_model=False)["enabled"])
        self.assertTrue(constraints.block_for(task))


class NoMisjudgeTest(_FixtureChain):
    """不误判：非打猎正文、只命中一个词，都不算认出来。"""

    def _with_body(self, name, body, **extra):
        work = Path(self._tmp.name) / name
        work.mkdir()
        (work / "chapter-01.md").write_text(body, encoding="utf-8")
        return self.task(workdir=str(work), **extra)

    def test_city_story_is_unknown_on_both_sides(self):
        task = self._with_body("city", CITY_BODY, title="加班夜的天台",
                               goal="都市女频短篇连载，共 3 章。", genre="都市")
        self.assertEqual(appeal.genre_of(task), "")
        result = appeal.check_task(task, use_model=False)
        self.assertEqual(result["reason"], "unknown_genre")
        self.assertEqual(constraints.block_for(task), "")

    def test_one_word_hit_is_not_enough(self):
        task = self._with_body("one", ONE_WORD_BODY, title="签单那天",
                               goal="写一个发生在东北农村的故事。")
        self.assertEqual(appeal.genre_of(task), "")
        self.assertEqual(appeal.check_task(task, use_model=False)["reason"],
                         "unknown_genre")

    def test_no_workdir_is_empty_not_error(self):
        task = self.task(workdir=str(Path(self._tmp.name) / "missing"))
        self.assertEqual(appeal.genre_of(task), "")
        self.assertEqual(appeal.check_task(task, use_model=False)["reason"],
                         "no_workdir")


class ConsistencyTest(_FixtureChain):
    """核心不变量：每种样态下，判据能不能判 与 约束注不注入 必须同真假。"""

    def test_verdict_and_constraints_agree_on_every_shape(self):
        work = Path(self._tmp.name) / "city2"
        work.mkdir()
        (work / "chapter-01.md").write_text(CITY_BODY, encoding="utf-8")
        shapes = [
            ("ui", self.task()),
            ("wide-category", self.task(genre="都市")),
            ("one-word-goal", self.task(goal="年代打猎文连载。")),
            ("explicit", self.task(appeal_genre="hunt_1980s")),
            ("explicit-unknown", self.task(appeal_genre="悬疑推理")),
            ("city", self.task(workdir=str(work), title="加班夜的天台",
                               goal="都市女频短篇连载。")),
        ]
        for name, task in shapes:
            result = appeal.check_task(task, use_model=False)
            block = constraints.block_for(task)
            self.assertEqual(bool(result.get("enabled")), bool(block),
                             "%s：判据 enabled=%r，约束块 %d 字"
                             % (name, result.get("enabled"), len(block)))

    def test_genre_of_does_not_mutate_task(self):
        task = self.task()
        before = dict(task)
        appeal.genre_of(task)
        self.assertEqual(task, before)


if __name__ == "__main__":
    unittest.main()
