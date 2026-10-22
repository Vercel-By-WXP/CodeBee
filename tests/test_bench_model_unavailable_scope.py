# -*- coding: utf-8 -*-
"""bench 停用范围锁：MODEL_UNAVAILABLE 不得停用 CLI（2026-10-22 1311 案）。

「[1311][当前订阅套餐暂未开放GLM-5.3-FlashX权限]」被判 MODEL_UNAVAILABLE 而
不是 FORBIDDEN 的关键收益就在这里：模型级死错只熔断 (供应商, 模型) 那一格，
pipeline 的台账链（bench.bench / record_failure）不得把整个 CLI 记成账号级
死错——claude-code 还要靠其余模型干活。若有人把 MODEL_UNAVAILABLE 加进
ACCOUNT_DEAD_CODES，这里会先红。
"""
import tempfile
import unittest

from app.core import bench
from app.core.error_codes import ErrorCode


class BenchScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        bench.init(self.tmp.name, start_probe=False)
        self.addCleanup(bench.init, self.tmp.name, start_probe=False)
        self.addCleanup(self.tmp.cleanup)

    def test_model_unavailable_never_benches_the_cli(self):
        self.assertFalse(bench.bench("run-1", "claude-code",
                                     ErrorCode.MODEL_UNAVAILABLE.value))
        self.assertFalse(bench.record_failure(
            "claude-code", ErrorCode.MODEL_UNAVAILABLE.value))
        self.assertNotIn("claude-code", bench.benched_ids("run-1"))
        self.assertFalse(bench.is_globally_benched("claude-code"))

    def test_account_dead_codes_still_bench(self):
        # bench 首报即停用、同 agent 重复报保留首报现场返回 False——每码独立 agent
        for idx, code in enumerate((ErrorCode.QUOTA, ErrorCode.FORBIDDEN,
                                    ErrorCode.MISSING_CREDENTIAL)):
            with self.subTest(code=code):
                self.assertTrue(bench.bench("run-2", "agent-%d" % idx,
                                            code.value))
                self.assertIn("agent-%d" % idx, bench.benched_ids("run-2"))


if __name__ == "__main__":
    unittest.main()
