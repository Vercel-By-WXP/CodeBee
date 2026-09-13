# -*- coding: utf-8 -*-
"""用量台账：记录 → 聚合、runner 解析细分、步骤落盘携带 model。"""
from __future__ import annotations

import json

from app.core import paths, runner, store, usage
from tests.base import BaseTest


class TestUsageLedger(BaseTest):

    def test_record_and_summary(self):
        usage.record(source="pipeline", run_id="r-1", task_id="t-1", task_type="code",
                     role="implement", agent="codex-cli", agent_label="Codex CLI",
                     tool="codex", model="gpt-5.2", ok=True, duration_s=12.3,
                     cost_usd=0.02, usage={"input": 100, "output": 50, "cached": 25,
                                           "reasoning": 10, "total": 175})
        usage.record(source="pipeline", run_id="r-1", task_id="t-1", task_type="code",
                     role="review", agent="claude-code", agent_label="Claude Code",
                     tool="claude", model="claude-x", ok=False, cost_usd=0.01,
                     usage={"input": 10, "output": 5})
        s = usage.summary(days=0)
        t = s["totals"]
        self.assertEqual(t["calls"], 2)
        self.assertEqual(t["ok"], 1)
        self.assertEqual(t["failed"], 1)
        self.assertEqual(t["input"], 110)
        self.assertEqual(t["output"], 55)
        self.assertEqual(t["cached"], 25)
        self.assertEqual(t["tokens"], 175 + 15)
        self.assertAlmostEqual(t["cost_usd"], 0.03, places=4)
        self.assertEqual(len(s["by_day"]), 1)
        self.assertEqual(s["by_day"][0]["tokens"], 190)
        by_tool = {r["key"]: r for r in s["by_tool"]}
        self.assertEqual(by_tool["codex"]["tokens"], 175)
        self.assertEqual(by_tool["claude"]["calls"], 1)
        by_role = {r["key"]: r for r in s["by_role"]}
        self.assertIn("implement", by_role)
        self.assertIn("review", by_role)
        self.assertEqual(len(s["recent"]), 2)

    def test_record_never_raises(self):
        usage.record()  # 全空参数
        usage.record(usage={"input": "abc", "total": None}, cost_usd="x", ok=None)
        usage.record(usage={"input": 5, "output": 3})
        s = usage.summary(days=0)
        self.assertEqual(s["totals"]["calls"], 3)
        self.assertEqual(s["totals"]["input"], 5)  # "abc"→0，不抛错不丢记录

    def test_summary_days_window_and_zero_fill(self):
        import datetime
        today = datetime.date.today()
        # 昨天一条（30 天窗口内），40 天前一条（窗口外）
        old_usage_dir = paths.USAGE_DIR
        try:
            usage.record(tool="codex", usage={"total": 100})
            f = paths.USAGE_DIR / ("usage-%s.jsonl" % today.strftime("%Y%m"))
            line = json.loads(f.read_text(encoding="utf-8").strip().splitlines()[0])
            for offset, day in ((1, today - datetime.timedelta(days=1)),
                                (40, today - datetime.timedelta(days=40))):
                rec = dict(line)
                rec["day"] = day.isoformat()
                month = paths.USAGE_DIR / ("usage-%s.jsonl" % day.strftime("%Y%m"))
                with open(month, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            s30 = usage.summary(days=30)
            days30 = {d["day"]: d["tokens"] for d in s30["by_day"]}
            self.assertEqual(len(s30["by_day"]), 30)          # 连续补零
            self.assertEqual(days30.get((today - datetime.timedelta(days=1)).isoformat()), 100)
            self.assertEqual(sum(d["tokens"] for d in s30["by_day"]), 200)  # 40 天前的不计
            s_all = usage.summary(days=0)
            self.assertGreaterEqual(s_all["totals"]["tokens"], 300)
            self.assertGreaterEqual(len(s_all["by_day"]), 2)
        finally:
            paths.USAGE_DIR = old_usage_dir

    def test_dimension_grouping(self):
        for i in range(3):
            usage.record(tool="codex", role="draft", task_type="novel",
                         usage={"total": 10})
        usage.record(tool="claude", role="critique", task_type="novel",
                     usage={"total": 40})
        s = usage.summary(days=0)
        self.assertEqual(s["by_tool"][0]["key"], "claude")     # 按 tokens 倒序
        by_role = {r["key"]: r for r in s["by_role"]}
        self.assertEqual(by_role["draft"]["calls"], 3)
        self.assertEqual(by_role["critique"]["tokens"], 40)
        self.assertEqual(s["by_task_type"][0]["key"], "novel")
        self.assertEqual(s["by_model"][0]["key"], "(默认)")     # 未传 model 时的占位


class TestRunnerUsageParse(BaseTest):

    def test_parse_codex_jsonl_usage(self):
        stdout = "\n".join([
            json.dumps({"type": "item.completed",
                        "item": {"type": "agent_message", "text": "回答"}}),
            json.dumps({"type": "turn.completed",
                        "usage": {"input_tokens": 1200, "cached_input_tokens": 900,
                                  "output_tokens": 300, "reasoning_output_tokens": 120,
                                  "total_tokens": 1500}}),
        ])
        text, u = runner._parse_codex_jsonl(stdout)
        self.assertEqual(text, "回答")
        self.assertEqual(u, {"input": 1200, "output": 300, "cached": 900,
                             "reasoning": 120, "total": 1500})

    def test_parse_claude_json_usage(self):
        data = {"result": "结论", "total_cost_usd": 0.0123, "is_error": False,
                "usage": {"input_tokens": 100, "cache_creation_input_tokens": 40,
                          "cache_read_input_tokens": 800, "output_tokens": 60}}
        parsed = runner._parse_claude_json(json.dumps(data))
        self.assertEqual(parsed["text"], "结论")
        self.assertEqual(parsed["usage"]["cached"], 840)
        self.assertEqual(parsed["usage"]["total"], 100 + 60 + 840)
        self.assertEqual(parsed["tokens"], parsed["usage"]["total"])
        self.assertAlmostEqual(parsed["cost_usd"], 0.0123, places=6)

    def test_parse_codex_jsonl_multiturn_accumulates(self):
        turns = [json.dumps({"type": "turn.completed",
                             "usage": {"input_tokens": 100, "output_tokens": 50,
                                       "total_tokens": 150}}),
                 json.dumps({"type": "turn.completed",
                             "usage": {"input_tokens": 200, "cached_input_tokens": 80,
                                       "output_tokens": 70, "reasoning_output_tokens": 20}})]
        _, u = runner._parse_codex_jsonl("\n".join(turns))
        self.assertEqual(u["input"], 300)
        self.assertEqual(u["output"], 120)
        self.assertEqual(u["cached"], 80)
        self.assertEqual(u["total"], 150 + 270)  # 缺 total_tokens 的 turn 按 in+out 兜底

    def test_parse_claude_garbage_returns_none(self):
        self.assertIsNone(runner._parse_claude_json("not json"))
        self.assertIsNone(runner._parse_claude_json("[1,2]"))


class TestStepModelField(BaseTest):

    def test_finish_step_records_model(self):
        run = store.create_run("orchestration", "测试")
        step, _log = store.add_step(run["id"], "implement", "codex-cli", "Codex CLI")
        store.finish_step(run["id"], step["n"], "done", tokens=123, model="gpt-5.2")
        got = store.get_run(run["id"])["steps"][0]
        self.assertEqual(got["model"], "gpt-5.2")
        self.assertEqual(got["tokens"], 123)


if __name__ == "__main__":
    unittest.main()
