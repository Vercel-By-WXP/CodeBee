# -*- coding: utf-8 -*-
"""run 内网络熔断台账回归（2026-10-26 头七实案）。

实案：死本地代理把大半供应商打成 10061 连接被拒，同一 aider 通道
（litellm InternalServerError: WinError 10061）在一场 run 里反复送死 20+
次——每个死候选都是一次完整 CLI 冷启动 + 自带重试梯，步骤拖成小时级。

台账契约：
· 同一 upstream 连续 NETWORK 死错达阈值（3）才进入本场熔断名单；
· 连败跨步骤共享，链上任一次成功即复位；
· 只认 NETWORK——TIMEOUT 是慢不是死，绝不进台账；
· upstream 为空/unknown 不记账；run 间互不影响；reset(run_id) 全清。
"""
import tempfile
import unittest

from app.core import bench
from app.core.error_codes import ErrorCode


class NetworkFuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        bench.init(self.tmp.name, start_probe=False)
        self.addCleanup(bench.init, self.tmp.name, start_probe=False)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(bench.reset, "run-net")

    def test_fuse_only_after_consecutive_threshold(self):
        self.assertFalse(bench.note_network_failure("run-net", "open.bigmodel.cn"))
        self.assertFalse(bench.note_network_failure("run-net", "open.bigmodel.cn"))
        self.assertEqual(bench.network_dead_upstreams("run-net"), set())
        self.assertTrue(bench.note_network_failure("run-net", "open.bigmodel.cn"))
        self.assertIn("open.bigmodel.cn", bench.network_dead_upstreams("run-net"))

    def test_success_resets_the_streak(self):
        for _ in range(2):
            bench.note_network_failure("run-net", "vsllm.cc")
        bench.note_upstream_ok("run-net", "vsllm.cc")
        self.assertEqual(bench.network_dead_upstreams("run-net"), set())
        # 复位后重新数：单次失败不再熔断
        bench.note_network_failure("run-net", "vsllm.cc")
        self.assertEqual(bench.network_dead_upstreams("run-net"), set())

    def test_network_ledger_is_separate_from_account_ledger(self):
        # 网络台账绝不连坐账号台账：网络死错不是账号死错，CLI 不得被停用
        for _ in range(3):
            bench.note_network_failure("run-net", "open.bigmodel.cn")
        self.assertIn("open.bigmodel.cn", bench.network_dead_upstreams("run-net"))
        self.assertEqual(bench.benched_ids("run-net"), set())
        self.assertFalse(bench.is_benched("run-net", "aider"))
        # TIMEOUT 是慢不是死：不存在 timeout 记账入口，NETWORK 码值独立
        self.assertNotEqual(ErrorCode.TIMEOUT.value, ErrorCode.NETWORK.value)

    def test_unknown_and_empty_upstream_ignored(self):
        self.assertFalse(bench.note_network_failure("run-net", "unknown"))
        self.assertFalse(bench.note_network_failure("run-net", ""))
        self.assertFalse(bench.note_network_failure("", "open.bigmodel.cn"))
        self.assertEqual(bench.network_dead_upstreams("run-net"), set())

    def test_runs_are_isolated_and_reset_clears(self):
        for _ in range(3):
            bench.note_network_failure("run-net", "api.iamhc.cn")
        bench.note_network_failure("run-other", "api.iamhc.cn")
        self.assertIn("api.iamhc.cn", bench.network_dead_upstreams("run-net"))
        self.assertEqual(bench.network_dead_upstreams("run-other"), set())
        bench.reset("run-net")
        self.assertEqual(bench.network_dead_upstreams("run-net"), set())


if __name__ == "__main__":
    unittest.main()
