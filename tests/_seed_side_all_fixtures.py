# -*- coding: utf-8 -*-
"""侧栏「任务不消失」回归测试的造数（不碰真实数据）。

复现原始 bug：单个任务灌满最近 40 条 run 的窗口（45 条），把其余任务全部挤出侧栏。
造数内容：
  task-flood  —— 45 条 run（占满 40 条窗口还有富余），修复前独占侧栏
  task-quiet  —— 1 条 run，但排在窗口外（5 小时前），修复前不可见
  task-norun1 / task-norun2 —— 只有任务档案、从未运行过，修复前不可见
全部终态（done），load_all 不会把它们判成中断残骸。
用法：python tests/_seed_side_all_fixtures.py   （自动建临时目录，首行打印路径）
"""
import json
import sys
import tempfile
import time
from pathlib import Path

data = Path(tempfile.mkdtemp(prefix="tutti-side-all-"))
(data / "tasks").mkdir(parents=True)
runs_dir = data / "runs"
now = time.time()
MIN, H = 60, 3600


def ts(ago_s):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - ago_s))


def step(n, summary):
    return {"n": n, "role": "impl", "agent": "claude", "agent_label": "Claude Code",
            "note": "", "status": "done", "started_at": time.strftime("%H:%M:%S"),
            "ended_at": None, "duration_s": 30.0, "exit_code": 0,
            "summary": summary, "log": None, "cost_usd": 0.0, "tokens": 0}


def task(tid, title, ago):
    (data / "tasks" / (tid + ".json")).write_text(json.dumps({
        "id": tid, "type": "one_shot", "title": title, "goal": title,
        "context": "", "workdir": "", "mode": "auto", "difficulty": "auto",
        "created_at": ts(ago), "status": "done",
    }, ensure_ascii=False), encoding="utf-8")


def run(ago, seq, title, task_id, steps):
    rid = "r-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(now - ago)) + "-" + seq
    d = runs_dir / rid
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps({
        "id": rid, "kind": "orchestration", "title": title, "task_id": task_id,
        "entry_id": None, "op": None, "status": "done", "steps": steps,
        "created_at": ts(ago), "started_at": ts(ago), "ended_at": ts(max(0, ago - 60)),
        "cost_usd": 0.0, "tokens": 0, "error": "", "verdict": None, "summary": "",
    }, ensure_ascii=False), encoding="utf-8")


task("task-flood", "回归-刷屏任务（45 连跑）", 60 * MIN)
for i in range(45):
    run((i + 1) * MIN, "%04d" % (45 - i), "回归-刷屏任务（45 连跑）", "task-flood",
        [step(1, "第 %d 次运行：完成" % (i + 1))])

task("task-quiet", "回归-窗口外任务（5 小时前跑过）", 6 * H)
run(5 * H, "0001", "回归-窗口外任务（5 小时前跑过）", "task-quiet",
    [step(1, "早就结束的运行")])

task("task-norun1", "回归-从未运行的任务甲", 3 * H)
task("task-norun2", "回归-从未运行的任务乙", 2 * H)

print("seeded ->", data)
for p in sorted(runs_dir.glob("*/run.json")):
    print(" ", p.parent.name)
if len(sys.argv) > 1 and sys.argv[1] == "--quiet":
    print(data)
