import unittest

from app.core import action_policy, zone_policy


class ZonePolicyTests(unittest.TestCase):
    def tearDown(self):
        zone_policy.unregister("write_file")

    def test_dynamic_resolver_can_raise_zone_and_fail_closed(self):
        zone_policy.register("write_file", lambda _ctx: "red")
        result = action_policy.preflight("write_file", sandbox={})
        self.assertEqual(result["zone"], "red")
        self.assertEqual(result["decision"], "deny")

    def test_invalid_resolver_result_fails_closed(self):
        zone_policy.register("write_file", lambda _ctx: "unknown")
        result = action_policy.preflight("write_file", sandbox={})
        self.assertEqual(result["zone"], "red")
        self.assertEqual(result["decision"], "deny")


if __name__ == "__main__":
    unittest.main()
