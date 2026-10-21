import unittest
from unittest.mock import patch

from app.core import portguard


class SafePortCleanupTests(unittest.TestCase):
    def test_test_instance_is_never_cleared(self):
        with patch.object(portguard, "clear_stale_port",
                          side_effect=AssertionError("must not clear")):
            self.assertEqual(
                portguard.safe_clear_stale_port(12345, "main.py", test_instance=True),
                (False, "测试实例不执行端口清场"),
            )

    def test_cleanup_exception_becomes_a_safe_failure_result(self):
        with patch.object(portguard, "clear_stale_port",
                          side_effect=OSError("diagnostic detail")):
            result = portguard.safe_clear_stale_port(12345, "main.py")
        self.assertEqual(result, (False, "端口清场失败（OSError）"))

    def test_success_result_is_preserved(self):
        expected = (True, "已结束旧实例 PID 42")
        with patch.object(portguard, "clear_stale_port", return_value=expected):
            self.assertEqual(
                portguard.safe_clear_stale_port(12345, "main.py"), expected)


if __name__ == "__main__":
    unittest.main()
