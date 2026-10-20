import unittest

from app.core.task_graph import run_dag


class RunDagTests(unittest.TestCase):
    def test_workers_receive_only_completed_dependency_ancestors(self):
        seen_inputs = {}

        def worker(node, inputs):
            seen_inputs[node["id"]] = dict(inputs)
            return node["id"]

        results = run_dag([
            {"id": "a"},
            {"id": "b"},
            {"id": "c", "depends_on": ["a"]},
        ], worker, max_workers=1)

        self.assertEqual(seen_inputs["a"], {})
        self.assertEqual(seen_inputs["b"], {})
        self.assertEqual(seen_inputs["c"], {"a": "a"})
        self.assertEqual(results, {"a": "a", "b": "b", "c": "c"})

    def test_completed_checkpoint_results_are_available_to_dependents(self):
        seen_inputs = {}

        def worker(node, inputs):
            seen_inputs[node["id"]] = dict(inputs)
            return node["id"]

        results = run_dag([
            {"id": "a"},
            {"id": "b", "depends_on": ["a"]},
        ], worker, completed={"a": "restored"})

        self.assertEqual(seen_inputs["b"], {"a": "restored"})
        self.assertEqual(results, {"a": "restored", "b": "b"})


if __name__ == "__main__":
    unittest.main()
