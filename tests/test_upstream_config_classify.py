"""4xx 归属：把「端点/格式被拒」从「超时」里拆出来。

2026-10-23 首轮真实任务里 codex-cli 的终态写「超时」，同一条 stderr 却是
``Reconnecting... 2/5 (unexpected status 404 Not Found: openai_error)``。
原因是分类表没有 404：``_classify_failure`` 在 ``timed_out`` 分支里追死因，
追不到就回落 ``TIMEOUT``。读者于是去查网络和超时预算，真凶其实是网关不支持
``/v1/responses`` 这个端点。

本文件锁三件事：
1. 400/404/422 归 ``UPSTREAM_CONFIG``，且可换将、算 blocked、不算账号级死错；
2. 不误伤既有分类——``404 + model not found`` 仍是 ``MODEL_UNAVAILABLE``，
   429/402/5xx/408 各归其位；
3. 走真实子进程：CLI 先在 stderr 喷 404 再卡住直到墙钟上限，归因必须是
   ``UPSTREAM_CONFIG`` 而不是 ``TIMEOUT``；不带 4xx 证据的纯卡死仍是 ``TIMEOUT``。
"""
import os
import sys
import tempfile
import unittest

from app.core import runner
from app.core.error_codes import (ErrorCode, classify_error_text,
                                  is_account_dead_error, is_blocked_code)
from app.core.routing_strategy import BuiltinStrategy

# 桩进程：stderr 先喷一条 404，然后挂住，模拟 CLI 内部无限重连直到外部超时。
STUB = '''
import sys, time

mode = sys.argv[1] if len(sys.argv) > 1 else "quiet"
if mode == "notfound":
    sys.stderr.write(
        "Reconnecting... 2/5 (unexpected status 404 Not Found: openai_error,"
        " url: https://gateway.example.invalid/v1/responses)\\n")
    sys.stderr.flush()
time.sleep(120)
'''


class ClassifyFourXxTest(unittest.TestCase):
    def test_codex_reconnect_line_maps_to_upstream_config(self):
        text = ("Reconnecting... 2/5 (unexpected status 404 Not Found: "
                "openai_error, url: https://gateway.example.invalid/v1/responses)")
        self.assertEqual(classify_error_text(text), ErrorCode.UPSTREAM_CONFIG)

    def test_status_and_http_forms(self):
        for text in ("HTTP 404 not found",
                     "unexpected status 400 Bad Request",
                     "unexpected status 422 Unprocessable Entity",
                     "404 page not found",
                     "http/1.1 404 Not Found"):
            self.assertEqual(classify_error_text(text), ErrorCode.UPSTREAM_CONFIG,
                             "未归类：%s" % text)

    def test_model_not_found_keeps_model_unavailable(self):
        """404 也可能是模型不存在，模型级判定必须继续优先。"""
        for text in ("HTTP 404 model not found", "model not found"):
            self.assertEqual(classify_error_text(text), ErrorCode.MODEL_UNAVAILABLE,
                             "模型级判定被 4xx 抢走了：%s" % text)

    def test_neighbouring_codes_unchanged(self):
        cases = {
            "status 429 too many requests": ErrorCode.RATE_LIMIT,
            "status 402 payment required": ErrorCode.QUOTA,
            "HTTP 500 internal server error": ErrorCode.UPSTREAM_SERVER,
            "HTTP 503 upstream unavailable": ErrorCode.UPSTREAM_SERVER,
            "request timed out": ErrorCode.TIMEOUT,
            "connection reset by peer": ErrorCode.NETWORK,
        }
        for text, expected in cases.items():
            self.assertEqual(classify_error_text(text), expected,
                             "分类漂移：%s" % text)

    def test_prose_without_status_code_is_not_config(self):
        """没有状态码的普通文本不能被误判成端点配置错。"""
        self.assertIsNone(classify_error_text("reading 404 tokens from cache"))
        self.assertIsNone(classify_error_text("done"))

    def test_switchable_blocked_but_not_account_dead(self):
        code = ErrorCode.UPSTREAM_CONFIG
        self.assertTrue(BuiltinStrategy().should_fallback({"error_code": code.value}),
                        "端点配置错应允许换 CLI：换一个入口常常立刻能通")
        self.assertTrue(is_blocked_code(code), "配置错不是产品失败")
        self.assertFalse(is_account_dead_error(code),
                         "不是账号死错，不该停用整个 CLI")


class RealProcessAttributionTest(unittest.TestCase):
    """真实子进程：404 证据必须压过「超时」这个死法。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="upstream-config-")
        self.stub = os.path.join(self.tmp, "stub_cli.py")
        with open(self.stub, "w", encoding="utf-8") as fh:
            fh.write(STUB)

    def _classify(self, mode, timeout=2.0):
        res = runner.run_process(argv=[sys.executable, self.stub, mode],
                                 cwd=self.tmp, timeout=timeout)
        return res, runner._classify_failure(res)

    def test_404_then_hang_is_config_not_timeout(self):
        res, code = self._classify("notfound")
        self.assertTrue(res["timed_out"], "桩进程未走到超时：%r" % res)
        self.assertIn("404", res["stderr"], "404 原文没被保留：%r" % res["stderr"])
        self.assertEqual(code, ErrorCode.UPSTREAM_CONFIG,
                         "404 又被抹成超时了：%s" % code)

    def test_hang_without_4xx_stays_timeout(self):
        res, code = self._classify("quiet")
        self.assertTrue(res["timed_out"])
        self.assertEqual(code, ErrorCode.TIMEOUT,
                         "没有 4xx 证据的卡死应仍是 TIMEOUT，不能一律归配置错：%s" % code)


class PeriodicQuotaTest(unittest.TestCase):
    """周期额度打满（七天上限）是账号级死错，不是瞬时限流。

    2026-10-23 实案：claude-code 撞智谱
    「[1310][已达到 7 天使用上限，2026-10-26 14:09 后可继续使用]」。
    措辞带 429，被归成 RATE_LIMIT 后按瞬时错误退避重试——每一步重撞一次，
    单个任务拖到 54 分钟，最终评审人数不足、稿件判为未评估。
    它下一次能用是三天后，重试与换 KEY 都无意义，必须停用该 CLI 换通道。
    """

    PERIODIC = ("claude 返回 is_error: API Error: Request rejected (429) · "
                "[1310][已达到 7 天使用上限，2026-10-26 14:09:00 后可继续使用。"
                "如需超限额按量付费使用，可联系管理员开启超额按量付费。]"
                "[20261023174729bb71547765e64d47]")

    def test_periodic_limit_is_account_dead_quota(self):
        code = classify_error_text(self.PERIODIC)
        self.assertEqual(code, ErrorCode.QUOTA)
        self.assertTrue(is_account_dead_error(code),
                        "七天上限要停用该 CLI，否则每步重撞一次")

    def test_transient_429_stays_rate_limited(self):
        # 普通限流仍按瞬时处理：退避重试有意义，不能把整个账号停掉
        for text in ("API Error: Request rejected (429) rate limit exceeded",
                     "Error: 429 Too Many Requests",
                     "HTTP 429: too many requests"):
            with self.subTest(text=text):
                code = classify_error_text(text)
                self.assertEqual(code, ErrorCode.RATE_LIMIT)
                self.assertFalse(is_account_dead_error(code))

    def test_quota_word_without_recovery_time_is_not_periodic(self):
        # 只有「达到…使用上限」但没有「后可继续使用」的措辞不外推成周期额度，
        # 仍按原分类走（此处带 429 → 瞬时限流）。
        code = classify_error_text("Request rejected (429) 达到并发使用上限")
        self.assertEqual(code, ErrorCode.RATE_LIMIT)


if __name__ == "__main__":
    unittest.main()
