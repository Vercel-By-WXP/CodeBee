# -*- coding: utf-8 -*-
"""压力分级报告与压缩触发的分级语义。

覆盖三块契约：
  1. token_meter.pressure_report 双口径分级（累计 bands + 下次请求锚点兜底）；
  2. maybe_compact 触发条件：累计达阈值 **或** 分级 critical，且压力快照
     随 compaction_start 落审计；
  3. execute_step 每步压力留痕（非 normal 落 context_pressure shadow 事件），
     precheck 用报告的锚点口径决定事前压缩。
"""
import unittest

from app.core.compaction import maybe_compact, compact_region
from app.core.session_log import Session
from app.core.step_runner import execute_step
from app.core.token_meter import TokenMeter, token_meter


def _mk_session():
    return Session("run-test-compaction-pressure")


class PressureReportTests(unittest.TestCase):
    def _meter(self):
        return TokenMeter(capacity={"": 1000})

    def test_bands_follow_cumulative_ratio(self):
        tm = self._meter()
        r1 = "run-normal"
        tm.accumulate(r1, {"input": 200})
        self.assertEqual(tm.pressure_report(r1)["level"], "normal")
        r2 = "run-notice"
        tm.accumulate(r2, {"input": 550})
        self.assertEqual(tm.pressure_report(r2)["level"], "notice")
        r3 = "run-warning"
        tm.accumulate(r3, {"input": 850})
        self.assertEqual(tm.pressure_report(r3)["level"], "warning")
        r4 = "run-critical"
        tm.accumulate(r4, {"input": 960})
        self.assertEqual(tm.pressure_report(r4)["level"], "critical")

    def test_next_anchor_forces_critical_below_critical_band(self):
        """累计 0.91 只到 warning 档，但下次请求锚点 0.91 贴容量 → critical。"""
        tm = self._meter()
        tm.accumulate("run-anchor", {"input": 910})
        rep = tm.pressure_report("run-anchor")
        self.assertEqual(rep["level"], "critical")
        self.assertLess(rep["ratio"], 0.95)
        self.assertGreaterEqual(rep["next_ratio"], 0.9)

    def test_model_falls_back_to_last_seen(self):
        tm = TokenMeter(capacity={"": 500, "mX": 1000})
        tm.accumulate("run-model", {"input": 450}, model="mX")
        self.assertEqual(tm.pressure_report("run-model")["capacity"], 1000)
        self.assertEqual(tm.pressure_report("run-model", model="mX")["capacity"], 1000)

    def test_empty_run_is_normal(self):
        tm = self._meter()
        rep = tm.pressure_report("run-empty")
        self.assertEqual(rep["level"], "normal")
        self.assertEqual(rep["used_tokens"], 0)
        self.assertEqual(rep["next_request_tokens"], 0)


class MaybeCompactTests(unittest.TestCase):
    def _seed_session(self):
        """两段 4000 字符表面消息：retain_tail=100 时区域为 [seq2, seq2]。"""
        s = _mk_session()
        s.append("system_message", {"content": "sys"})
        s.append("user_message", {"content": "A" * 4000})
        s.append("assistant_message", {"content": "B" * 4000})
        return s

    def test_no_llm_caller_is_noop(self):
        s = self._seed_session()
        self.assertFalse(maybe_compact(s, model="m", llm_caller=None))
        self.assertFalse([e for e in s.events() if e.type == "compaction_start"])

    def test_below_threshold_non_critical_skips(self):
        tm = TokenMeter(capacity={"": 1000})
        tm.accumulate("run-skip", {"input": 300})
        s = self._seed_session()
        calls = []
        self.assertFalse(maybe_compact(s, model="m", llm_caller=lambda ms: calls.append(ms) or "x",
                                       threshold=0.8, run_id="run-skip", meter=tm))
        self.assertEqual(calls, [])

    def test_critical_anchor_overrides_threshold(self):
        """累计 0.91 低于阈值 0.95，但锚点 critical 必须照样压缩。"""
        tm = TokenMeter(capacity={"": 1000})
        tm.accumulate("run-crit", {"input": 910})
        s = self._seed_session()
        calls = []
        ok = maybe_compact(s, model="m", llm_caller=lambda ms: calls.append(ms) or "摘要",
                           threshold=0.95, run_id="run-crit", meter=tm,
                           retain_tail_tokens=100)
        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)
        starts = [e for e in s.events() if e.type == "compaction_start"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0].data["pressure"]["level"], "critical")
        self.assertEqual(s.replace_generation(), 1)
        # 表面已折叠：seq2 的原文被摘要消息替换
        msgs = s.derive_messages()
        self.assertNotIn("A" * 4000, [m["content"] for m in msgs])

    def test_summary_failure_still_closes_transaction(self):
        tm = TokenMeter(capacity={"": 1000})
        tm.accumulate("run-fail", {"input": 910})
        s = self._seed_session()

        def _boom(messages):
            raise RuntimeError("llm down")

        ok = compact_region(s, 2, 2, _boom, pressure={"level": "critical"})
        self.assertFalse(ok)
        ends = [e for e in s.events() if e.type == "compaction_end"]
        self.assertEqual(len(ends), 1)
        self.assertTrue(ends[0].data["error"])


class ExecuteStepPressureTests(unittest.TestCase):
    def setUp(self):
        self.run_id = "run-step-%d" % id(self)

    def tearDown(self):
        token_meter.reset(self.run_id)

    def _ok_agent(self, counter):
        def _call(prompt, **kw):
            counter.append(prompt)
            return {"ok": True, "error_code": "", "text": "done"}
        return _call

    def test_elevated_pressure_is_recorded(self):
        token_meter.accumulate(self.run_id, {"input": 120000})
        s = Session(self.run_id)
        counter = []
        result, retried = execute_step(s, self._ok_agent(counter), "p",
                                       model="m", llm_caller=None)
        self.assertTrue(result["ok"])
        self.assertFalse(retried)
        evs = [e for e in s.events() if e.type == "context_pressure"]
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].data["level"], "critical")

    def test_normal_pressure_records_nothing(self):
        s = Session(self.run_id)
        counter = []
        execute_step(s, self._ok_agent(counter), "p", model="m", llm_caller=None)
        self.assertFalse([e for e in s.events() if e.type == "context_pressure"])

    def test_precheck_compacts_before_send_on_critical_anchor(self):
        token_meter.accumulate(self.run_id, {"input": 120000})
        s = Session(self.run_id)
        s.append("system_message", {"content": "sys"})
        s.append("user_message", {"content": "A" * 4000})
        s.append("assistant_message", {"content": "B" * 4000})
        counter = []
        result, retried = execute_step(
            s, self._ok_agent(counter), "p", model="m",
            llm_caller=lambda ms: "摘要", retain_tail_tokens=100)
        self.assertTrue(result["ok"])
        self.assertFalse(retried)
        self.assertEqual(len(counter), 1)
        self.assertGreater(s.replace_generation(), 0)
        self.assertEqual(len([e for e in s.events()
                              if e.type == "compaction_start"]), 1)
        self.assertEqual(len([e for e in s.events()
                              if e.type == "context_pressure"]), 1)


if __name__ == "__main__":
    unittest.main()
