# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from unittest import mock

from base import BaseTest


class TestFailureNotify(BaseTest):
    def test_notifies_once_after_consecutive_exhaustion(self):
        from app.core import failure_notify

        old_threshold = os.environ.get("TUTTI_FAILURE_NOTIFY_THRESHOLD")
        os.environ["TUTTI_FAILURE_NOTIFY_THRESHOLD"] = "3"
        try:
            attempts = [{"ok": False, "provider_id": "p1", "model": "m1"},
                        {"ok": False, "provider_id": "p2", "model": "m2"}]
            with mock.patch.object(failure_notify, "send",
                                   return_value={"test": {"sent": True}}) as send:
                self.assertFalse(failure_notify.record_all_candidates_failed(
                    run_id="r1", task_id="t", role="implement", attempts=attempts,
                    error_code="UPSTREAM_SERVER")["notified"])
                self.assertFalse(failure_notify.record_all_candidates_failed(
                    run_id="r2", task_id="t", role="implement", attempts=attempts,
                    error_code="UPSTREAM_SERVER")["notified"])
                third = failure_notify.record_all_candidates_failed(
                    run_id="r3", task_id="t", role="implement", attempts=attempts,
                    error_code="UPSTREAM_SERVER")
                self.assertTrue(third["notified"])
                self.assertEqual(send.call_count, 1)
                duplicate = failure_notify.record_all_candidates_failed(
                    run_id="r3", task_id="t", role="implement", attempts=attempts)
                self.assertEqual(duplicate["reason"], "duplicate_run")
                self.assertEqual(send.call_count, 1)
        finally:
            if old_threshold is None:
                os.environ.pop("TUTTI_FAILURE_NOTIFY_THRESHOLD", None)
            else:
                os.environ["TUTTI_FAILURE_NOTIFY_THRESHOLD"] = old_threshold

    def test_success_resets_episode_and_partial_success_does_not_count(self):
        from app.core import failure_notify

        attempts = [{"ok": False}, {"ok": True}]
        self.assertEqual(failure_notify.record_all_candidates_failed(
            run_id="ok", role="review", attempts=attempts)["reason"],
            "chain_not_exhausted")
        failure_notify.record_success(role="review")
        self.assertEqual(failure_notify.record_all_candidates_failed(
            run_id="r1", role="review", attempts=[{"ok": False}])["consecutive_failures"], 1)

    def test_payload_has_no_error_text_or_prompt(self):
        from app.core import failure_notify

        result = failure_notify.record_all_candidates_failed(
            run_id="r1", task_id="t1", role="x", attempts=[{
                "ok": False, "error": "SECRET_API_KEY and prompt body",
                "provider_id": "provider-a", "model": "model-a"}],
            error_code="NETWORK")
        self.assertTrue(result["recorded"])
        state = (self.data_dir / "failure_notifications.json").read_text(encoding="utf-8")
        self.assertNotIn("SECRET_API_KEY", state)
        self.assertNotIn("prompt body", state)

    def test_metadata_redacts_credential_shaped_values(self):
        from app.core import failure_notify

        failure_notify.record_all_candidates_failed(
            run_id="r1", task_id="t1", role="x",
            attempts=[{"ok": False, "provider_id":
                       "https://api.example.test?api_key=SECRET_API_KEY",
                       "model": "sk-test-secret-value-12345"}],
            error_code="TOKEN=another-secret")
        state = (self.data_dir / "failure_notifications.json").read_text(encoding="utf-8")
        self.assertNotIn("SECRET_API_KEY", state)
        self.assertNotIn("sk-test-secret-value-12345", state)
        self.assertNotIn("another-secret", state)

    def test_malformed_email_headers_are_best_effort_failures(self):
        from app.core import failure_notify

        event = {"consecutive_failures": 3, "run_id": "r1"}
        with mock.patch.dict(os.environ, {
                "TUTTI_NOTIFY_SMTP_HOST": "smtp.example.test",
                "TUTTI_NOTIFY_EMAIL_TO": "oncall@example.test\nX-Injected: yes",
            }, clear=False):
            sent, detail = failure_notify._send_email(event)
        self.assertFalse(sent)
        self.assertEqual(detail, "email_error:ValueError")

    def test_failed_channels_leave_episode_retryable(self):
        from app.core import failure_notify

        with mock.patch.dict(os.environ, {
                "TUTTI_FAILURE_NOTIFY_THRESHOLD": "1",
                "TUTTI_FAILURE_NOTIFY_COOLDOWN_S": "0"}, clear=False), \
                mock.patch.object(failure_notify, "send",
                                  return_value={"email": {"sent": False}}):
            result = failure_notify.record_all_candidates_failed(
                run_id="r1", role="review", attempts=[{"ok": False}])
        self.assertTrue(result["notified"])
        state = (self.data_dir / "failure_notifications.json").read_text(encoding="utf-8")
        self.assertIn('"episode_notified": false', state)
