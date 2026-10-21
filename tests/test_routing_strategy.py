import unittest
from unittest import mock

from app.core import router, routing_strategy


class RoutingStrategyTests(unittest.TestCase):
    def test_unknown_strategy_falls_back_to_compatible_builtin(self):
        strategy = routing_strategy.get("does-not-exist")
        self.assertIsInstance(strategy, routing_strategy.BuiltinStrategy)
        self.assertIn("builtin", routing_strategy.names())

    def test_error_taxonomy_controls_fallback_without_vendor_text_rules(self):
        strategy = routing_strategy.BuiltinStrategy()
        self.assertTrue(strategy.should_fallback({"error_code": "RATE_LIMIT"}))
        self.assertTrue(strategy.should_fallback("HTTP 503 upstream unavailable"))
        self.assertTrue(strategy.should_fallback({"error_code": "FORBIDDEN"}))
        self.assertTrue(strategy.should_fallback("HTTP 403 forbidden"))
        self.assertTrue(strategy.should_fallback({"error_code": "MODEL_UNAVAILABLE"}))
        self.assertTrue(strategy.should_fallback("unknown model id"))
        self.assertTrue(strategy.should_fallback({"error_code": "VENDOR_REFUSAL"}))
        self.assertTrue(strategy.should_fallback("content filtering"))
        self.assertFalse(strategy.should_fallback({"error_code": "AUTH"}))
        self.assertFalse(strategy.should_fallback({"error_code": "PARSE_FAIL"}))
        self.assertFalse(strategy.should_fallback({
            "error_code": "RATE_LIMIT", "raw": {"cancelled": True}}))

    def test_registration_is_explicit_and_fallback_chain_is_copied(self):
        class Custom(routing_strategy.RoutingStrategy):
            def select_cli(self, task, available_clis):
                return available_clis[0] if available_clis else None

            def should_fallback(self, error):
                return False

            def get_fallback_chain(self, cli):
                return []

        routing_strategy.register("test-custom", Custom)
        self.addCleanup(lambda: routing_strategy._FACTORIES.pop("test-custom", None))
        self.assertIsInstance(routing_strategy.get("test-custom"), Custom)
        chain = [{"id": "backup"}]
        result = routing_strategy.BuiltinStrategy().get_fallback_chain(
            {"fallbacks": chain})
        result[0]["id"] = "changed"
        self.assertEqual(chain[0]["id"], "backup")

    def test_callable_strategy_instance_is_not_treated_as_factory(self):
        class CallableStrategy(routing_strategy.RoutingStrategy):
            def __call__(self):
                raise AssertionError("strategy instance must not be called")

            def select_cli(self, task, available_clis):
                return None

            def should_fallback(self, error):
                return False

            def get_fallback_chain(self, cli):
                return []

        instance = CallableStrategy()
        routing_strategy.register("callable-instance", instance)
        self.addCleanup(lambda: routing_strategy._FACTORIES.pop(
            "callable-instance", None))
        self.assertIs(routing_strategy.get("callable-instance"), instance)

    def test_factory_error_is_explicit(self):
        def broken_factory():
            raise ValueError("bad construction")

        routing_strategy.register("broken-factory", broken_factory)
        self.addCleanup(lambda: routing_strategy._FACTORIES.pop(
            "broken-factory", None))
        with self.assertRaisesRegex(RuntimeError, "broken-factory"):
            routing_strategy.get("broken-factory")

    def test_router_pick_uses_selected_runtime_policy(self):
        class PreferSecond(routing_strategy.RoutingStrategy):
            def select_cli(self, task, available_clis):
                self.assert_task = task
                return available_clis[1], "custom policy"

            def should_fallback(self, error):
                return False

            def get_fallback_chain(self, cli):
                return []

        old_default = routing_strategy._DEFAULT_NAME
        policy = PreferSecond()
        routing_strategy.register("prefer-second-test", policy)
        self.addCleanup(lambda: routing_strategy._FACTORIES.pop(
            "prefer-second-test", None))
        self.addCleanup(routing_strategy.set_default, old_default)
        with mock.patch.dict("os.environ", {"CODEBEE_ROUTING_STRATEGY": ""}):
            routing_strategy.set_default("prefer-second-test")
            agents = [{"id": "first"}, {"id": "second"}]
            selected, reason = router.pick(agents, "implement", "code", stats={})

        self.assertIs(selected, agents[1])
        self.assertEqual(reason, "custom policy")
        self.assertEqual(policy.assert_task["role"], "implement")
        self.assertEqual(policy.assert_task["type"], "code")

    def test_router_rejects_policy_candidates_outside_the_active_pool(self):
        class OutsidePool(routing_strategy.RoutingStrategy):
            def select_cli(self, task, available_clis):
                return {"id": "not-enabled"}

            def should_fallback(self, error):
                return False

            def get_fallback_chain(self, cli):
                return []

        old_default = routing_strategy._DEFAULT_NAME
        routing_strategy.register("outside-pool-test", OutsidePool)
        self.addCleanup(lambda: routing_strategy._FACTORIES.pop(
            "outside-pool-test", None))
        self.addCleanup(routing_strategy.set_default, old_default)
        agents = [{"id": "enabled"}]
        with mock.patch.dict("os.environ", {"CODEBEE_ROUTING_STRATEGY": ""}), \
                mock.patch.object(router, "_pick_scored",
                                  return_value=(agents[0], "builtin")):
            routing_strategy.set_default("outside-pool-test")
            selected, reason = router.pick(agents, "implement", "code", stats={})

        self.assertIs(selected, agents[0])
        self.assertEqual(reason, "builtin")

    def test_switch_uses_ordered_chain_and_strategy_error_gate(self):
        class ChainFirst(routing_strategy.RoutingStrategy):
            def select_cli(self, task, available_clis):
                return available_clis[0] if available_clis else None

            def should_fallback(self, error):
                return error.get("error_code") == "TIMEOUT"

            def get_fallback_chain(self, cli):
                return ["backup"]

        old_default = routing_strategy._DEFAULT_NAME
        routing_strategy.register("chain-first-test", ChainFirst)
        self.addCleanup(lambda: routing_strategy._FACTORIES.pop(
            "chain-first-test", None))
        self.addCleanup(routing_strategy.set_default, old_default)
        agents = [{"id": "primary"}, {"id": "scored-first"},
                  {"id": "backup"}]
        with mock.patch.dict("os.environ", {"CODEBEE_ROUTING_STRATEGY": ""}):
            routing_strategy.set_default("chain-first-test")
            selected, reason = router.pick_switch_candidate(
                agents, "implement", "code", {}, exclude={"primary"},
                failed_cli=agents[0], error={"error_code": "TIMEOUT"})
            refused, refusal = router.pick_switch_candidate(
                agents, "implement", "code", {}, exclude={"primary"},
                failed_cli=agents[0], error={"error_code": "PARSE_FAIL"})

        self.assertIs(selected, agents[2])
        self.assertEqual(reason, "策略后备链第 1 位")
        self.assertIsNone(refused)
        self.assertIn("无需跨 CLI 降级", refusal)


if __name__ == "__main__":
    unittest.main()
