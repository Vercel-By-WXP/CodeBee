# -*- coding: utf-8 -*-
"""驳回约束（P0-C）守测：把平台驳回变成下次开工的硬约束，且不许变成噪音。

守五件事：

1. **有约束就要能看到**：同题材任务的提示词块里能 grep 到平台原话与 ≥3 条约束。
2. **改文件就跟着变**：约束是数据不是代码，改 JSON 后块内容必须随之变化。
3. **没有就不注入**：无约束文件 / 题材认不出 / 不是小说 —— 一律返回空串，
   绝不出现「（无）」式占位（这是 P0-C 的验收原文）。
4. **平台事实与人工判断分栏**（决策 11）：平台原话原样引用，判断条目必须标明
   「不是平台规则」，否则作者的自省会被下一本书当成平台禁令。
5. **台账坏了只损失这一块**：坏 JSON 不能让提示词拼装抛错。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.quality import constraints


def _profile(**kw):
    base = {
        "genre": "hunt_1980s",
        "genre_name": "年代打猎文",
        "detect": ["打猎", "狩猎", "猎手", "林场", "进山", "野猪"],
        "source": {
            "work": "一头猎物，三笔账",
            "platform": "zongheng",
            "platform_label": "纵横中文网",
            "stage": "signing_rejected",
            "stage_label": "签约评估未通过",
            "at": "2026-10-21",
            "quote": "综合质量一般、整体内容比较普通且缺乏吸引力。",
            "evidence": "前五章 10,465 字",
        },
        "constraints": [
            {"id": "a", "kind": "类型承诺",
             "rule": "每章至少落地一次「获得」，章内兑现。", "fact": "第 1、2 章零获得。"},
            {"id": "b", "kind": "开篇节奏",
             "rule": "第 1 章 500 字内见猎或见血。", "fact": "原稿第 3 章才首猎。"},
            {"id": "c", "kind": "章节篇幅",
             "rule": "每章不少于 2000 字。", "fact": "第 4 章 1406 字。"},
        ],
    }
    base.update(kw)
    return base


class _DirMixin(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._patch = mock.patch.object(constraints, "_dir", return_value=self.root)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def write(self, data, name=None):
        path = self.root / (name or ("constraints-%s.json" % data["genre"]))
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path


class ConstraintBlockTest(_DirMixin):
    def test_block_carries_quote_and_all_constraints(self):
        self.write(_profile())
        text = constraints.block_for({"type": "novel", "appeal_genre": "hunt_1980s"})
        self.assertIn("综合质量一般", text)
        self.assertIn("纵横中文网", text)
        self.assertIn("每章至少落地一次", text)
        self.assertIn("500 字内见猎", text)
        self.assertIn("不少于 2000 字", text)
        self.assertGreaterEqual(text.count("\n"), 4)

    def test_block_separates_platform_fact_from_human_judgement(self):
        # 决策 11：平台原话是事实，约束是判断。混成一句会让下一本书把作者的
        # 自省当成平台规则，越写越窄。
        self.write(_profile())
        text = constraints.block_for({"type": "novel", "genre": "hunt_1980s"})
        self.assertIn("平台原话", text)
        self.assertIn("不是", text)          # 块头明示「不是平台规则」
        self.assertIn("平台规则", text)
        self.assertIn("依据：", text)        # 每条约束都带证据

    def test_editing_the_file_changes_the_block(self):
        path = self.write(_profile())
        before = constraints.block_for({"type": "novel", "appeal_genre": "hunt_1980s"})
        data = json.loads(path.read_text(encoding="utf-8"))
        data["constraints"][0]["rule"] = "第 1 章必须让猎物上桌。"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        after = constraints.block_for({"type": "novel", "appeal_genre": "hunt_1980s"})
        self.assertIn("每章至少落地一次", before)
        self.assertNotIn("每章至少落地一次", after)
        self.assertIn("猎物上桌", after)

    def test_no_file_means_no_block(self):
        # 不许出现「（无）」式占位。
        self.assertEqual(constraints.block_for({"type": "novel"}), "")
        self.assertIsNone(constraints.resolve({"type": "novel"}))

    def test_unknown_genre_is_not_applied(self):
        self.write(_profile())
        task = {"type": "novel", "title": "碎镜开瞳",
                "goal": "江野在旧货市场捡到一面民国铜镜，瞳孔里开始出现别人的死法。"}
        self.assertEqual(constraints.block_for(task), "")

    def test_detects_genre_from_task_description(self):
        self.write(_profile())
        task = {"type": "serial_novel",
                "goal": "1989 年东北林场，重生猎手要进山打一头野猪换全家的药和煤。"}
        text = constraints.block_for(task)
        self.assertIn("每章至少落地一次", text)

    def test_single_detect_word_is_not_enough(self):
        self.write(_profile())
        # 一份鉴宝文的简介里也可能出现一次「林场」。
        task = {"type": "novel", "goal": "他在林场边上的旧货摊淘到一只瓷碗。"}
        self.assertEqual(constraints.block_for(task), "")

    def test_non_novel_task_is_not_injected(self):
        self.write(_profile())
        task = {"type": "doc", "goal": "打猎 狩猎 林场 进山 野猪 猎手"}
        self.assertEqual(constraints.block_for(task), "")

    def test_broken_file_does_not_raise(self):
        (self.root / "constraints-bad.json").write_text("{不是 JSON", encoding="utf-8")
        self.assertEqual(constraints.block_for({"type": "novel", "genre": "bad"}), "")
        self.assertEqual(constraints.all_profiles(), [])

    def test_block_is_bounded(self):
        data = _profile()
        data["constraints"] = [
            {"id": "x%d" % i, "kind": "类型承诺", "rule": "长" * 300, "fact": "证据" * 60}
            for i in range(20)]
        self.write(data)
        text = constraints.block_for({"type": "novel", "genre": "hunt_1980s"})
        self.assertLessEqual(len(text), constraints.MAX_BLOCK_CHARS + 20)
        self.assertLessEqual(text.count("\n   依据："), constraints.MAX_CONSTRAINTS)


class SkillsInjectionTest(_DirMixin):
    def test_skills_block_includes_constraints(self):
        from core import skills
        self.write(_profile())
        text, _used = skills.block_for({"type": "novel", "appeal_genre": "hunt_1980s"})
        self.assertIn("驳回约束", text)
        self.assertIn("综合质量一般", text)

    def test_skills_block_stays_clean_without_constraints(self):
        from core import skills
        text, _used = skills.block_for({"type": "novel", "appeal_genre": "hunt_1980s"})
        self.assertNotIn("驳回约束", text)


if __name__ == "__main__":
    unittest.main()
