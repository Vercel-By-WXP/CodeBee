# -*- coding: utf-8 -*-
"""内存守卫触发证据落盘回归（2026-10-14 CLI 裸跑服务凌晨死亡实案）。

实案：61172 服务凌晨死亡、三本书停摆一晚——守卫触发只 print（无控制台
模式丢弃），死因零证据可查。锁定两个行为：
1. `_record_trigger`：写 JSONL 到数据目录 memory-guard.jsonl（时间/pid/
   RSS/阈值），append 语义不清旧账；
2. 落盘失败静默：数据目录不可写也不得抛——守卫关键路径不能被日志拖死。
"""
from __future__ import annotations

import json
import os

from base import BaseTest


class TestMemoryGuardRecord(BaseTest):
    def test_record_trigger_writes_jsonl(self):
        from app.core import memory_guard as MG, paths
        MG._threshold_mb = 1228
        MG._record_trigger(1500.52)
        MG._record_trigger(1600.0)          # append 语义：第二条不清第一条
        p = paths.DATA_DIR / "memory-guard.jsonl"
        self.assertTrue(p.exists())
        rows = [json.loads(x) for x in
                p.read_text(encoding="utf-8").strip().splitlines() if x]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["pid"], os.getpid())
        self.assertEqual(rows[0]["threshold_mb"], 1228)
        self.assertAlmostEqual(rows[0]["rss_mb"], 1500.5)
        self.assertAlmostEqual(rows[1]["rss_mb"], 1600.0)
        self.assertTrue(rows[0]["at"])

    def test_record_trigger_silent_on_bad_dir(self):
        from app.core import memory_guard as MG, paths
        # 数据目录指向非法长路径 → makedirs/open 必炸 → 必须被吞掉不外抛
        orig = paths.DATA_DIR
        paths.DATA_DIR = orig / ("x" * 300)
        try:
            MG._record_trigger(1234.0)      # 不应抛
        finally:
            paths.DATA_DIR = orig
