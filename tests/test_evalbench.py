# -*- coding: utf-8 -*-
"""评测基准台（evalbench）单测：裁判 JSON 解析、修 bug 题客观验证（真跑子进程）、
整轮编排（mock 生成/裁判）、榜单聚合、start 的守卫与线程派发。

跑法：python -m unittest discover -s tests -p "test_evalbench.py" -v
"""
from __future__ import annotations

import json
import time
import unittest
from unittest import mock

from base import BaseTest

from app.core import evalbench


GOOD_SOLUTION = '''```python
def sliding_window_max(nums, k):
    if k <= 0 or k > len(nums):
        return []
    out = []
    for i in range(len(nums) - k + 1):
        out.append(max(nums[i:i + k]))
    return out
```
把窗口有效性判断提前返回。'''


def _fake_gen_factory(judge_json=None, judge_ok=True, judge_text=""):
    """按 prompt 内容分流的假 generate：裁判调用返回 JSON，修 bug 题返回好代码。"""
    def fake(prov_id, model, prompt, max_tokens=2048):
        if "评审员" in prompt:
            if not judge_ok:
                return {"ok": False, "text": "", "usage": None, "error": "judge down",
                        "latency_ms": 5}
            text = judge_text or json.dumps(judge_json or {
                "dims": {"情节": 8, "人物": 7.5, "文笔": 8, "吸引力": 7,
                         "正确性": 9, "代码质量": 8, "解释清晰": 7,
                         "准确性": 8, "结构": 8, "简洁": 7},
                "overall": 7.8, "comment": "稳"})
            return {"ok": True, "text": text, "usage": {"input": 100, "output": 50},
                    "error": "", "latency_ms": 30}
        if "sliding_window_max" in prompt:
            return {"ok": True, "text": GOOD_SOLUTION,
                    "usage": {"input": 80, "output": 120}, "error": "", "latency_ms": 40}
        return {"ok": True, "text": "这是候选作答正文。",
                "usage": {"input": 60, "output": 200}, "error": "", "latency_ms": 70}
    return fake


class EvalBenchBase(BaseTest):

    def setUp(self):
        super().setUp()
        evalbench._RUN = None


class TestJudgeParsing(EvalBenchBase):

    def test_plain_and_fenced_json(self):
        good = {"dims": {"情节": 8, "文笔": 9}, "overall": 8.5, "comment": "好"}
        for raw in (json.dumps(good, ensure_ascii=False),
                    "评审如下：\n```json\n" + json.dumps(good, ensure_ascii=False) + "\n```\n完毕"):
            r = evalbench._parse_judge_json(raw)
            self.assertIsNotNone(r)
            self.assertEqual(r["overall"], 8.5)
            self.assertEqual(r["dims"]["情节"], 8)

    def test_overall_missing_uses_dim_mean(self):
        r = evalbench._parse_judge_json('{"dims": {"a": 6, "b": 8}}')
        self.assertAlmostEqual(r["overall"], 7.0)

    def test_garbage_returns_none(self):
        for bad in ("", "没有 JSON", '{"dims": {}}', '{"overall": 9}',
                    "前缀 { 截断的 JSON"):
            self.assertIsNone(evalbench._parse_judge_json(bad), bad)

    def test_scores_clamped(self):
        r = evalbench._parse_judge_json('{"dims": {"a": 42, "b": -3}, "overall": 99}')
        self.assertEqual(r["dims"]["a"], 10.0)
        self.assertEqual(r["dims"]["b"], 0.0)
        self.assertEqual(r["overall"], 10.0)


class TestBugfixVerify(EvalBenchBase):

    def test_correct_solution_passes(self):
        ok, detail = evalbench._verify_bugfix(GOOD_SOLUTION)
        self.assertTrue(ok, detail)

    def test_buggy_solution_fails(self):
        buggy = "```python\ndef sliding_window_max(nums, k):\n" \
                "    return [max(nums[i:i + k]) for i in range(len(nums) - k + 1)]\n```"
        ok, detail = evalbench._verify_bugfix(buggy)
        self.assertFalse(ok)

    def test_missing_function_fails(self):
        ok, detail = evalbench._verify_bugfix("我不会写代码。")
        self.assertFalse(ok)
        self.assertIn("sliding_window_max", detail)


class TestRunBench(EvalBenchBase):

    def _board(self):
        return {r["model"]: r for r in evalbench._leaderboard(
            evalbench._read().get("results") or [], {})}

    def test_full_round_two_candidates(self):
        cands = [{"provider_id": "prov-a", "model": "model-a"},
                 {"provider_id": "prov-b", "model": "model-b"}]
        with mock.patch.object(evalbench, "_gen",
                               side_effect=_fake_gen_factory()):
            evalbench._run_bench("bench-test", cands, ["writing", "bugfix"],
                                 {"provider_id": "prov-j", "model": "judge-x"})
        rows = evalbench._read().get("results")
        self.assertEqual(len(rows), 4)                     # 2 候选 × 2 样题
        self.assertTrue(all(r["ok"] and r["scored"] for r in rows))
        self.assertTrue(all(r["verify_ok"] for r in rows if r["sample_id"] == "bugfix"))
        board = self._board()
        self.assertEqual(set(board), {"model-a", "model-b"})
        self.assertEqual(board["model-a"]["overall"], 7.8)
        self.assertEqual(board["model-a"]["verify_pass"], 1)
        self.assertFalse(board["model-a"]["same_family"])
        # 台账：4 样题生成 + 4 裁判 = 8 条 bench 记录
        from app.core import usage
        bench_rows = [r for r in usage._iter_records(1) if r.get("source") == "bench"]
        self.assertEqual(len(bench_rows), 8)

    def test_same_family_flagged(self):
        with mock.patch.object(evalbench, "_gen",
                               side_effect=_fake_gen_factory()):
            evalbench._run_bench("bench-test", [{"provider_id": "prov-j", "model": "model-a"}],
                                 ["writing"], {"provider_id": "prov-j", "model": "judge-x"})
        row = evalbench._read()["results"][0]
        self.assertTrue(row["same_family"])
        board = self._board()["model-a"]
        self.assertTrue(board["same_family"])

    def test_gen_failure_recorded_not_scored(self):
        def fake(prov_id, model, prompt, max_tokens=2048):
            if "评审员" in prompt:
                self.fail("生成失败后不应再调裁判")
            return {"ok": False, "text": "", "usage": None, "error": "HTTP 503",
                    "latency_ms": 10}
        with mock.patch.object(evalbench, "_gen", side_effect=fake):
            evalbench._run_bench("bench-test", [{"provider_id": "p", "model": "m"}],
                                 ["writing"], {"provider_id": "j", "model": "jm"})
        row = evalbench._read()["results"][0]
        self.assertFalse(row["ok"])
        self.assertIn("503", row["error"])
        self.assertIsNone(row["overall"])

    def test_judge_unparseable_marks_unscored(self):
        with mock.patch.object(evalbench, "_gen", side_effect=_fake_gen_factory(
                judge_ok=True, judge_text="我只能说答得不错。")):
            evalbench._run_bench("bench-test", [{"provider_id": "p", "model": "m"}],
                                 ["writing"], {"provider_id": "j", "model": "jm"})
        row = evalbench._read()["results"][0]
        self.assertTrue(row["ok"])
        self.assertFalse(row["scored"])
        self.assertIsNone(self._board()["m"]["overall"])

    def test_progress_counts_failures_too(self):
        evalbench._RUN = {"total": 2, "done": 0, "current": "", "cancel": False,
                          "started_ts": time.time()}
        def fake(prov_id, model, prompt, max_tokens=2048):
            return {"ok": False, "text": "", "usage": None, "error": "x", "latency_ms": 1}
        with mock.patch.object(evalbench, "_gen", side_effect=fake):
            evalbench._run_bench("bench-test", [{"provider_id": "p", "model": "m"}],
                                 ["writing", "summary"], {"provider_id": "j", "model": "jm"})
        self.assertIsNone(evalbench._RUN)   # 跑完复位；done 已计入失败行


class TestStartGuards(EvalBenchBase):

    def test_no_candidates_rejected(self):
        info, err = evalbench.start([])
        self.assertIsNone(info)
        self.assertIn("候选", err)

    def test_judge_not_ready_rejected(self):
        from app.core import modelhub
        with mock.patch.object(modelhub, "orchestrator_view",
                               return_value={"ready": False}):
            info, err = evalbench.start([{"provider_id": "p", "model": "m"}])
        self.assertIsNone(info)
        self.assertIn("编排者", err)

    def test_running_guard_and_dispatch(self):
        import threading
        from app.core import modelhub
        spawned = []
        release = threading.Event()

        def fake_worker(*a, **kw):
            release.wait(timeout=5)            # 按住替身，护栏断言才有确定性
            spawned.append(a)
            evalbench._RUN = None              # 真 _run_bench 跑完复位；mock 替身自己复刻

        with mock.patch.object(modelhub, "orchestrator_view",
                               return_value={"ready": True, "provider_id": "jp",
                                             "model": "jm"}), \
             mock.patch.object(evalbench, "_run_bench", side_effect=fake_worker):
            info, err = evalbench.start(
                [{"provider_id": "p", "model": "m"}], sample_ids=["writing"])
            self.assertIsNone(err)
            self.assertEqual(info["total"], 1)
            info2, err2 = evalbench.start([{"provider_id": "p", "model": "m"}])
            self.assertIsNone(info2)
            self.assertIn("在跑", err2)
            release.set()
        for _ in range(200):                        # 等派发线程执行完
            if spawned and evalbench._RUN is None:
                break
            time.sleep(0.02)
        self.assertTrue(spawned)
        self.assertIsNone(evalbench._RUN)


if __name__ == "__main__":
    unittest.main()
