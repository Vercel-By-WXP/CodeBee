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
        " url: https://api.iamhc.cn/v1/responses)\\n")
    sys.stderr.flush()
time.sleep(120)
'''


class ClassifyFourXxTest(unittest.TestCase):
    def test_codex_reconnect_line_maps_to_upstream_config(self):
        text = ("Reconnecting... 2/5 (unexpected status 404 Not Found: "
                "openai_error, url: https://api.iamhc.cn/v1/responses)")
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


if __name__ == "__main__":
    unittest.main()
