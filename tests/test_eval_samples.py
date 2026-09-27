# -*- coding: utf-8 -*-
"""评测台自定义样题（增删/校验/分享码/跑评测接线）单测。

跑法：python -m unittest discover -s tests -p "test_eval_samples.py" -v
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from base import BaseTest

from app.core import evalbench


def _sample(**kw):
    s = {"name": "我的题材题", "dims": ["情节", "文笔"],
         "requirement": "按给定题材写 200 字", "prompt": "写一段 200 字的赛博朋克开场。"}
    s.update(kw)
    return s


class TestSampleOp(BaseTest):

    def test_add_and_validate(self):
        s, err = evalbench.sample_op("add", _sample())
        self.assertIsNone(err, err)
        self.assertTrue(s["id"].startswith("c-"))
        self.assertEqual(len(evalbench.custom_samples()), 1)
        # 校验拒绝：太短 / 维度数量 / 名称空
        for bad in (_sample(prompt="太短"), _sample(dims=["只一个维度"]),
                    _sample(name="")):
            _, err = evalbench.sample_op("add", bad)
            self.assertTrue(err)
        # 超上限
        for i in range(evalbench.CUSTOM_MAX - 1):
            evalbench.sample_op("add", _sample(name="题%d" % i))
        _, err = evalbench.sample_op("add", _sample(name="超限"))
        self.assertIn("最多", err)

    def test_delete(self):
        s, err = evalbench.sample_op("add", _sample())
        self.assertIsNone(err, err)
        res, err = evalbench.sample_op("delete", sid=s["id"])
        self.assertIsNone(err, err)
        self.assertEqual(evalbench.custom_samples(), [])

    def test_samples_merge_builtin_first(self):
        evalbench.sample_op("add", _sample())
        ids = [s["id"] for s in evalbench.samples()]
        self.assertEqual(ids[:3], ["writing", "bugfix", "summary"])
        self.assertTrue(evalbench.samples()[0]["builtin"])
        self.assertFalse(evalbench.samples()[-1]["builtin"])


class TestSampleShare(BaseTest):

    def test_export_import_roundtrip(self):
        s, _ = evalbench.sample_op("add", _sample())
        code, err = evalbench.export_sample_code(s["id"])
        self.assertIsNone(err, err)
        self.assertTrue(code.startswith("CBSAMP1."))
        evalbench.sample_op("delete", sid=s["id"])
        res, err = evalbench.import_sample_code(code)
        self.assertIsNone(err, err)
        self.assertEqual(res["name"], "我的题材题")
        self.assertEqual(len(evalbench.custom_samples()), 1)

    def test_import_id_conflict_auto_renames(self):
        s, _ = evalbench.sample_op("add", _sample())
        code, _ = evalbench.export_sample_code(s["id"])
        res, err = evalbench.import_sample_code(code)   # 同 ID 再导 → 换号
        self.assertIsNone(err, err)
        self.assertNotEqual(res["id"], s["id"])
        self.assertEqual(len(evalbench.custom_samples()), 2)

    def test_builtin_export_rejected(self):
        _, err = evalbench.export_sample_code("writing")
        self.assertIn("内置", err)

    def test_bad_code(self):
        res, err = evalbench.import_sample_code("CBFLOW1.x")
        self.assertIsNone(res)
        self.assertIn("样题", err)


class TestBenchUsesCustom(BaseTest):

    def test_run_bench_covers_custom_sample(self):
        s, _ = evalbench.sample_op("add", _sample())
        judge = {"dims": {"情节": 8, "文笔": 8}, "overall": 8.0, "comment": ""}

        def fake(prov_id, model, prompt, max_tokens=2048):
            if "评审员" in prompt:
                return {"ok": True, "text": json.dumps(judge), "usage": None,
                        "error": "", "latency_ms": 5}
            assert "赛博朋克" in prompt                    # 自定义题面真的发出去了
            return {"ok": True, "text": "正文。", "usage": None, "error": "",
                    "latency_ms": 5}

        with mock.patch.object(evalbench, "_gen", side_effect=fake):
            evalbench._RUN = {"total": 1, "done": 0, "current": "", "cancel": False,
                              "started_ts": 0}
            evalbench._run_bench("bench-t", [{"provider_id": "p", "model": "m"}],
                                 [s["id"]], {"provider_id": "j", "model": "jm"})
        rows = evalbench._read_results_list()
        self.assertEqual(rows[0]["sample_id"], s["id"])
        self.assertEqual(rows[0]["overall"], 8.0)


if __name__ == "__main__":
    unittest.main()
