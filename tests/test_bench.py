# -*- coding: utf-8 -*-
"""本场停用台账 + 额度强信号分类回归。

实案：2026-10-15《头七结账人》复评——qwen 欠费 403「预扣费额度失败」被
误判成 FORBIDDEN 后，连载评审逐章把同一死链送场，连撞 31 次白烧 4 小时。
这些用例钉住两件事：
1) 计费拒绝的强信号（预扣费/剩余额度/insufficient balance）先于裸 403 判定；
2) 账号级死错进 bench 后，后续步骤绕开、全停时编排干净收场。
"""
from __future__ import annotations

import sys
from pathlib import Path

from base import BaseTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestQuotaStrongSignal(BaseTest):
    """额度强信号先于 401/403 正则（qwen 网关把计费拒绝包成 403）。"""

    def test_qwen_prepaid_403_classifies_as_quota(self):
        from app.core.error_codes import ErrorCode, classify_error_text
        err = ("[API Error: 403 预扣费额度失败, 用户剩余额度: ¥0.006000, "
               "需要预扣费额度: ¥0.010000 (request id: 20261014175642...)]")
        self.assertEqual(classify_error_text(err), ErrorCode.QUOTA)

    def test_insufficient_balance_wording_classifies_as_quota(self):
        from app.core.error_codes import ErrorCode, classify_error_text
        self.assertEqual(
            classify_error_text("API Error: Insufficient balance"), ErrorCode.QUOTA)
        self.assertEqual(
            classify_error_text("403 insufficient credit balance"), ErrorCode.QUOTA)

    def test_permission_403_stays_forbidden(self):
        """权限类 403 不受强信号影响——强信号词不会出现在权限文案里。"""
        from app.core.error_codes import ErrorCode, classify_error_text
        self.assertEqual(
            classify_error_text("403 invalid API key for this model"),
            ErrorCode.FORBIDDEN)
        self.assertEqual(
            classify_error_text("HTTP/1.1 403 Forbidden: permission denied"),
            ErrorCode.FORBIDDEN)


class TestAccountDeadCodes(BaseTest):
    """账号级死错集合。"""

    def test_account_dead_set(self):
        from app.core.error_codes import is_account_dead_error
        for code in ("QUOTA", "AUTH", "MISSING_CREDENTIAL", "FORBIDDEN", "TOOL_LAUNCH"):
            self.assertTrue(is_account_dead_error(code), code)
        # transient / 正常错误不配下场
        for code in ("RATE_LIMIT", "TIMEOUT", "NETWORK", "UPSTREAM_SERVER", "", None):
            self.assertFalse(is_account_dead_error(code), code)


class TestBenchRegistry(BaseTest):
    """bench 台账行为：首报即停、transient 不进、reset 隔离 run。"""

    def setUp(self):
        super().setUp()
        from app.core import bench
        self.bench = bench
        self.bench.reset("r-test")

    def test_quota_failure_benches_cli(self):
        ok = self.bench.bench("r-test", "qwen", "QUOTA")
        self.assertTrue(ok)
        self.assertTrue(self.bench.is_benched("r-test", "qwen"))
        self.assertIn("额度", self.bench.reason("r-test", "qwen"))

    def test_transient_codes_never_bench(self):
        for code in ("RATE_LIMIT", "TIMEOUT", "NETWORK", "VENDOR_ERROR", "EMPTY"):
            self.assertFalse(self.bench.bench("r-test", "kimi", code))
        self.assertFalse(self.bench.is_benched("r-test", "kimi"))

    def test_first_report_wins(self):
        self.bench.bench("r-test", "qwen", "QUOTA")
        again = self.bench.bench("r-test", "qwen", "FORBIDDEN")
        self.assertFalse(again)
        self.assertEqual(self.bench.benched_agents("r-test")["qwen"].split("（")[1],
                         "QUOTA）")

    def test_benched_ids_and_reset(self):
        self.bench.bench("r-test", "qwen", "QUOTA")
        self.bench.bench("r-test", "codex", "MISSING_CREDENTIAL")
        self.assertEqual(self.bench.benched_ids("r-test"), {"qwen", "codex"})
        self.bench.reset("r-test")
        self.assertEqual(self.bench.benched_ids("r-test"), set())
        self.assertFalse(self.bench.is_benched("r-test", "qwen"))

    def test_other_run_unaffected(self):
        self.bench.bench("r-test", "qwen", "QUOTA")
        self.assertFalse(self.bench.is_benched("r-other", "qwen"))

    def test_reads_never_raise_on_garbage(self):
        """读侧宁白试不误杀：台账异常一律按未停用处理。"""
        self.assertFalse(self.bench.is_benched(None, "qwen"))
        self.assertFalse(self.bench.is_benched("r-test", None))
        self.assertEqual(self.bench.reason("r-test", "nobody"), "")
        self.assertEqual(self.bench.benched_agents("r-test"), {})
        self.assertFalse(self.bench.bench("", "qwen", "QUOTA"))


class TestPipelineBenchWiring(BaseTest):
    """pipeline 的停用接线冒烟：异常类与 import 都在。"""

    def test_pipeline_exposes_upstream_exhausted(self):
        from app.core import pipeline
        self.assertTrue(issubclass(pipeline.UpstreamExhausted, Exception))

    def test_pipeline_imports_bench_module(self):
        from app.core import bench, pipeline
        self.assertTrue(hasattr(bench, "bench"))
        self.assertTrue(hasattr(bench, "is_benched"))

    def test_global_round_spare_pool_excludes_benched(self):
        """全局评审补位池并入停用名单，且全挂错误行带停用名单。

        补位池不过滤时，qwen/codex 双双欠费后补位仍指向同两条死链，
        白烧一轮完整 CLI 冷启动（2026-10-15 案）。
        """
        import pathlib
        from app.core import pipeline
        src = pathlib.Path(pipeline.__file__).read_text(encoding="utf-8")
        # 补位池：tried 先并入 benched_ids 再进入候选过滤
        self.assertIn("tried |= _bench.benched_ids(run_id)", src)
        # 全挂收场：错误行拼接 benched_agents 名单（用户看得见去充哪个账号）
        self.assertIn("_bl = _bench.benched_agents(run_id)", src)
        self.assertIn("本场停用：", src)
