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


class RankCandidates(JevBase):
    """路由侧：Jev 概率重排的开关/降级语义。"""

    def _cands(self):
        return [{"provider_id": "a", "model": "m1", "score": 9, "reason": "r1"},
                {"provider_id": "b", "model": "m2", "score": 5, "reason": "r2"}]

    def test_disabled_returns_none(self):
        self.assertIsNone(jev.rank_candidates(self._cands()))

    def test_no_key_returns_none(self):
        os.environ["JEV_ENABLED"] = "1"
        self.assertIsNone(jev.rank_candidates(self._cands()))

    def test_empty_candidates_none(self):
        os.environ["JEV_ENABLED"] = "1"
        self.assertIsNone(jev.rank_candidates([]))

    def test_http_error_returns_none(self):
        os.environ["JEV_ENABLED"] = "1"
        os.environ["JEV_ENDPOINT"] = "http://127.0.0.1:1/jev"
        os.environ["JEV_API_KEY"] = "x"
        # 连不上 → 返回 None（调用方保持确定性顺序），不抛、不连累主线
        self.assertIsNone(jev.rank_candidates(self._cands()))

    def test_valid_ranking_reorders_and_audits(self):
        os.environ["JEV_ENABLED"] = "1"
        os.environ["JEV_ENDPOINT"] = "http://127.0.0.1:9/jev"
        os.environ["JEV_API_KEY"] = "x"
        cands = self._cands()
        with mock.patch.object(jev, "_http_rank", return_value=[cands[1], cands[0]]):
            out = jev.rank_candidates(cands)
        self.assertEqual([c["provider_id"] for c in out], ["b", "a"])
        files = list((Path(paths.DATA_DIR) / "audit").glob("jev_orchestrator_rank-*.jsonl"))
        self.assertEqual(len(files), 1)
        rec = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(rec["event"], "jev_orchestrator_rank")
        self.assertEqual(rec["chosen"], "b|m2")
        self.assertEqual(rec["prev"], "a|m1")

    def test_invalid_http_rank_none(self):
        os.environ["JEV_ENABLED"] = "1"
        os.environ["JEV_ENDPOINT"] = "http://127.0.0.1:9/jev"
        os.environ["JEV_API_KEY"] = "x"
        with mock.patch.object(jev, "_http_rank", return_value=None):
            self.assertIsNone(jev.rank_candidates(self._cands()))


class ReorderParse(JevBase):
    """路由侧：Jev 响应解析与重排的兼容形状。"""

    def _cands(self):
        return [{"provider_id": "a", "model": "m1"},
                {"provider_id": "b", "model": "m2"},
                {"provider_id": "c", "model": "m3"}]

    def test_reorder_ranking_list(self):
        out = jev._reorder(self._cands(), {"ranking": [
            {"provider_id": "b", "model": "m2"}, {"provider_id": "a", "model": "m1"}]})
        self.assertEqual([c["provider_id"] for c in out], ["b", "a", "c"])

    def test_reorder_appends_unmentioned(self):
        out = jev._reorder(self._cands(), {"ranking": [{"provider_id": "c", "model": "m3"}]})
        self.assertEqual([c["provider_id"] for c in out], ["c", "a", "b"])

    def test_reorder_invalid_none(self):
        self.assertIsNone(jev._reorder(self._cands(), {}))
        self.assertIsNone(jev._reorder(self._cands(), None))

    def test_extract_order_weights(self):
        order = jev._extract_order({"weights": {"a|m1": 0.2, "b|m2": 0.8}})
        self.assertEqual(order, [("b", "m2"), ("a", "m1")])

    def test_extract_order_strings(self):
        order = jev._extract_order({"order": ["b|m2", "a|m1"]})
        self.assertEqual(order, [("b", "m2"), ("a", "m1")])


class JevRerankHook(JevBase):
    """modelhub._jev_rerank 集成钩子：Jev 结论透传 / 保持原序。"""

    def test_propagates_reranked(self):
        from app.core import modelhub
        cands = [{"provider_id": "x", "model": "y"}]
        reranked = [{"provider_id": "z", "model": "w"}]
        with mock.patch("app.core.jev.rank_candidates", return_value=reranked):
            self.assertEqual(modelhub._jev_rerank(cands), reranked)

    def test_none_when_rank_returns_none(self):
        from app.core import modelhub
        with mock.patch("app.core.jev.rank_candidates", return_value=None):
            self.assertIsNone(modelhub._jev_rerank([{"provider_id": "x", "model": "y"}]))


class AutoOrchPickJev(JevBase):
    """自动选型路径：Jev 重排决定最终选模（_auto_orch_pick_uncached 是唯一入口）。"""

    def _provs(self):
        return [
            {"id": "p1", "name": "P1", "protocol": "openai", "api_key": "k1",
             "model": "m1", "models": [{"name": "m1", "enabled": True, "priority": 1}],
             "enabled": True},
            {"id": "p2", "name": "P2", "protocol": "openai", "api_key": "k2",
             "model": "m2", "models": [{"name": "m2", "enabled": True, "priority": 2}],
             "enabled": True},
        ]

    def _run(self, rerank):
        from app.core import modelhub
        provs = self._provs()
        cands = [{"provider_id": "p1", "model": "m1"}, {"provider_id": "p2", "model": "m2"}]
        with mock.patch.object(modelhub, "_load", return_value={
                    "providers": provs, "pricing": {}, "orchestrator": {}}), \
             mock.patch("app.core.health.down_names", return_value=set()), \
             mock.patch("app.core.health.is_model_down", return_value=False), \
             mock.patch.object(modelhub, "_evaluation_is_usable", return_value=True), \
             mock.patch("app.core.dispatch.rank_model_entries",
                        return_value=(cands, [])), \
             mock.patch.object(modelhub, "_jev_rerank", return_value=rerank):
            return modelhub._auto_orch_pick_uncached()

    def test_jev_rerank_changes_pick(self):
        cands = [{"provider_id": "p1", "model": "m1"}, {"provider_id": "p2", "model": "m2"}]
        pick = self._run([cands[1], cands[0]])
        self.assertEqual(pick["provider_id"], "p2")
        self.assertEqual(pick["model"], "m2")

    def test_without_jev_keeps_dispatch_order(self):
        pick = self._run(None)
        self.assertEqual(pick["provider_id"], "p1")
        self.assertEqual(pick["model"], "m1")


class ResolveOrchestratorManual(JevBase):
    """resolve_orchestrator 分支：手动配置生效则按配置，否则走自动选型。"""

    def _prov(self):
        return {"id": "p1", "name": "P1", "protocol": "openai", "api_key": "k1",
                "model": "m1", "models": [{"name": "m1", "enabled": True, "priority": 1}],
                "enabled": True}

    def test_manual_config_used_when_valid(self):
        from app.core import modelhub
        prov = self._prov()
        with mock.patch.object(modelhub, "_load", return_value={
                    "providers": [prov], "pricing": {},
                    "orchestrator": {"provider_id": "p1", "model": "m1", "enabled": True}}), \
             mock.patch.object(modelhub, "providers", return_value=[prov]), \
             mock.patch.object(modelhub, "_evaluation_is_usable", return_value=True):
            r = modelhub.resolve_orchestrator()
        self.assertIsNotNone(r)
        self.assertEqual(r[0]["id"], "p1")
        self.assertEqual(r[1], "m1")

    def test_no_manual_falls_back_to_auto(self):
        from app.core import modelhub
        prov = self._prov()
        with mock.patch.object(modelhub, "_load", return_value={
                    "providers": [prov], "pricing": {}, "orchestrator": {}}), \
             mock.patch.object(modelhub, "_auto_orch_provider", return_value=(prov, "m1")):
            r = modelhub.resolve_orchestrator()
        self.assertEqual(r[0]["id"], "p1")
        self.assertEqual(r[1], "m1")


if __name__ == "__main__":
    unittest.main()
