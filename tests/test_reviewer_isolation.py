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


if __name__ == "__main__":
    unittest.main()
