# -*- coding: utf-8 -*-
"""扫榜选材「方向可留空」承诺守护：菜单文案（flows.py goal_hint）承诺留空默认
分析总榜热门题材，paihang/pipeline 两侧均支持空 goal——创建入口（store/UI）
必须放行，否则承诺与实际不符（2026-10-10 G 专项巡检实案）。"""
from __future__ import annotations

from base import BaseTest


class RankScanEmptyGoalTests(BaseTest):
    def runTest(self):
        from app.core import store

        payload = {"type": "rank_scan", "workdir": str(self.workdir)}
        task = store.create_task(payload)
        self.assertEqual(task["goal"], "")                      # 空 goal 放行
        self.assertEqual(task["title"], "扫榜选材")              # 标题兜底流程名
        self.assertEqual(task["status"], "created")

        task2 = store.create_task(dict(payload, title="都市榜观察"))
        self.assertEqual(task2["title"], "都市榜观察")            # 显式标题优先

        # 其余类型目标必填不变（防回归：放行面只限 rank_scan）
        with self.assertRaises(ValueError) as cm:
            store.create_task({"type": "novel", "workdir": str(self.workdir)})
        self.assertIn("目标描述不能为空", str(cm.exception))
