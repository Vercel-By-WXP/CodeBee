# -*- coding: utf-8 -*-
"""「评审未达标」如实死因回归（2026-10-26 头七实案）。

实案：修订链五连灭（litellm 10061 / codex 断网 / claude 502），稿件一字
未动，第 6 个尝试的评审给了 5.1 分，任务以「评审未达标，需回炉修订」收场
——把基础设施故障伪装成稿件质量问题，用户照着改稿纯属白费。

契约：本次运行修订从未生效（history_rounds 非空且零轮真实改动稿件）时，
终判错误必须带如实补注；任一轮修订真实生效则绝不补注。
"""
import unittest

from app.core.pipeline import _revision_starved_note


class RevisionStarvedNoteTests(unittest.TestCase):
    def test_all_rounds_starved_gets_honest_note(self):
        rounds = [{"round": 1, "means": {"情节因果": 3.0}}, {"round": 2, "means": {}}]
        note = _revision_starved_note(rounds, effective_revisions=0)
        self.assertIn("修订从未生效", note)
        self.assertIn("未修订稿件", note)
        self.assertIn("供应商健康面板", note)

    def test_single_round_no_revise_also_counts(self):
        # rounds=1 且终审即判死（r>=rounds 收口、修订未执行）——修订同样
        # 从未发生，补注必须出现
        note = _revision_starved_note([{"round": 1, "means": {}}], 0)
        self.assertIn("修订从未生效", note)

    def test_effective_revision_suppresses_note(self):
        rounds = [{"round": 1}, {"round": 2}]
        self.assertEqual(_revision_starved_note(rounds, 1), "")
        self.assertEqual(_revision_starved_note(rounds, 2), "")

    def test_no_rounds_no_note(self):
        self.assertEqual(_revision_starved_note([], 0), "")


if __name__ == "__main__":
    unittest.main()
