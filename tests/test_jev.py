# -*- coding: utf-8 -*-
"""Jev 评审门禁第二意见（P2-2 PoC）单测。

覆盖：mock 确定性评分、HTTP 返回解析（含嵌套）、开关语义、审计落盘与哈希链可验证、
审计不含原文。全部走临时数据目录，不读不写用户真实审计账。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import audit, paths
import app.core.jev as jev


class JevBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_data = paths.DATA_DIR
        paths.DATA_DIR = Path(self.tmp.name)
        self._saved = {k: os.environ.pop(k, None)
                       for k in ("JEV_ENABLED", "JEV_ENDPOINT", "JEV_API_KEY")}

    def tearDown(self):
        paths.DATA_DIR = self.old_data
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
        self.tmp.cleanup()


class MockAssess(JevBase):
    def test_high_score_more(self):
        op = jev._mock_assess({"avg_score": 9, "round": 1, "max_rounds": 3})
        self.assertEqual(op["choice"], "more")
        self.assertEqual(op["source"], "mock")

    def test_mid_score_pass(self):
        op = jev._mock_assess({"avg_score": 7})
        self.assertEqual(op["choice"], "pass")

    def test_low_score_rewrite(self):
        op = jev._mock_assess({"avg_score": 2})
        self.assertEqual(op["choice"], "rewrite")

    def test_parse_top_level(self):
        d = jev._parse({"choice": "more", "score": 9, "noul": False})
        self.assertEqual(d["choice"], "more")
        self.assertEqual(d["score"], 9)

    def test_parse_nested(self):
        d = jev._parse({"data": {"choice": "pass", "score": 6}})
        self.assertEqual(d["choice"], "pass")

    def test_parse_bad_choice_falls_back(self):
        self.assertEqual(jev._parse({"score": 9})["choice"], "pass")
        self.assertEqual(jev._parse({"score": 2})["choice"], "rewrite")

    def test_score_clamped(self):
        self.assertEqual(jev._parse({"choice": "pass", "score": 99})["score"], 10)
        self.assertEqual(jev._parse({"choice": "pass", "score": -5})["score"], 2)


class SwitchSemantics(JevBase):
    def test_disabled_by_default(self):
        self.assertFalse(jev.is_enabled())

    def test_enabled_flag(self):
        os.environ["JEV_ENABLED"] = "1"
        self.assertTrue(jev.is_enabled())

    def test_disabled_returns_none_no_audit(self):
        self.assertIsNone(jev.note_review_gate({"round": 1, "gate_decision": "pass"}))
        self.assertFalse((Path(paths.DATA_DIR) / "audit").exists())


class AuditTrail(JevBase):
    def test_enabled_mock_writes_audit(self):
        os.environ["JEV_ENABLED"] = "1"
        op = jev.note_review_gate({
            "round": 2, "max_rounds": 3, "task_type": "doc",
            "avg_score": 8.0, "seat_count": 3, "issue_count": 1,
            "gate_decision": "more",
        })
        self.assertIsNotNone(op)
        self.assertEqual(op["source"], "mock")
        files = list((Path(paths.DATA_DIR) / "audit").glob("jev_review_gate-*.jsonl"))
        self.assertEqual(len(files), 1)
        lines = files[0].read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec["event"], "jev_review_gate")
        self.assertEqual(rec["jev_choice"], "more")
        self.assertEqual(rec["gate_decision"], "more")
        # 哈希链可验证
        self.assertTrue(audit.verify("jev_review_gate")["ok"])

    def test_audit_no_raw_text(self):
        os.environ["JEV_ENABLED"] = "1"
        jev.note_review_gate({
            "round": 1, "gate_decision": "pass", "avg_score": 7,
            "manuscript": "一段很长很长的原文……" * 50,
        })
        files = list((Path(paths.DATA_DIR) / "audit").glob("jev_review_gate-*.jsonl"))
        blob = files[0].read_text(encoding="utf-8")
        self.assertNotIn("一段很长", blob)  # 原文绝不进审计账

    def test_provider_fallback_to_mock_on_http_error(self):
        os.environ["JEV_ENABLED"] = "1"
        os.environ["JEV_ENDPOINT"] = "http://127.0.0.1:1/jev"
        os.environ["JEV_API_KEY"] = "x"
        # 连不上 → 退回 mock，不抛
        op = jev.assess({"avg_score": 9})
        self.assertEqual(op["source"], "mock")


if __name__ == "__main__":
    unittest.main()
