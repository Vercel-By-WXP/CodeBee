# -*- coding: utf-8 -*-
"""「取消不进 KEY 冷却」守卫（执行标准红线回归）。

docs/execution-standard.md 统一执行算法第 3 条：取消立即终止，且取消产生的
杀进程、断管道和尾部错误不得进入 KEY 冷却或健康惩罚。builtin_agent 的实现
顺序是「取消判断先于 note_key_error / note_key_ok」（源码注释「取消先于一切
记账：健康 KEY 不能因被放弃的请求背上冷却」），此前只有注释没有守卫——谁把
记账挪到取消判断前面，健康 KEY 就会因被用户放弃的请求背上 30 分钟冷却。

覆盖三条路径，传输层全部打桩（_post_sse_stream / _post_interruptible），
不发起真实网络；记账点直接替换成记录器，断言取消时一次都不落账：
  1) 流式中途取消 → 立即终态返回，不降级到非流式重发、不记账；
  2) 非流式请求被取消（连接被掐断 status=0）→ 不 note_key_error（主锁点：
     若取消判断被挪到记账之后，这里会红）；
  3) 响应 200 已到手但用户已取消 → 不 note_key_ok（成功也不记账）。
"""
from __future__ import annotations

import threading

from base import BaseTest


def _bi():
    return {"prov": {"id": "p", "name": "P", "protocol": "openai",
                     "base_url": "http://gw.test/v1", "api_key": "k",
                     "enabled": True, "model": "m"},
            "model": "m", "provider_id": "p", "provider_name": "P"}


OPENAI_REPLY = {"choices": [{"message": {"role": "assistant", "content": "正文"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                          "total_tokens": 15}}


class _Ledger(object):
    """记录 note_key_ok / note_key_error 的调用，替代真实 KEY 账本。"""

    def __init__(self):
        from app.core import modelhub
        self.mh = modelhub
        self.ok_calls, self.err_calls = [], []

    def __enter__(self):
        self._orig_ok = self.mh.note_key_ok
        self._orig_err = self.mh.note_key_error
        self.mh.note_key_ok = \
            lambda pid, kid="": self.ok_calls.append((pid, kid))
        self.mh.note_key_error = \
            lambda pid, kid="", err="": self.err_calls.append((pid, kid, err))
        return self

    def __exit__(self, *exc):
        self.mh.note_key_ok = self._orig_ok
        self.mh.note_key_error = self._orig_err
        return False


class TestStreamCancelReturnsImmediately(BaseTest):
    def runTest(self):
        """流式中途取消：立即终态，不降级重发、零记账。"""
        from app.core import builtin_agent as ba
        seen = {"sse": 0, "post": 0}

        def fake_sse(url, headers, body, allow_private, timeout, proto,
                     on_reason=None, on_text=None, on_tick=None,
                     cancel_event=None, **kw):
            seen["sse"] += 1
            if cancel_event is not None:
                cancel_event.set()          # 用户在流中途按下取消
            return {"status": 0, "text": "", "reasoning": "", "usage": {},
                    "calls": [], "events": 0, "error": "已取消"}

        def fake_post(url, headers, body, allow_private, timeout,
                      cancel_event, **kw):
            seen["post"] += 1
            return 200, dict(OPENAI_REPLY), ""

        ev = threading.Event()
        orig_sse, orig_post = ba._post_sse_stream, ba._post_interruptible
        ba._post_sse_stream, ba._post_interruptible = fake_sse, fake_post
        try:
            with _Ledger() as led:
                res = ba.run(_bi(), "写点什么", str(self.workdir),
                             cancel_event=ev)
        finally:
            ba._post_sse_stream, ba._post_interruptible = orig_sse, orig_post
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "已取消")
        self.assertEqual(seen["sse"], 1)      # 立即终止
        self.assertEqual(seen["post"], 0)     # 取消绝不降级/自动重试
        self.assertEqual(led.ok_calls + led.err_calls, [])


class TestNonStreamCancelNoKeyCooldown(BaseTest):
    def runTest(self):
        """非流式被掐断（status=0 + 取消）：不得 note_key_error。"""
        from app.core import builtin_agent as ba

        def fake_sse(url, headers, body, allow_private, timeout, proto,
                     on_reason=None, on_text=None, on_tick=None,
                     cancel_event=None, **kw):
            # 网关忽略 stream（零事件零错误）→ run 回落非流式重发
            return {"status": 200, "text": "", "reasoning": "", "usage": {},
                    "calls": [], "events": 0, "error": ""}

        def fake_post(url, headers, body, allow_private, timeout,
                      cancel_event, **kw):
            if cancel_event is not None:
                cancel_event.set()          # 请求在飞时用户取消，连接被掐
            return 0, None, "已取消"

        ev = threading.Event()
        orig_sse, orig_post = ba._post_sse_stream, ba._post_interruptible
        ba._post_sse_stream, ba._post_interruptible = fake_sse, fake_post
        try:
            with _Ledger() as led:
                res = ba.run(_bi(), "写点什么", str(self.workdir),
                             cancel_event=ev)
        finally:
            ba._post_sse_stream, ba._post_interruptible = orig_sse, orig_post
        self.assertFalse(res["ok"])
        self.assertIn("取消", res["error"])
        self.assertEqual(led.err_calls, [],
                         "取消请求不得给 KEY 记失败/冷却")
        self.assertEqual(led.ok_calls, [])


class TestCancelAfterSuccessNoKeyOk(BaseTest):
    def runTest(self):
        """响应 200 已到手但用户已取消：成功也不记账（取消先于一切记账）。"""
        from app.core import builtin_agent as ba

        def fake_sse(url, headers, body, allow_private, timeout, proto,
                     on_reason=None, on_text=None, on_tick=None,
                     cancel_event=None, **kw):
            return {"status": 200, "text": "", "reasoning": "", "usage": {},
                    "calls": [], "events": 0, "error": ""}

        def fake_post(url, headers, body, allow_private, timeout,
                      cancel_event, **kw):
            if cancel_event is not None:
                cancel_event.set()          # 结果到手的同时用户按下取消
            return 200, dict(OPENAI_REPLY), ""

        ev = threading.Event()
        orig_sse, orig_post = ba._post_sse_stream, ba._post_interruptible
        ba._post_sse_stream, ba._post_interruptible = fake_sse, fake_post
        try:
            with _Ledger() as led:
                res = ba.run(_bi(), "写点什么", str(self.workdir),
                             cancel_event=ev)
        finally:
            ba._post_sse_stream, ba._post_interruptible = orig_sse, orig_post
        self.assertFalse(res["ok"])
        self.assertIn("取消", res["error"])
        self.assertEqual(led.ok_calls, [],
                         "被取消的请求即使拿到 200 也不得记 KEY 成功")
        self.assertEqual(led.err_calls, [])
