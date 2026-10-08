import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from core import agent_context, checkpoints, eval_matrix, flow_graph
from core import knowledge_pipeline, policy, retrieval, tracing, webhooks


class CompetitiveFeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_agent_context_discovers_nearest_files_and_scrubs_secrets(self):
        parent = self.root / "repo"
        workdir = parent / "packages" / "demo"
        workdir.mkdir(parents=True)
        (parent / "AGENTS.md").write_text(
            "# repo\nUse API key sk-1234567890abcdef and keep tests green.\n",
            encoding="utf-8")
        (workdir / "AGENTS.md").write_text("# demo\nPrefer small changes.\n", encoding="utf-8")

        result = agent_context.discover(workdir)

        self.assertEqual(result["files"][0]["path"], str(workdir / "AGENTS.md"))
        self.assertEqual(result["files"][1]["path"], str(parent / "AGENTS.md"))
        self.assertNotIn("sk-1234567890abcdef", result["prompt"])
        self.assertEqual(result["content_sha256"],
                         hashlib.sha256(result["prompt"].encode("utf-8")).hexdigest())
        self.assertIn("Prefer small changes", result["prompt"])

    def test_checkpoint_records_hashes_and_unknown_is_not_replayable(self):
        with patch.object(checkpoints, "_DIR", self.root / "checkpoints"):
            started = checkpoints.start("run-1", 2, "implement", "prompt with token sk-1234567890", attempt=2)
            self.assertEqual(started["attempt"], 2)
            self.assertEqual(len(started["prompt_sha256"]), 64)
            self.assertNotIn("sk-1234567890", json.dumps(started))
            checkpoints.finish("run-1", 2, "unknown", error="network timeout")
            view = checkpoints.replay_preview("run-1")
        self.assertFalse(view["replayable"])
        self.assertEqual(view["blocked_reason"], "requires_reconciliation")
        self.assertEqual(view["steps"][0]["status"], "unknown")

    def test_checkpoint_orphan_and_missing_run_are_not_replayable(self):
        with patch.object(checkpoints, "_DIR", self.root / "checkpoints"):
            checkpoints.start("orphan", 1, "implement", "prompt")
            orphan = checkpoints.replay_preview("orphan")
            missing = checkpoints.replay_preview("missing")
        self.assertFalse(orphan["replayable"])
        self.assertFalse(missing["replayable"])
        self.assertEqual(missing["blocked_reason"], "checkpoint_not_found")

    def test_webhook_signature_and_delivery_deduplication(self):
        raw = b'{"goal":"run"}'
        secret = "local-secret"
        timestamp = "1700000000"
        signature = webhooks.sign(secret, raw, timestamp)
        self.assertTrue(webhooks.verify(secret, raw, signature, timestamp, now=1700000001))
        self.assertFalse(webhooks.verify(secret, raw, signature, timestamp, now=1700001000))
        github = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        self.assertTrue(webhooks.verify_raw(secret, raw, github))
        with patch.object(webhooks, "_FILE", self.root / "deliveries.json"):
            self.assertTrue(webhooks.claim_delivery("delivery-1", now=1700000000)[0])
            self.assertFalse(webhooks.claim_delivery("delivery-1", now=1700000001)[0])
            webhooks.release_delivery("delivery-1")
            self.assertTrue(webhooks.claim_delivery("delivery-1", now=1700000002)[0])

    def test_eval_matrix_reports_baseline_regressions(self):
        manifest = eval_matrix.normalize_manifest({
            "id": "release-1",
            "candidates": ["model-a", "model-b"],
            "cases": [{"id": "case-1", "prompt": "hello", "expected": "hi"}],
            "regression_threshold": 0.5,
        })
        report = eval_matrix.evaluate(manifest, {
            "model-a": {"case-1": {"score": 7.0}},
            "model-b": {"case-1": {"score": 8.0}},
        }, baseline={"model-a": {"case-1": {"score": 8.0}}})
        self.assertEqual(report["matrix"][0]["candidate"], "model-a")
        self.assertEqual(report["regressions"][0]["delta"], -1.0)
        self.assertEqual(report["best_candidate"], "model-b")
        self.assertIsNone(eval_matrix.evaluate(manifest, {"model-a": {"case-1": {"score": float("nan")}}})["matrix"][0]["score"])

    def test_knowledge_pipeline_chunks_with_source_hash_and_quality(self):
        with patch.object(retrieval, "_FILE", self.root / "index.json"), \
             patch.object(knowledge_pipeline, "_REPORT_FILE", self.root / "reports.json"):
            result = knowledge_pipeline.ingest(
                "notes.md", "alpha beta " * 80,
                metadata={"scope": "code", "owner": "team"}, chunk_size=80, overlap=10)
            report = knowledge_pipeline.quality_report(result["ingest_id"])
        self.assertGreater(result["chunks"], 1)
        self.assertEqual(len(result["source_sha256"]), 64)
        self.assertEqual(report["source_sha256"], result["source_sha256"])
        self.assertEqual(report["indexed_chunks"], result["chunks"])
        self.assertGreater(report["quality"]["non_empty_ratio"], 0)
        with self.assertRaises(ValueError):
            knowledge_pipeline.ingest("bad", "text", metadata=["not", "an object"])

    def test_policy_sandbox_and_flow_graph_are_deterministic(self):
        sandbox = policy.normalize_sandbox({
            "allowed_roots": [str(self.root)], "env_allowlist": ["PATH", "OPENAI_API_KEY"],
            "network": False, "timeout_s": 0, "max_output_bytes": 0}, self.root)
        self.assertFalse(sandbox["network"])
        self.assertTrue(policy.path_allowed(self.root / "x.txt", sandbox))
        self.assertFalse(policy.path_allowed(self.root.parent / "x.txt", sandbox))
        self.assertEqual(policy.filter_env({"PATH": "x", "SECRET": "y"}, sandbox), {"PATH": "x"})
        graph = flow_graph.build("code")
        self.assertTrue(graph["nodes"])
        self.assertTrue(graph["edges"])
        self.assertEqual(graph["version_id"], flow_graph.build("code")["version_id"])

    def test_otel_export_has_standard_span_shape_and_route_endpoint(self):
        run = {"id": "run-otel", "status": "done", "started_at": "2026-01-01 00:00:00",
               "ended_at": "2026-01-01 00:00:01", "steps": [{
                   "n": 1, "role": "implement", "status": "done", "agent": "mock",
                   "started_at_epoch": 1, "ended_at_epoch": 2, "tokens": 3,
               }]}
        exported = tracing.to_otel(tracing.build_trace(run))
        span = exported["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        self.assertEqual(span["name"], "agent.implement")
        self.assertIn("traceId", span)
        h = object.__new__(main.Handler)
        h.path = "/api/flows/code/graph"
        h._authed = lambda: True
        h._json = lambda code, payload: (code, payload)
        status, payload = h._route_get()
        self.assertEqual(status, 200)
        self.assertEqual(payload["graph"]["flow_id"], "code")


if __name__ == "__main__":
    unittest.main()
