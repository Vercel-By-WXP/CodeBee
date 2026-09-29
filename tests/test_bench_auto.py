# -*- coding: utf-8 -*-
"""评测台定时回归（evalbench.fire_due）+ 逐题对比 matrix 单测。

覆盖：开关/节流/无候选/在跑守卫、自动候选收集、收尾推送、matrix 形状。

跑法：python -m unittest discover -s tests -p "test_bench_auto.py" -v
"""
from __future__ import annotations

import json
import time
import unittest
from unittest import mock

from base import BaseTest

from app.core import evalbench, settings as settings_mod


def _ts(days_ago=0):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - days_ago * 86400))


def _enable(days=7):
    settings_mod.save({"bench_auto_enabled": True, "bench_auto_days": days})


class TestFireDue(BaseTest):

    def setUp(self):
        super().setUp()
        evalbench._RUN = None
        from app.core import paths
        (paths.DATA_DIR).mkdir(parents=True, exist_ok=True)

    def test_disabled_skips(self):
        settings_mod.save({})
        r = evalbench.fire_due()
        self.assertTrue(r.get("skipped"))
        self.assertEqual(r["reason"], "disabled")

    def test_not_due_skips(self):
        _enable(days=7)
        evalbench._append_run_log("bench-auto", {"provider_id": "j", "model": "m"}, auto=True)
        r = evalbench.fire_due()
        self.assertEqual(r["reason"], "not-due")

    def test_due_after_interval(self):
        _enable(days=7)
        evalbench._write({"runs": [{"ts": _ts(days_ago=8), "auto": True,
                                    "cancelled": False, "run_id": "x"}]})
        with mock.patch.object(evalbench, "_auto_candidates",
                               return_value=[{"provider_id": "p", "model": "m"}]), \
             mock.patch.object(evalbench, "start",
                               return_value=({"run_id": "b", "total": 3}, None)) as mstart:
            r = evalbench.fire_due()
        self.assertTrue(r.get("started"), r)
        mstart.assert_called_once()
        self.assertTrue(mstart.call_args.kwargs.get("notify_done"))

    def test_no_candidates_skips(self):
        _enable()
        with mock.patch.object(evalbench, "_auto_candidates", return_value=[]):
            r = evalbench.fire_due()
        self.assertEqual(r["reason"], "no-candidates")

    def test_running_skips(self):
        _enable()
        evalbench._RUN = {"total": 3, "done": 1, "current": "", "cancel": False,
                          "started_ts": time.time()}
        try:
            with mock.patch.object(evalbench, "_auto_candidates",
                                   return_value=[{"provider_id": "p", "model": "m"}]):
                r = evalbench.fire_due()
            self.assertTrue(r.get("skipped"), r)     # start 的在跑守卫兜底
        finally:
            evalbench._RUN = None

    def test_corrupted_last_ts_treated_due(self):
        _enable()
        evalbench._write({"runs": [{"ts": "garbage", "auto": True, "cancelled": False}]})
        with mock.patch.object(evalbench, "_auto_candidates",
                               return_value=[{"provider_id": "p", "model": "m"}]), \
             mock.patch.object(evalbench, "start",
                               return_value=({"run_id": "b", "total": 1}, None)):
            r = evalbench.fire_due()
        self.assertTrue(r.get("started"), r)


class TestAutoCandidates(BaseTest):

    def test_collects_enabled_models_with_cap(self):
        from app.core import modelhub
        provs = [
            {"id": "p1", "enabled": True, "api_key": "k",
             "models": [{"name": "a", "enabled": True}, {"name": "b", "hidden": True},
                        {"name": "c", "enabled": False}, {"name": "d"}]},
            {"id": "p2", "enabled": False, "api_key": "k",
             "models": [{"name": "z"}]},
            {"id": "p3", "enabled": True, "models": [{"name": "n"}]},   # 无 KEY
        ]
        with mock.patch.object(modelhub, "providers", return_value=provs):
            out = evalbench._auto_candidates()
        self.assertEqual(out, [{"provider_id": "p1", "model": "a"},
                               {"provider_id": "p1", "model": "d"}])


class TestNotifyAndMatrix(BaseTest):

    def _fake_gen(self, judge_json):
        def fake(prov_id, model, prompt, max_tokens=2048):
            if "评审员" in prompt:
                return {"ok": True, "text": json.dumps(judge_json), "usage": None,
                        "error": "", "latency_ms": 5}
            return {"ok": True, "text": "正文。", "usage": {"input": 1, "output": 2},
                    "error": "", "latency_ms": 6}
        return fake

    def test_auto_done_pushes_summary(self):
        from app.core import notify
        with mock.patch.object(evalbench, "_gen", side_effect=self._fake_gen(
                {"dims": {"情节": 8}, "overall": 8.8, "comment": ""})), \
             mock.patch.object(notify, "push_text", return_value=True) as mpush:
            evalbench._RUN = {"total": 1, "done": 0, "current": "", "cancel": False,
                              "started_ts": time.time()}
            evalbench._run_bench("bench-auto", [{"provider_id": "p", "model": "m"}],
                                 ["writing"], {"provider_id": "j", "model": "jm"},
                                 notify_done=True)
            self.assertTrue(mpush.called)
            text = mpush.call_args.args[0]
            self.assertIn("定时回归", text)
            self.assertIn("8.8", text)
            # 再跑一轮：首轮 m 以 8.8 居首（top1 已钉 m）；本轮替身让 m2 考 9.9、
            # m 考 5.0（候选作答按 model 分化，裁判按作答判分）→ 榜首变 m2，点名易主
            mpush.reset_mock()
            def drifting(prov_id, model, prompt, max_tokens=2048):
                if "评审员" in prompt:
                    score = 9.9 if "答二" in prompt else 5.0
                    return {"ok": True, "text": json.dumps(
                        {"dims": {"情节": score}, "overall": score, "comment": ""}),
                        "usage": None, "error": "", "latency_ms": 5}
                return {"ok": True, "text": "答二" if model == "m2" else "答一",
                        "usage": {"input": 1, "output": 2}, "error": "", "latency_ms": 6}
            with mock.patch.object(evalbench, "_gen", side_effect=drifting):
                evalbench._RUN = {"total": 2, "done": 0, "current": "", "cancel": False,
                                  "started_ts": time.time()}
                evalbench._run_bench("bench-auto2",
                                     [{"provider_id": "p2", "model": "m2"},
                                      {"provider_id": "p", "model": "m"}],
                                     ["writing"], {"provider_id": "j", "model": "jm"},
                                     notify_done=True)
            self.assertTrue(mpush.called)
            text2 = mpush.call_args.args[0]
            self.assertIn("榜首易主", text2)
            self.assertIn("→ 本次 m2（", text2)   # 新榜首 m2

    def test_no_push_when_cancelled(self):
        from app.core import notify
        evalbench._RUN = {"total": 1, "done": 0, "current": "", "cancel": True,
                          "started_ts": time.time()}
        try:
            with mock.patch.object(notify, "push_text", return_value=True) as mpush:
                evalbench._run_bench("bench-auto", [{"provider_id": "p", "model": "m"}],
                                     ["writing"], {"provider_id": "j", "model": "jm"},
                                     notify_done=True)
            self.assertFalse(mpush.called)
        finally:
            evalbench._RUN = None

    def test_matrix_latest_per_cell(self):
        evalbench._persist_result({"ts": "2026-09-27 10:00:00", "sample_id": "writing",
                                   "provider_id": "p", "model": "m", "ok": True,
                                   "scored": True, "verify_ok": None, "error": "",
                                   "overall": 7.0, "scores": {}, "comment": "",
                                   "cost_usd": 0.0, "duration_s": 1.0, "same_family": False})
        evalbench._persist_result({"ts": "2026-09-27 11:00:00", "sample_id": "writing",
                                   "provider_id": "p", "model": "m", "ok": True,
                                   "scored": True, "verify_ok": None, "error": "",
                                   "overall": 9.0, "scores": {}, "comment": "",
                                   "cost_usd": 0.0, "duration_s": 1.0, "same_family": False})
        evalbench._persist_result({"ts": "2026-09-27 11:00:00", "sample_id": "bugfix",
                                   "provider_id": "p", "model": "m", "ok": False,
                                   "scored": False, "verify_ok": None, "error": "x",
                                   "overall": None, "scores": {}, "comment": "",
                                   "cost_usd": 0.0, "duration_s": 1.0, "same_family": False})
        mx = evalbench._matrix(evalbench._read_results_list())
        self.assertEqual(mx["samples"], ["writing", "bugfix", "summary", "redteam"])
        cell = mx["cells"]["writing"]["p|m"]
        self.assertEqual(cell["overall"], 9.0)           # 同键最新胜
        self.assertFalse(mx["cells"]["bugfix"]["p|m"]["ok"])
        self.assertNotIn("summary", mx["cells"]["writing"])


if __name__ == "__main__":
    unittest.main()
