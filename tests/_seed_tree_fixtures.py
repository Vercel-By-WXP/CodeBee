# -*- coding: utf-8 -*-
"""侧栏任务树 UI 测试的造数：往 TUTTI_DATA/runs 写几条终态运行（不碰真实数据）。
只写磁盘、不起服务——load_all 启动时会把磁盘上 queued/running 的运行判为中断残骸，
所以这里全部用终态（done/failed/cancelled）；「进行中」的字形由测试在页面里注入验证。
用法：TUTTI_DATA=/tmp/tutti-tree python tests/_seed_tree_fixtures.py"""
import json
import os
import time
from pathlib import Path

data = Path(os.environ.get("TUTTI_DATA") or "/tmp/tutti-tree")
runs_dir = data / "runs"
tasks_dir = data / "tasks"
now = time.time()
MIN, H, D = 60, 3600, 86400


def ts(ago_s):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - ago_s))


# 三个不同工作目录：验证侧栏「文件夹 → 任务」两级分组。proj-alpha 含最近活动（5 分前）
# 的两个任务，应作为无 running 时默认展开的那个文件夹。
WDIRS = {
    "task-5m": data / "proj-alpha",
    "task-2h": data / "proj-alpha",
    "task-1d": data / "proj-beta",
    "task-3d": data / "proj-gamma",
}


def step(n, agent, label, status, summary):
    return {"n": n, "role": "impl", "agent": agent, "agent_label": label,
            "note": "断点续跑" if n % 2 else "", "status": status,
            "started_at": time.strftime("%H:%M:%S"), "ended_at": None,
            "duration_s": 30.0, "exit_code": 0 if status != "failed" else 1,
            "summary": summary, "log": None, "cost_usd": 0.0, "tokens": 0}


def run(ago, seq, title, task_id, status, steps):
    rid = "r-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(now - ago)) + "-" + seq
    d = runs_dir / rid
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps({
        "id": rid, "kind": "orchestration", "title": title, "task_id": task_id,
        "entry_id": None, "op": None, "status": status, "steps": steps,
        "created_at": ts(ago), "started_at": ts(ago), "ended_at": ts(max(0, ago - 60)),
        "cost_usd": 0.0, "tokens": 0, "error": "", "verdict": None, "summary": "",
    }, ensure_ascii=False), encoding="utf-8")


def task(task_id, title, ago):
    wd = WDIRS[task_id]
    wd.mkdir(parents=True, exist_ok=True)
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / (task_id + ".json")).write_text(json.dumps({
        "id": task_id, "type": "novel-serial", "engine": "novel", "title": title,
        "goal": title, "context": "", "workdir": str(wd), "mode": "auto",
        "difficulty": "auto", "created_at": ts(ago), "status": "done",
    }, ensure_ascii=False), encoding="utf-8")


task("task-5m", "样式核验-五分钟前", 5 * MIN)
run(5 * MIN, "0001", "样式核验-五分钟前", "task-5m", "done", [
    step(1, "claude", "Claude Code", "done", "规划：三幕结构已定"),
    step(2, "codex", "Codex", "done", "实现：正文两千字"),
    step(3, "claude", "Claude Code", "done", "评审：通过"),
])
task("task-2h", "样式核验-两小时前（失败）", 2 * H)
run(2 * H, "0002", "样式核验-两小时前（失败）", "task-2h", "failed", [
    step(1, "claude", "Claude Code", "done", "规划完成"),
    step(2, "codex", "Codex", "failed", "验证命令退出码 1"),
])
task("task-1d", "样式核验-昨天（取消）", 30 * H)
run(30 * H, "0003", "样式核验-昨天（取消）", "task-1d", "cancelled", [
    step(1, "codex", "Codex", "cancelled", "用户取消"),
])
task("task-3d", "样式核验-三天前（20 步长任务）", 3 * D)
run(3 * D, "0004", "样式核验-三天前（20 步长任务）", "task-3d", "done", [
    step(i, "claude", "Claude Code", "done", "断点续跑：第 %d 段续写完成" % i) for i in range(1, 21)
])

print("seeded ->", runs_dir)
for p in sorted(runs_dir.glob("*/run.json")):
    print(" ", p.parent.name)
