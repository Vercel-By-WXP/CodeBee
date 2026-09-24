# -*- coding: utf-8 -*-
"""Tests for the dependency-free trace/event borrowing slice."""
from __future__ import annotations

from base import BaseTest


class TestTraceBorrowing(BaseTest):
    def test_trace_links_run_step_and_dispatch_without_sensitive_text(self):
        from app.core import dispatch_log, store, tracing

        run = store.create_run("orchestration", "trace fixture")
        step, _ = store.add_step(
            run["id"], "implement", "mock-agent", "Mock Agent",
            model="mock-model", provider="mock-provider",
        )
        store.finish_step(
            run["id"], step["n"], "done", summary="secret body must not enter trace",
            duration_s=0.25, tokens=12, cost_usd=0.001,
        )
        dispatch_log.record_event(
            run_id=run["id"], role="implement", selected="mock-agent",
            selection_reason="routing metadata only sk-super-secret-token",
            result="done",
            span_id=tracing.span_id(run["id"], step["n"]),
        )

        events = dispatch_log.replay(run_id=run["id"])
        trace = tracing.build_trace(store.get_run(run["id"]), events)
        self.assertEqual(len(trace["trace_id"]), 32)
        self.assertEqual(trace["trace_id"], tracing.trace_id(run["id"]))
        saved_run = store.get_run(run["id"])
        self.assertEqual(saved_run["trace_schema_version"], tracing.SCHEMA_VERSION)
        self.assertEqual(saved_run["trace_id"], trace["trace_id"])
        self.assertEqual(saved_run["root_span_id"], tracing.root_span_id(run["id"]))
        self.assertEqual(len(trace["spans"]), 1)
        span = trace["spans"][0]
        self.assertEqual(span["span_id"], tracing.span_id(run["id"], 1))
        self.assertEqual(span["parent_span_id"], tracing.root_span_id(run["id"]))
        self.assertEqual(saved_run["steps"][0]["trace_id"], trace["trace_id"])
        self.assertEqual(saved_run["steps"][0]["span_id"], span["span_id"])
        self.assertIsInstance(saved_run["steps"][0]["started_at_epoch"], float)
        self.assertIsInstance(saved_run["steps"][0]["ended_at_epoch"], float)
        # Existing run storage keeps duration to one decimal place; the trace
        # preserves that contract while exposing it as a span attribute.
        self.assertEqual(span["duration_s"], 0.2)
        self.assertEqual(span["tokens"], 12)
        self.assertEqual(len(events[0]["event_id"]), 24)
        self.assertEqual(events[0]["trace_id"], trace["trace_id"])
        self.assertNotIn("secret body", repr(trace))
        self.assertNotIn("sk-super-secret-token", repr(trace))
        self.assertNotIn("password", repr(trace).lower())

    def test_trace_is_backward_compatible_with_legacy_steps_and_events(self):
        from app.core import tracing

        run = {"id": "legacy-run", "status": "done", "steps": [{
            "n": 1, "role": "review", "agent": "legacy", "status": "done",
            "duration_s": 1.0,
        }]}
        trace = tracing.build_trace(run, [{"run_id": "legacy-run", "phase": "done"}])
        self.assertEqual(trace["trace_id"], tracing.trace_id("legacy-run"))
        self.assertEqual(trace["spans"][0]["span_id"], tracing.span_id("legacy-run", 1))
        self.assertEqual(trace["events"][0]["trace_id"], trace["trace_id"])
        self.assertTrue(trace["events"][0]["event_id"])
