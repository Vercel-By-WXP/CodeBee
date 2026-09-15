# -*- coding: utf-8 -*-
"""使用统计扩展聚合单测（BaseTest 临时数据目录，不碰真实 data/）。
覆盖：模型名大小写归一、按日按模型趋势（by_day_model）、全史按日（all_by_day）、
峰值 Token、最长单次时长、当前/最长连续天数、空台账。纯进程内调用，不起服务。
"""
from __future__ import annotations

import datetime
import json
import unittest

from base import BaseTest


class UsageStatsTest(BaseTest):

    def _seed(self, day, model, total, dur=60.0, ok=True, cached=0):
        """直接写一行台账（record() 只记当天，历史日期得手写）。"""
        rec = {
            "ts": day + " 12:00:00", "day": day, "source": "pipeline", "run_id": "r-t",
            "step": 0, "task_id": "t-t", "task_type": "code", "role": "implement",
            "agent": "codex-cli", "agent_label": "Codex CLI", "tool": "codex",
            "model": model, "provider": "", "ok": ok, "duration_s": dur,
            "cost_usd": 0.01, "input": total // 2, "output": total // 4,
            "cached": cached, "reasoning": 0, "total": total,
        }
        f = self._paths.USAGE_DIR / ("usage-%s.jsonl" % day[:7].replace("-", ""))
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def test_summary_extensions(self):
        from app.core import usage

        today = datetime.date.today()
        d = lambda n: (today - datetime.timedelta(days=n)).isoformat()

        # 今天 GLM 两种大小写（2 大 1 小，归一后显示高频写法）；昨天/前天另一模型；
        # -5、-40 各一条（连续段参照：{-40} {-5} {-2,-1,0}）
        self._seed(d(0), "GLM-5.3-Flash", 1000, dur=100.0)
        self._seed(d(0), "GLM-5.3-Flash", 500, dur=50.0)
        self._seed(d(0), "glm-5.3-flash", 100, dur=10.0)
        self._seed(d(1), "DeepSeek-V4-Flash", 800, dur=200.0)
        self._seed(d(2), "DeepSeek-V4-Flash", 300, dur=30.0)
        self._seed(d(5), "glm-5.3-flash", 700, dur=70.0)
        self._seed(d(40), "qwen-max", 5000, dur=400.0)

        s = usage.summary(days=7)

        # 模型名归一：GLM 两大小写合并，显示名取出现最多的原始写法
        models = {r["key"]: r for r in s["by_model"]}
        self.assertNotIn("glm-5.3-flash", models)
        self.assertIn("GLM-5.3-Flash", models)
        # 今天 1000+500+100 加上 -5 那条小写 700（同属 7 天范围）
        self.assertEqual(models["GLM-5.3-Flash"]["tokens"], 2300)

        # by_day_model：与 by_day 同天序列、同一套归一名字、补零天空表
        self.assertEqual(len(s["by_day_model"]), len(s["by_day"]))
        bd = {r["day"]: r["models"] for r in s["by_day_model"]}
        self.assertEqual(bd[d(0)].get("GLM-5.3-Flash"), 1600)
        self.assertNotIn("glm-5.3-flash", bd[d(0)])
        self.assertEqual(bd[d(1)].get("DeepSeek-V4-Flash"), 800)
        self.assertEqual(bd[d(3)], {})

        # 峰值 / 最长单次
        self.assertEqual(s["totals"]["peak_tokens"], 1600)
        self.assertAlmostEqual(s["totals"]["max_duration_s"], 200.0, places=1)

        # 连续天数：今天有数据 → 锚今天；今/昨/前天连续 → 3；-5 断开 → 最长也是 3
        self.assertEqual(s["totals"]["streak_current"], 3)
        self.assertEqual(s["totals"]["streak_longest"], 3)

        # all_by_day：全历史、不补零、与范围无关
        all_days = {r["day"]: r["tokens"] for r in s["all_by_day"]}
        self.assertEqual(all_days.get(d(40)), 5000)
        self.assertEqual(len(s["all_by_day"]), 5)

        # days=0 全部范围
        s0 = usage.summary(days=0)
        self.assertEqual(s0["totals"]["tokens"], 8400)
        self.assertEqual(s0["totals"]["peak_tokens"], 5000)

    def test_summary_empty(self):
        from app.core import usage
        s = usage.summary(days=7)
        self.assertEqual(s["totals"]["tokens"], 0)
        self.assertEqual(s["all_by_day"], [])
        self.assertEqual(s["by_day_model"], [{ "day": r["day"], "models": {}} for r in s["by_day"]])
        self.assertEqual(s["totals"]["streak_current"], 0)
        self.assertEqual(s["totals"]["streak_longest"], 0)
        self.assertEqual(s["totals"]["peak_tokens"], 0)
        self.assertEqual(s["totals"]["max_duration_s"], 0.0)


if __name__ == "__main__":
    unittest.main()
