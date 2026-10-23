# -*- coding: utf-8 -*-
"""流式预览落盘预算回归（对应审查 C4）。

`stream_step` 每 0.6s 在全局 LOCK 内全量重写 run.json 并 fsync。实测（NVMe，
见 AUDIT-CODEBEE-2026-10-23.md C4）：0.28MB → 4.7ms、1.55MB → 11.9ms、
4.65MB → 32.3ms（峰值 51ms）；长跑 task 就落在 4MB 量级，并行任务的
update_run/add_step 全排在这把全局锁后面。前端 2s 才消费一次状态，0.6s 的
cadence 有一半白写。

本文件钉住修复后的三条契约（用子进程跑，避免污染其它用例的 DATA_DIR）：

1. 预览态落盘不透传 durable（省 fsync），终态 finish_step 仍必须 durable；
2. 默认节流放宽到 1.2s，且仍能「第一次放行、紧接着拒绝」；
3. 预览仍走 atomic（读者看不到半个文件，目录里不留 .tmp），内容可读回。
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

PROBE = r"""
import inspect, json, sys, tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp())
from app.core import paths
paths.DATA_DIR = root
paths.TASKS_DIR = root / "tasks"
paths.RUNS_DIR = root / "runs"
paths.RUNS_DIR.mkdir(parents=True, exist_ok=True)

from app.core import store

seen = []
real_write_json = store.atomic.write_json

def spy(path, data, **kw):
    seen.append({"durable": kw.get("durable", True), "bytes": len(str(data))})
    return real_write_json(path, data, **kw)

store.atomic.write_json = spy

run = store.create_run("orchestration", "流式落盘预算")
rid = run["id"]
step, _ = store.add_step(rid, "impl", "a1", "A1")

seen.clear()   # create_run/add_step 自己也是 durable 写，别混进预览统计

first = store.stream_step(rid, step["n"], thinking="想" * 50, text="写" * 50, min_secs=0)
preview_flags = [item["durable"] for item in seen]
preview_doc = json.loads((paths.RUNS_DIR / rid / "run.json").read_text(encoding="utf-8"))
preview_text = next(s for s in preview_doc["steps"] if s["n"] == step["n"]).get("stream")
seen.clear()

# 默认节流：紧接着再调一次必须被拒（1.2s 内）
second = store.stream_step(rid, step["n"], thinking="继续")

store.finish_step(rid, step["n"], status="done", summary="完成")
final_flags = [item["durable"] for item in seen]

p = paths.RUNS_DIR / rid / "run.json"
data = json.loads(p.read_text(encoding="utf-8"))
step_doc = next(s for s in data["steps"] if s["n"] == step["n"])
leftovers = [q.name for q in p.parent.iterdir() if q.name.endswith(".tmp")]

default_secs = inspect.signature(store.stream_step).parameters["min_secs"].default
print("@@RESULT@@" + json.dumps({
    "first": first,
    "second": second,
    "preview_durable": preview_flags,
    "final_durable": final_flags,
    "default_secs": default_secs,
    "preview_text": preview_text,
    "stream_after_finish": step_doc.get("stream"),
    "tmp_leftovers": leftovers,
    "file_bytes": p.stat().st_size,
}, ensure_ascii=False))
"""


class StreamPersistBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run([sys.executable, "-c", PROBE], cwd=str(REPO),
                              env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=120)
        assert proc.returncode == 0, proc.stderr[-3000:]
        line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@RESULT@@")]
        assert line, proc.stdout[-3000:]
        cls.r = json.loads(line[-1][len("@@RESULT@@"):])

    def test_preview_write_skips_fsync_but_final_write_is_durable(self):
        # 预览态：这一次 stream_step 的所有写入都不得 durable
        self.assertTrue(self.r["preview_durable"], "预览未落盘")
        self.assertEqual(set(self.r["preview_durable"]), {False},
                         "预览态仍在 fsync：瞬时打字动画不需要崩不丢")
        # 终态：finish_step 必须 durable（这是崩溃后唯一的真相）
        self.assertTrue(self.r["final_durable"])
        self.assertEqual(self.r["final_durable"], [True])

    def test_default_cadence_is_1_2s_and_still_throttles(self):
        self.assertAlmostEqual(self.r["default_secs"], 1.2, places=3)
        self.assertTrue(self.r["first"], "首次调用应落盘")
        self.assertFalse(self.r["second"], "1.2s 内第二次调用必须被节流")

    def test_preview_stays_atomic_and_readable(self):
        # 内容真写进去了（非 durable 不等于不落盘）
        self.assertEqual(self.r["preview_text"], "写" * 50)
        # 收尾后 finish_step 会清掉流式字段（最终值走 summary/output/thinking）
        self.assertIsNone(self.r["stream_after_finish"])
        # atomic.write_json 用同目录临时文件 + os.replace：异常退出可能留下
        # .tmp，这里确认收尾后目录干净（读者拿不到半成品）
        self.assertEqual(self.r["tmp_leftovers"], [])
        self.assertGreater(self.r["file_bytes"], 100)


if __name__ == "__main__":
    unittest.main()
