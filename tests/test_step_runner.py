# -*- coding: utf-8 -*-
"""step_runner（1D 重试守门）测试。
设计稿：docs/migration/02-context-compaction.md §1D。
"""
from __future__ import annotations

from base import BaseTest


def _ok_result(text="done"):
    return {"ok": True, "text": text, "error_code": ""}


def _overflow_result():
    return {"ok": False, "text": "", "error_code": "MAX_TOKENS",
            "error": "撑爆了"}


def _fail_result():
    return {"ok": False, "text": "", "error_code": "VENDOR_ERROR",
            "error": "崩了"}


def _big_session(run_id="r-1d", turns=8):
    """构造已超压的会话 + meter。"""
    from app.core.session_log import Session
    from app.core.token_meter import token_meter
    token_meter.reset(run_id)
    s = Session(run_id)
    s.append("system_message", {"content": "sys"})
    for i in range(turns):
        s.append("user_message", {"content": f"u{i} " + "x" * 500})
        s.append("assistant_message", {"content": f"a{i} " + "x" * 500})
    token_meter.accumulate(run_id, {"input": 120_000}, model="")
    return s


class TestExecuteStepBasic(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core.token_meter import token_meter
        token_meter.reset("r-1d")

    def tearDown(self):
        from app.core.token_meter import token_meter
        token_meter.reset("r-1d")
        super().tearDown()

    def test_success_no_retry(self):
        from app.core.step_runner import execute_step
        from app.core.session_log import Session
        calls = []
        s = Session("r-1d")
        s.append("user_message", {"content": "q"})
        res, retried = execute_step(
            s, lambda p, **k: calls.append(p) or _ok_result(), "prompt",
            llm_caller=lambda m: "")
        self.assertTrue(res["ok"])
        self.assertFalse(retried)
        self.assertEqual(len(calls), 1)

    def test_non_overflow_failure_no_retry(self):
        """非撑爆错误（如 VENDOR_ERROR）不触发压缩重试。"""
        from app.core.step_runner import execute_step
        from app.core.session_log import Session
        calls = []
        s = Session("r-1d")
        s.append("user_message", {"content": "q"})
        res, retried = execute_step(
            s, lambda p, **k: calls.append(p) or _fail_result(), "prompt",
            llm_caller=lambda m: "x")
        self.assertFalse(res["ok"])
        self.assertFalse(retried)
        self.assertEqual(len(calls), 1)

    def test_overflow_without_llm_caller_no_retry(self):
        """撑爆但没有压缩能力 → 不重试。"""
        from app.core.step_runner import execute_step
        from app.core.session_log import Session
        calls = []
        s = Session("r-1d")
        s.append("user_message", {"content": "q"})
        res, retried = execute_step(
            s, lambda p, **k: calls.append(p) or _overflow_result(), "prompt",
            llm_caller=None)
        self.assertFalse(retried)
        self.assertEqual(len(calls), 1)


class TestExecuteStepOverflowRetry(BaseTest):

    def setUp(self):
        super().setUp()
        from app.core.token_meter import token_meter
        token_meter.reset("r-1d")

    def tearDown(self):
        from app.core.token_meter import token_meter
        token_meter.reset("r-1d")
        super().tearDown()

    def test_overflow_compact_retry(self):
        """撑爆 → 压缩前进 generation → 同一 prompt 重试一次。"""
        from app.core.step_runner import execute_step
        s = _big_session()
        calls = []

        def runner(prompt, **k):
            calls.append(prompt)
            if len(calls) == 1:
                return _overflow_result()
            return _ok_result("第二轮成功")

        def llm(messages):
            return "摘要"

        res, retried = execute_step(s, runner, "原prompt", llm_caller=llm,
                                    retain_tail_tokens=500)
        self.assertTrue(res["ok"])
        self.assertTrue(retried)
        self.assertEqual(len(calls), 2)
        # 复用同一 prompt（不重新渲染）
        self.assertEqual(calls[0], calls[1])
        # generation 前进了
        self.assertGreaterEqual(s.replace_generation(), 1)

    def test_no_compaction_progress_no_retry(self):
        """撑爆但内容全在尾预算内（压缩无区域）→ 不重试。"""
        from app.core.step_runner import execute_step
        from app.core.session_log import Session
        from app.core.token_meter import token_meter
        token_meter.reset("r-1d")
        s = Session("r-1d")
        s.append("user_message", {"content": "tiny"})
        token_meter.accumulate("r-1d", {"input": 120_000})
        calls = []
        res, retried = execute_step(
            s, lambda p, **k: calls.append(p) or _overflow_result(), "p",
            llm_caller=lambda m: "x", retain_tail_tokens=8000)
        self.assertFalse(retried)
        self.assertEqual(len(calls), 1)
        token_meter.reset("r-1d")

    def test_retry_only_once(self):
        """重试后再撑爆不再重试（总调用数 = 2）。"""
        from app.core.step_runner import execute_step
        s = _big_session()
        calls = []

        def runner(prompt, **k):
            calls.append(prompt)
            return _overflow_result()

        res, retried = execute_step(s, runner, "p", llm_caller=lambda m: "x",
                                    retain_tail_tokens=500)
        self.assertFalse(res["ok"])
        self.assertTrue(retried)  # 重试发生了
        self.assertEqual(len(calls), 2)  # 但只重试一次


class TestExecuteStepPrecheck(BaseTest):
    """事前预检（借鉴 freebuff 每步容量重估）：换小窗模型后第一个请求前先压缩。"""

    def setUp(self):
        super().setUp()
        from app.core.token_meter import token_meter
        token_meter.reset("r-pre")

    def tearDown(self):
        from app.core.token_meter import token_meter
        token_meter.reset("r-pre")
        super().tearDown()

    def _seed(self, model, inp=120_000, turns=8):
        from app.core.session_log import Session
        from app.core.token_meter import token_meter
        token_meter.reset("r-pre")
        s = Session("r-pre")
        s.append("system_message", {"content": "sys"})
        for i in range(turns):
            s.append("user_message", {"content": "u%d " % i + "x" * 500})
            s.append("assistant_message", {"content": "a%d " % i + "x" * 500})
        token_meter.accumulate("r-pre", {"input": inp}, model=model)
        return s

    def test_precheck_compacts_before_first_call(self):
        """ctx 120k vs 未登记模型默认 128k 容量 → 发请求前就压缩（换将场景）。"""
        from app.core.step_runner import execute_step
        s = self._seed("small-model")
        gens = []

        def runner(prompt, **k):
            gens.append(s.replace_generation())
            return _ok_result("一次就成")

        res, retried = execute_step(s, runner, "p", model="small-model",
                                    llm_caller=lambda m: "摘要",
                                    retain_tail_tokens=500)
        self.assertTrue(res["ok"])
        self.assertFalse(retried)
        self.assertEqual(len(gens), 1)
        # 压缩发生在第一次调用之前：调用时 generation 已前进
        self.assertGreaterEqual(gens[0], 1)

    def test_precheck_skips_big_window_model(self):
        """gpt-5 容量 400k：120k 未超 0.9 阈 → 不预压，行为同老路径。"""
        from app.core.step_runner import execute_step
        s = self._seed("gpt-5")
        gens = []

        def runner(prompt, **k):
            gens.append(s.replace_generation())
            return _ok_result()

        res, retried = execute_step(s, runner, "p", model="gpt-5",
                                    llm_caller=lambda m: "x",
                                    retain_tail_tokens=500)
        self.assertTrue(res["ok"])
        self.assertFalse(retried)
        self.assertEqual(gens, [0])

    def test_precheck_needs_model(self):
        """不传 model（老调用方）：不预检，零行为变化。"""
        from app.core.step_runner import execute_step
        s = self._seed("")
        gens = []

        def runner(prompt, **k):
            gens.append(s.replace_generation())
            return _ok_result()

        execute_step(s, runner, "p", model="", llm_caller=lambda m: "x")
        self.assertEqual(gens, [0])