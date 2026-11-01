# -*- coding: utf-8 -*-
"""ReasoningBank 模式库（reasoning_bank）回归。

核心语义（2026-11-01，借鉴 ruvnet/ruflo 的 ReasoningBank，MIT，只借思路）：
1. record：追加入账 + 同源同型同文指纹去重（重复只 count+1）；
2. retrieve：字符二元组 TF-IDF 余弦，类型亲和加权、成功优先、时效衰减，
   全程零模型零额度；
3. 绝不抛错：坏行跳过、检索故障返回空；
4. reindex_memory：项目记忆 vault 已采纳条目幂等搬运。
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from core import paths, reasoning_bank


class BankTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cb-bank-")
        self._p = mock.patch.object(paths, "DATA_DIR", self.tmp)
        self._p.start()
        reasoning_bank.reset_for_tests()

    def tearDown(self):
        self._p.stop()
        reasoning_bank.reset_for_tests()

    def test_record_and_dedup(self):
        r1 = reasoning_bank.record("民俗玄学开篇用白描守村人", task_type="serial_novel",
                                   outcome="success", source="manual")
        r2 = reasoning_bank.record("民俗玄学开篇用白描守村人", task_type="serial_novel",
                                   outcome="success", source="manual")
        self.assertTrue(r1.get("id"))
        self.assertTrue(r2.get("deduped"))
        self.assertEqual(r2["count"], 2)
        stats = reasoning_bank.stats()
        self.assertEqual(stats["rows"], 1)
        self.assertEqual(stats["by_outcome"].get("success"), 1)

    def test_record_bad_outcome_normalized(self):
        r = reasoning_bank.record("任意", outcome="unknown-flag")
        self.assertEqual(r["outcome"], "neutral")

    def test_retrieve_hits_relevant_row(self):
        reasoning_bank.record("民俗玄学小说开篇要白描不要堆设定", task_type="serial_novel",
                              outcome="success", source="manual")
        reasoning_bank.record("标书要先核废标条款再排版", task_type="bid_doc",
                              outcome="success", source="manual")
        hits = reasoning_bank.retrieve("民俗玄学小说怎么开篇")
        self.assertTrue(hits)
        self.assertIn("民俗玄学", hits[0]["text"])

    def test_type_affinity_boosts_typed_row(self):
        reasoning_bank.record("节奏慢就砍支线加钩子", task_type="serial_novel",
                              outcome="success", source="manual")
        reasoning_bank.record("节奏慢就砍支线加钩子", task_type="code",
                              outcome="success", source="manual")
        hits = reasoning_bank.retrieve("节奏慢怎么办", task_type="serial_novel")
        self.assertEqual(hits[0]["task_type"], "serial_novel")

    def test_success_outranks_failure_on_tie(self):
        reasoning_bank.record("同一个任务模式甲", outcome="failure", source="manual",
                              ts=time.time() - 1)
        reasoning_bank.record("同一个任务模式甲", outcome="success", source="manual",
                              ts=time.time())
        hits = reasoning_bank.retrieve("同一个任务模式甲")
        self.assertEqual(hits[0]["outcome"], "success")
        self.assertEqual(hits[1]["outcome"], "failure")

    def test_empty_query_returns_empty(self):
        self.assertEqual(reasoning_bank.retrieve("  "), [])

    def test_corrupt_line_skipped(self):
        reasoning_bank.record("好模式", source="manual")
        f = Path(reasoning_bank._file())
        with f.open("a", encoding="utf-8") as fh:
            fh.write("{broken json!!\n")
        reasoning_bank.reset_for_tests()
        stats = reasoning_bank.stats()
        self.assertEqual(stats["rows"], 1)
        self.assertTrue(reasoning_bank.retrieve("好模式"))

    def test_reindex_memory_idempotent(self):
        # 无任务/无记忆时安全返回零；有 mock 数据时搬运且重复跑不重复入库
        res = reasoning_bank.reindex_memory()
        self.assertEqual(res["added"], 0)
        task = {"id": "t1", "title": "书", "type": "serial_novel",
                "workdir": "W:/fake"}
        entry = {"id": "e1", "title": "经验", "status": "accepted",
                 "content": "开篇三章必须埋钩子", "version": 1}
        with mock.patch("core.memory_vault._tasks_with_memory",
                        return_value=[(task, "W:/fake", [entry])]):
            res1 = reasoning_bank.reindex_memory()
            res2 = reasoning_bank.reindex_memory()
        self.assertEqual(res1["added"], 1)
        self.assertEqual(res2["added"], 0)
        hits = reasoning_bank.retrieve("开篇钩子")
        self.assertTrue(hits)

    def test_prune_keeps_newest_per_type(self):
        for i in range(5):
            reasoning_bank.record("模式%d" % i, task_type="t", source="manual",
                                  ts=time.time() + i)
        removed = reasoning_bank.prune(keep_per_type=2)
        self.assertEqual(removed, 3)
        self.assertEqual(reasoning_bank.stats()["rows"], 2)


if __name__ == "__main__":
    unittest.main()
