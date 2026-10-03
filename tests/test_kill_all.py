# -*- coding: utf-8 -*-
"""急停（kill switch）回归：jobs.cancel_all 只取消 queued/running，
终态运行不动；cancelled_by_user 意图标记先落（自动续跑不得续上）；
running 主路径（CANCELS 事件在场）与 queued 兜底路径都覆盖。"""
from __future__ import annotations

import unittest

from base import BaseTest


class KillAllTest(BaseTest):

    def _seed(self, status):
        from app.core import store
        run = store.create_run("orchestration", "急停-" + status)
        if status != "queued":
            store.update_run(run["id"], status=status)
        return run["id"]

    def test_cancel_all_only_touches_active_runs(self):
        """queued/running 落 cancelled；done/failed/cancelled 原样不动。"""
        from app.core import jobs, store
        rid_q = self._seed("queued")
        rid_r = self._seed("running")
        rid_d = self._seed("done")
        rid_f = self._seed("failed")
        rid_c = self._seed("cancelled")

        n = jobs.cancel_all()

        self.assertEqual(n, 2)
        for rid in (rid_q, rid_r):
            run = store.get_run(rid)
            self.assertEqual(run["status"], "cancelled")
            self.assertTrue(run.get("cancelled_by_user"))
        expected = {rid_d: "done", rid_f: "failed", rid_c: "cancelled"}
        for rid, want in expected.items():
            self.assertEqual(store.get_run(rid)["status"], want)

    def test_cancel_all_running_with_registered_event(self):
        """running 且起跑方已登记取消事件：走事件置位主路径，秒落终态。"""
        from app.core import jobs, store
        rid = self._seed("running")
        ev = jobs.cancel_event_for(rid)   # 模拟 run_process 已在轮询的取消事件

        n = jobs.cancel_all()

        self.assertEqual(n, 1)
        self.assertTrue(ev.is_set())
        self.assertEqual(store.get_run(rid)["status"], "cancelled")
        self.assertTrue(store.get_run(rid).get("cancelled_by_user"))

    def test_cancel_all_empty_returns_zero(self):
        """没有任何运行时返回 0，不抛错。"""
        from app.core import jobs
        self.assertEqual(jobs.cancel_all(), 0)

    def test_cancel_all_idempotent(self):
        """急停后的第二次调用：无进行中任务，返回 0（幂等不重复计数）。"""
        from app.core import jobs
        rid = self._seed("queued")
        self.assertEqual(jobs.cancel_all(), 1)
        self.assertEqual(jobs.cancel_all(), 0)


if __name__ == "__main__":
    unittest.main()
