import threading
import unittest

from app.core import cli_discovery, model_family, task_graph


class TaskGraphTests(unittest.TestCase):
    def test_topological_order_and_cycle(self):
        nodes = [{"id": "b", "depends_on": ["a"]}, {"id": "a"}]
        self.assertEqual([n["id"] for n in task_graph.topological_order(nodes)], ["a", "b"])
        with self.assertRaises(task_graph.DependencyError):
            task_graph.topological_order([{"id": "a", "depends_on": ["b"]},
                                         {"id": "b", "depends_on": ["a"]}])

    def test_budget_and_cli_limit(self):
        budget = task_graph.Budget(max_calls=1)
        self.assertTrue(budget.consume()[0])
        self.assertFalse(budget.consume()[0])
        gate = task_graph.CliConcurrency({"x": 1})
        self.assertTrue(gate.acquire("x", timeout=0))
        self.assertFalse(gate.acquire("x", timeout=0))
        gate.release("x")

    def test_model_family(self):
        self.assertEqual(model_family.family("claude-3-7-sonnet"), "Anthropic")
        self.assertEqual(model_family.family("qwen-max"), "Alibaba")

    def test_cli_probe_missing_is_safe(self):
        result = cli_discovery.probe({"name": "missing", "executable": "definitely-missing"})
        self.assertFalse(result["available"])
        self.assertIn("install_hint", result)


if __name__ == "__main__":
    unittest.main()
