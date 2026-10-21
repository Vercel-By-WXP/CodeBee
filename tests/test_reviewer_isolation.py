import unittest
from unittest import mock

from app.core import router


class ReviewerIsolationTests(unittest.TestCase):
    def _agents(self):
        return [
            {"id": "author", "kind": "console", "mode": "real"},
            {"id": "same-family", "kind": "console", "mode": "real"},
            {"id": "other-family", "kind": "web", "mode": "real"},
        ]

    def test_same_model_family_is_rejected_even_when_cli_kind_differs(self):
        bindings = {
            "author": {"call_chain": [{"model": "gpt-5", "provider_id": "p1"}]},
            "same-family": {"call_chain": [{"model": "gpt-4.1", "provider_id": "p2"}]},
            "other-family": {"call_chain": [{"model": "claude-sonnet-4", "provider_id": "p3"}]},
        }
        with mock.patch.object(router.modelhub, "resolve_binding",
                               side_effect=lambda agent, difficulty="default": bindings.get(agent)), \
             mock.patch.object(router, "agent_upstreams", return_value=set()):
            reviewer, reason = router.pick_reviewer(
                self._agents(), self._agents()[0], "code", stats={})
        self.assertEqual(reviewer["id"], "other-family")
        self.assertIn("模型族/供应商", reason)

    def test_same_provider_is_rejected_when_family_is_unknown(self):
        agents = [
            {"id": "author", "kind": "console", "mode": "real"},
            {"id": "same-provider", "kind": "web", "mode": "real"},
            {"id": "other-provider", "kind": "web", "mode": "real"},
        ]
        bindings = {
            "author": {"call_chain": [{"model": "vendor-x", "provider_id": "p1"}]},
            "same-provider": {"call_chain": [{"model": "vendor-y", "provider_id": "p1"}]},
            "other-provider": {"call_chain": [{"model": "vendor-z", "provider_id": "p2"}]},
        }
        with mock.patch.object(router.modelhub, "resolve_binding",
                               side_effect=lambda agent, difficulty="default": bindings.get(agent)), \
             mock.patch.object(router, "agent_upstreams", return_value=set()):
            reviewer, _ = router.pick_reviewer(agents, agents[0], "code", stats={})
        self.assertEqual(reviewer["id"], "other-provider")

    def test_automatic_critics_prioritize_isolated_model_families(self):
        agents = [
            {"id": "author", "kind": "console", "mode": "real"},
            {"id": "same-family", "kind": "web", "mode": "real"},
            {"id": "claude", "kind": "web", "mode": "real"},
            {"id": "gemini", "kind": "console", "mode": "real"},
        ]
        bindings = {
            "author": {"call_chain": [{"model": "gpt-5", "provider_id": "p1"}]},
            "same-family": {"call_chain": [{"model": "gpt-4.1", "provider_id": "p2"}]},
            "claude": {"call_chain": [{"model": "claude-sonnet-4", "provider_id": "p3"}]},
            "gemini": {"call_chain": [{"model": "gemini-2.5-pro", "provider_id": "p4"}]},
        }
        scores = {"same-family": 100, "claude": 20, "gemini": 10}
        with mock.patch.object(router.modelhub, "resolve_binding",
                               side_effect=lambda agent, difficulty="default": bindings.get(agent)), \
             mock.patch.object(router, "agent_upstreams", return_value=set()), \
             mock.patch.object(router, "score",
                               side_effect=lambda agent, *_args: (scores[agent["id"]], "score")):
            critics, reason = router.pick_critics(
                agents, "story", stats={}, impl=agents[0])
        self.assertEqual({agent["id"] for agent in critics}, {"claude", "gemini"})
        self.assertIn("跨模型族隔离", reason)

    def test_automatic_critics_explain_same_source_compatibility_fallback(self):
        agents = [
            {"id": "author", "kind": "console", "mode": "real"},
            {"id": "same-family", "kind": "console", "mode": "real"},
        ]
        bindings = {
            "author": {"call_chain": [{"model": "gpt-5", "provider_id": "p1"}]},
            "same-family": {"call_chain": [{"model": "gpt-4.1", "provider_id": "p1"}]},
        }
        with mock.patch.object(router.modelhub, "resolve_binding",
                               side_effect=lambda agent, difficulty="default": bindings.get(agent)), \
             mock.patch.object(router, "agent_upstreams", return_value=set()):
            critics, reason = router.pick_critics(
                agents, "story", stats={}, impl=agents[0])
        self.assertEqual([agent["id"] for agent in critics], ["same-family"])
        self.assertIn("同源候选兼容回退", reason)
        self.assertIn("模型族相同", reason)


if __name__ == "__main__":
    unittest.main()
