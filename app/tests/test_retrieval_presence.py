import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import analytics, openapi, presence, retrieval


class RetrievalPresenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.p1 = patch.object(retrieval, "_FILE", self.root / "index.json")
        self.p2 = patch.object(presence, "_FILE", self.root / "presence.json")
        self.p1.start(); self.p2.start()
        self.addCleanup(self.p1.stop); self.addCleanup(self.p2.stop)

    def test_retrieval_returns_traceable_hits(self):
        retrieval.upsert("chapters/01.md", "protagonist receives anonymous letter", {"chapter": 1})
        rows = retrieval.search("anonymous letter")
        self.assertEqual(rows[0]["source"], "chapters/01.md")
        self.assertIn("anonymous", rows[0]["matched_terms"])

    def test_explicit_document_ids_do_not_overwrite_other_sources(self):
        first = retrieval.upsert("a.md", "alpha", doc_id="shared")
        second = retrieval.upsert("b.md", "beta", doc_id="shared")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(retrieval._load()["documents"]), 2)

    def test_index_directory_is_scoped_and_rejects_symlink(self):
        allowed = self.root / "workspace"
        allowed.mkdir()
        (allowed / "ok.md").write_text("safe context", encoding="utf-8")
        outside = self.root / "secret.txt"
        outside.write_text("secret", encoding="utf-8")
        # A direct path outside an allowed root is rejected before reading.
        with self.assertRaises(ValueError):
            retrieval.index_directory(self.root, allowed_roots=[allowed])
        link = allowed / "linked.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            link = None
        result = retrieval.index_directory(allowed, allowed_roots=[allowed])
        self.assertEqual(result["indexed"], 1)
        if link is not None:
            self.assertNotIn("linked.txt", [x.get("source") for x in retrieval._load()["documents"].values()])

    def test_search_rejects_malformed_limit(self):
        with self.assertRaises(ValueError):
            retrieval.search("term", "not-a-number")

    def test_presence_marks_stale_agents_offline(self):
        with patch.object(presence, "_STALE_S", 1):
            row = presence.heartbeat("agent-1", "作者", ["writing"])
            self.assertTrue(row["agent_id"])
            with patch("core.presence.time.time", return_value=row["last_seen"] + 2):
                self.assertFalse(presence.list_agents()[0]["online"])

    def test_presence_rejects_unbounded_field_shapes(self):
        with self.assertRaises(ValueError):
            presence.heartbeat("agent-1", capabilities="writing")
        with self.assertRaises(ValueError):
            presence.heartbeat("agent-1", status=123)

    def test_presence_tolerates_corrupt_last_seen(self):
        presence._FILE.write_text('{"agent-1": {"agent_id": "agent-1", "last_seen": "bad"}}', encoding="utf-8")
        self.assertFalse(presence.list_agents()[0]["online"])

    def test_openapi_and_analytics_shape(self):
        doc = openapi.document()
        self.assertIn("/api/tasks/{task_id}/contract", doc["paths"])
        self.assertIn("/api/state", doc["paths"])
        self.assertIn("/api/health", doc["paths"])
        self.assertIn("codebeeQueryToken", doc["components"]["securitySchemes"])
        fake = type("S", (), {"list_runs": lambda self, limit=None: [
            {"task_id": "t", "status": "done", "tokens": 10, "cost_usd": .2,
             "verdict": {"overall": 8}}
        ]})()
        result = analytics.summary(fake)
        self.assertEqual(result["totals"]["tokens"], 10)
        self.assertEqual(result["tasks"][0]["quality_avg"], 8.0)


if __name__ == "__main__":
    unittest.main()
