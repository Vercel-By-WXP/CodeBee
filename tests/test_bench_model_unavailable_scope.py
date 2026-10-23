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

    def test_unavailable_model_is_benched_per_run_without_benching_cli(self):
        """同一步证明「这个模型名不存在」后，同 run 的后续步骤不该再撞。

        2026-10-23 实案：grok-build 未配绑定、被推荐了 6 个它不认的模型名，
        每个步骤重试整条链，单个任务拖到 54 分钟。修法是按 (供应商, 模型) 熔断
        这一格——不是停用 CLI（同 CLI 的其余模型还能干活）。
        """
        self.assertTrue(bench.bench_model("run-model", "prov-41",
                                          "glm-5.3-flash-max",
                                          ErrorCode.MODEL_UNAVAILABLE.value))
        self.assertIn(("prov-41", "glm-5.3-flash-max"),
                      bench.model_benched_keys("run-model"))
        # 本场只熔这一格：别的模型与别的 run 都不受影响
        self.assertNotIn(("prov-41", "gpt-5.6-sol"),
                         bench.model_benched_keys("run-model"))
        self.assertEqual(bench.model_benched_keys("run-other"), set())
        # 账号级台账保持原语义：模型级死错绝不把整条 CLI 停掉
        self.assertEqual(bench.benched_ids("run-model"), set())

    def test_only_model_unavailable_is_tracked_as_a_model_cell(self):
        # 欠费/超时是账号级或瞬时错误，不该进模型格台账（否则会误跳过活模型）
        for code in (ErrorCode.QUOTA, ErrorCode.TIMEOUT,
                     ErrorCode.FORBIDDEN, ErrorCode.VENDOR_ERROR):
            with self.subTest(code=code):
                self.assertFalse(bench.bench_model(
                    "run-scope", "prov-1", "m-%s" % code.value, code.value))
        self.assertEqual(bench.model_benched_keys("run-scope"), set())


if __name__ == "__main__":
    unittest.main()
