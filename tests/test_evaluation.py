# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from unittest import mock

from base import BaseTest


class TestEvaluationExpiry(BaseTest):
    def test_passed_has_ttl_and_expires(self):
        from app.core import evaluation

        old = os.environ.get("TUTTI_EVALUATION_TTL_S")
        os.environ["TUTTI_EVALUATION_TTL_S"] = "60"
        try:
            result = evaluation.record(
                "model", "provider-a", "model-a",
                {"ok": True, "status": "passed"})
            self.assertTrue(result["fresh"])
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["expires_at"] - result["evaluated_at"], 60)
            fresh = evaluation.get("model", "provider-a", "model-a",
                                   now=result["evaluated_at"] + 59)
            self.assertTrue(fresh["fresh"])
            stale = evaluation.get("model", "provider-a", "model-a",
                                   now=result["expires_at"] + 1)
            self.assertFalse(stale["fresh"])
            self.assertEqual(stale["status"], "stale")
            self.assertEqual(stale["stale_reason"], "ttl_expired")
        finally:
            if old is None:
                os.environ.pop("TUTTI_EVALUATION_TTL_S", None)
            else:
                os.environ["TUTTI_EVALUATION_TTL_S"] = old

    def test_health_failure_invalidates_until_fresh_evaluation(self):
        from app.core import evaluation, health

        provider = evaluation.record(
            "provider", "provider-a", "",
            {"ok": True, "status": "reachable"})
        result = evaluation.record(
            "model", "provider-a", "model-a",
            {"ok": True, "status": "passed"})
        health.report_failure("Provider A", "HTTP 503", model="model-a",
                              provider_id="provider-a")
        stale = evaluation.get("model", "provider-a", "model-a",
                               now=result["evaluated_at"] + 1)
        self.assertFalse(stale["fresh"])
        self.assertEqual(stale["stale_reason"], "health_failure")
        self.assertFalse(evaluation.get(
            "provider", "provider-a", "",
            now=provider["evaluated_at"] + 1)["fresh"])
        fresh = evaluation.record(
            "model", "provider-a", "model-a",
            {"ok": True, "status": "passed"})
        self.assertTrue(evaluation.get("model", "provider-a", "model-a",
                                       now=fresh["evaluated_at"] + 1)["fresh"])

    def test_stale_evidence_is_not_usable_for_routing(self):
        from app.core import evaluation, modelhub

        with mock.patch.object(evaluation, "get",
                               return_value={"fresh": False, "status": "stale"}), \
                mock.patch.object(modelhub, "test_model",
                                   return_value={"ok": False, "fresh": True}):
            self.assertFalse(modelhub._evaluation_is_usable("provider-a", "model-a"))

    def test_corrupt_timestamps_are_treated_as_stale(self):
        from app.core import evaluation

        evaluation._write({"entries": {evaluation.key("model", "p", "m"): {
            "ok": True, "status": "passed", "evaluated_at": "broken",
            "expires_at": "also-broken"}}})
        value = evaluation.get("model", "p", "m")
        self.assertFalse(value["fresh"])
        self.assertFalse(value["ok"])
        self.assertEqual(value["stale_reason"], "ttl_expired")
