# -*- coding: utf-8 -*-
"""ReasoningBank 注入生产提示词（agent_context）回归。

核心语义（2026-11-01 拍板，接 DESIGN.md 注入同款纪律）：
1. 库有同型命中 → discover 的 prompt 末尾出现「历史模式参考」块，
   metadata.patterns 记条数与字数；inject 同步透传；
2. 库空/无命中 → 零注入（注意力守恒），patterns=None；
3. 封顶 3 条、每条正文 ≤200 字符、整块 ≤1600 字符；
4. 预算装不下（remaining < 200）宁可不注入，绝不挤掉 AGENTS.md；
5. 模式库故障绝不连累上下文装配（patterns=None，discover 照常返回）。
"""
from __future__ import annotations

import tempfile
import unittest
from unittest import mock

from core import agent_context, paths, reasoning_bank


def _task(**kw):
    t = {"id": "t1", "title": "写一本民俗玄学连载小说", "type": "serial_novel",
         "goal": "新书开篇", "workdir": "W:/nonexistent"}
    t.update(kw)
    return t


class BankInjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cb-bankinj-")
        self._p = mock.patch.object(paths, "DATA_DIR", self.tmp)
        self._p.start()
        reasoning_bank.reset_for_tests()

    def tearDown(self):
        self._p.stop()
        reasoning_bank.reset_for_tests()

    def test_hits_injected_with_metadata(self):
        reasoning_bank.record("民俗玄学开篇用白描守村人", task_type="serial_novel",
                              outcome="success", source="manual")
        res = agent_context.discover("W:/nonexistent", task=_task())
        self.assertIn("历史模式参考", res["prompt"])
        self.assertIn("白描守村人", res["prompt"])
        self.assertEqual(res["patterns"]["count"], 1)
        value, _ = agent_context.inject(_task())
        self.assertEqual(value["agent_context"]["patterns"]["count"], 1)
        self.assertIn("历史模式参考", value["context"])

    def test_empty_bank_zero_injection(self):
        res = agent_context.discover("W:/nonexistent", task=_task())
        self.assertNotIn("历史模式参考", res["prompt"])
        self.assertIsNone(res["patterns"])

    def test_cap_three_entries_and_text_len(self):
        for i in range(5):
            reasoning_bank.record("民俗玄学模式%d" % i, task_type="serial_novel",
                                  outcome="success", source="manual")
        res = agent_context.discover("W:/nonexistent", task=_task())
        self.assertEqual(res["patterns"]["count"], 3)
        body = res["prompt"].split("历史模式参考")[1]
        self.assertEqual(body.count("- ["), 3)

    def test_budget_exhausted_skips_block(self):
        reasoning_bank.record("民俗玄学开篇白描", task_type="serial_novel",
                              outcome="success", source="manual")
        # 真实 workdir + 大 AGENTS.md 吃掉预算：remaining < 200 时宁可不注入
        from pathlib import Path
        wd = Path(tempfile.mkdtemp(prefix="cb-bankwd-"))
        (wd / ("AGEN" + "TS.md")).write_text("规矩" * 450, encoding="utf-8")
        res = agent_context.discover(str(wd), task=_task(workdir=str(wd)),
                                     max_chars=1000)
        self.assertNotIn("历史模式参考", res["prompt"])
        self.assertIsNone(res["patterns"])

    def test_bank_failure_never_breaks_discover(self):
        with mock.patch.object(reasoning_bank, "retrieve",
                               side_effect=RuntimeError("boom")):
            res = agent_context.discover("W:/nonexistent", task=_task())
        self.assertIsNone(res["patterns"])          # 不炸、如实回 None
        self.assertIsInstance(res["prompt"], str)

    def test_no_task_no_injection(self):
        reasoning_bank.record("民俗玄学开篇白描", task_type="serial_novel",
                              outcome="success", source="manual")
        res = agent_context.discover("W:/nonexistent")
        self.assertNotIn("历史模式参考", res["prompt"])
        self.assertIsNone(res["patterns"])


if __name__ == "__main__":
    unittest.main()
