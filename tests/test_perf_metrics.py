# -*- coding: utf-8 -*-
"""体验指标回归：首字延迟/吞吐只在带内容的增量上打点，零值不落台账字段。

背景（2026-09-24）：`usage.record` 新增 `first_token_ms`/`tokens_per_sec`，
只有内置直连的流式路径测得到（CLI 事件流没有逐 token 时刻）。风险有两类：
把"没测到"记成 0（用量页会显示首字 0 毫秒），以及把 usage-only 的收尾事件
当成首字（延迟被系统性低估）。本文件用假 SSE 传输把这两条钉住，并锁台账
与用量页 KPI 的字段契约。
"""
from __future__ import annotations

import json
import time
import unittest

from base import BaseTest


class _FakeResp:
    """最小 SSE 响应：可迭代出 data: 行，status=200。"""

    def __init__(self, lines):
        self._lines = lines
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._lines)

    def read(self, _n=0):
        return b""


class _FakeOpener:
    def __init__(self, resp):
        self._resp = resp

    def open(self, _req, timeout=None):
        return self._resp


def _sse(obj):
    return ("data: " + json.dumps(obj) + "\n").encode("utf-8")


def _collect(lines):
    """在假传输上跑一次 _post_sse_stream（openai 协议），返回结果 dict。"""
    from app.core import builtin_agent as ba

    orig_opener, orig_host = ba.modelhub._opener, ba.modelhub._validate_host
    ba.modelhub._opener = lambda: _FakeOpener(_FakeResp(lines))
    ba.modelhub._validate_host = lambda url, allow_private: ("api.test", "")
    try:
        return ba._post_sse_stream("https://api.test/v1/chat/completions", {},
                                   {"model": "m"}, True, 10, "openai")
    finally:
        ba.modelhub._opener, ba.modelhub._validate_host = orig_opener, orig_host


def _delay(seconds):
    """生成器里的延时标记（假传输按序吐行，真实网络节拍用 sleep 模拟）。"""
    return ("__sleep__", seconds)


def _replay(items):
    for it in items:
        if isinstance(it, tuple) and it[0] == "__sleep__":
            time.sleep(it[1])
        else:
            yield it


class TestStreamPerfMeasurement(unittest.TestCase):
    def test_first_token_ms_measured_on_content_delta(self):
        # 排队 0.3s 才吐第一个字 → 首字延迟可读；两条正文间隔 0.3s → 生成窗口 0.3s
        out = _collect(_replay([
            _delay(0.3),
            _sse({"choices": [{"delta": {"content": "你"}}]}),
            _delay(0.3),
            _sse({"choices": [{"delta": {"content": "好"}}]}),
            _sse({"choices": [{"delta": {}}], "usage": {"completion_tokens": 25}}),
            b"data: [DONE]\n"]))
        self.assertGreaterEqual(out["first_token_ms"], 250.0)
        # 25 token / ~0.3s ≈ 80 tok/s；窗口 <0.2s 时按不可测处理
        self.assertGreater(out["tokens_per_sec"], 10.0)
        self.assertLess(out["tokens_per_sec"], 500.0)

    def test_usage_only_events_are_not_first_token(self):
        out = _collect(_replay([
            _delay(0.3),
            _sse({"usage": {"completion_tokens": 3}}),
            _sse({"choices": [{"delta": {}}], "usage": {"completion_tokens": 3}})]))
        self.assertEqual(0.0, out["first_token_ms"])
        self.assertEqual(0.0, out["tokens_per_sec"])

    def test_tool_arg_delta_counts_as_content(self):
        """工具参数增量也是"模型开始输出"——不能只认正文。"""
        out = _collect(_replay([
            _delay(0.3),
            _sse({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": "{\"a\""}}]}}]}),
            _delay(0.3),
            _sse({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": "\":1}"}}]}}],
                "usage": {"completion_tokens": 40}})]))
        self.assertGreaterEqual(out["first_token_ms"], 250.0)
        self.assertGreater(out["tokens_per_sec"], 10.0)


class TestPerfLedgerContract(BaseTest):
    def _records(self):
        from app.core import paths
        day = time.strftime("%Y-%m-%d")
        f = paths.USAGE_DIR / ("usage-%s.jsonl" % day[:7].replace("-", ""))
        return [json.loads(x) for x in
                f.read_text(encoding="utf-8").splitlines() if x.strip()]

    def test_zero_values_are_not_persisted(self):
        from app.core import usage
        usage.record(source="perf", run_id="r-a", task_type="novel", role="draft",
                     agent="a1", ok=True, duration_s=30.0,
                     usage={"input": 100, "output": 20, "total": 120},
                     first_token_ms=812.5, tokens_per_sec=63.2)
        usage.record(source="perf", run_id="r-b", task_type="novel", role="draft",
                     agent="a1", ok=True, duration_s=30.0,
                     usage={"input": 100, "output": 20, "total": 120},
                     first_token_ms=0.0, tokens_per_sec=0.0)
        recs = self._records()
        self.assertEqual(2, len(recs))
        a, b = recs
        self.assertEqual(812.5, a["first_token_ms"])
        self.assertEqual(63.2, a["tokens_per_sec"])
        self.assertNotIn("first_token_ms", b)
        self.assertNotIn("tokens_per_sec", b)

    def test_summary_averages_only_measurable_rows(self):
        from app.core import usage
        for i, (ft, tps) in enumerate([(100.0, 50.0), (300.0, 150.0)]):
            usage.record(source="perf", run_id="r-%d" % i, task_type="novel",
                         role="draft", agent="a1", ok=True, duration_s=10.0,
                         usage={"input": 10, "output": 5, "total": 15},
                         first_token_ms=ft, tokens_per_sec=tps)
        usage.record(source="perf", run_id="r-x", task_type="novel", role="draft",
                     agent="a1", ok=True, duration_s=10.0,
                     usage={"input": 10, "output": 5, "total": 15})
        tot = usage.summary(days=1)["totals"]
        self.assertEqual(2, tot["perf_samples"])
        self.assertEqual(200.0, tot["avg_first_token_ms"])
        self.assertGreater(tot["p95_first_token_ms"], 0.0)
        self.assertEqual(100.0, tot["avg_tokens_per_sec"])

    def test_unmeasurable_range_reports_zero_samples(self):
        from app.core import usage
        usage.record(source="perf", run_id="r-cli", task_type="novel", role="draft",
                     agent="a1", ok=True, duration_s=10.0,
                     usage={"input": 10, "output": 5, "total": 15})
        tot = usage.summary(days=1)["totals"]
        self.assertEqual(0, tot["perf_samples"])
        self.assertEqual(0.0, tot["avg_first_token_ms"])
        self.assertEqual(0.0, tot["avg_tokens_per_sec"])


class TestUsagePageKpi(unittest.TestCase):
    """用量页 KPI 与 i18n 键的字符级契约——t() 是精确匹配，差一个空格就漏翻。"""

    def test_kpi_reads_totals_and_keys_exist(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        js = (root / "app" / "ui" / "app.js").read_text(encoding="utf-8")
        i18n = (root / "app" / "ui" / "i18n.js").read_text(encoding="utf-8")
        block = js.split("function renderUsage()", 1)[1].split("\n}", 1)[0]
        for field in ("perf_samples", "avg_first_token_ms",
                      "p95_first_token_ms", "avg_tokens_per_sec"):
            with self.subTest(field=field):
                self.assertIn(field, block)
        for key in ('"首字延迟"', '"暂无可测数据"', '"仅内置直连流式调用可测"',
                    '" · 吞吐 "', '" tok/s · 样本 "'):
            with self.subTest(key=key):
                self.assertIn(key + ":", i18n)
        self.assertIn('t("首字延迟")', block)


if __name__ == "__main__":
    unittest.main()
