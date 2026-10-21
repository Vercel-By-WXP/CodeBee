import unittest

from scripts.architecture_check import (
    ROOT,
    build_graph,
    check_layers,
    load_baseline,
    load_policy,
)


class ArchitectureLayerTests(unittest.TestCase):
    def test_higher_layer_may_depend_on_lower_layer(self):
        policy = {"layers": {"0": ["storage"], "2": ["workflow"]}}

        self.assertEqual(
            check_layers(policy, {"workflow": {"storage"}}), [])

    def test_lower_layer_may_not_depend_on_higher_layer(self):
        policy = {"layers": {"0": ["storage"], "2": ["workflow"]}}

        violations = check_layers(policy, {"storage": {"workflow"}})

        self.assertEqual(len(violations), 1)
        self.assertIn("storage(L0) -> workflow(L2)", violations[0])

    def test_relative_publish_imports_keep_their_package_identity(self):
        graph, problems = build_graph(load_policy())

        self.assertEqual(problems, [])
        self.assertIn("publish.ledger", graph["publish.auto"])
        self.assertIn("publish.browser", graph["publish.manager"])
        self.assertIn("paths", graph["publish.manager"])
        self.assertIn("cli_text", graph["store"])
        self.assertNotIn("runner", graph["store"])
        self.assertFalse(any(name.startswith("output.") for name in graph))

    def test_repository_layer_assignments_have_no_reverse_dependencies(self):
        policy = load_policy()
        graph, problems = build_graph(policy)

        self.assertEqual(problems, [])
        self.assertEqual(check_layers(policy, graph), [])

    def test_baseline_does_not_keep_legacy_reversed_layer_exemptions(self):
        self.assertFalse(any(
            row.get("kind") == "layer"
            for row in load_baseline().get("violations", [])
            if isinstance(row, dict)))


if __name__ == "__main__":
    unittest.main()
