# -*- coding: utf-8 -*-
"""右键菜单（打开/复制路径、重命名任务）UI 测试的造数：往 TUTTI_DATA 写
1 个带工作目录的任务 + 它的两条终态运行 + 1 条无任务的管理运行（不碰真实数据）。
只写磁盘、不起服务——load_all 启动时会把磁盘上 queued/running 的运行判为中断残骸，
所以这里全部用终态。
用法：TUTTI_DATA=/tmp/tutti-ctx python tests/_seed_ctx_fixtures.py"""
import json
import os
import time
from pathlib import Path

data = Path(os.environ.get("TUTTI_DATA") or "/tmp/tutti-ctx")
now = time.time()

# 任务工作目录：真实存在（reveal 的 is_dir 校验要过），里面放个稿件文件
workdir = data / "sandbox-workdir"
workdir.mkdir(parents=True, exist_ok=True)
(workdir / "manuscript.md").write_text("# 造数稿件\n", encoding="utf-8")

TASK = "task-ctx"


def ts(ago_s):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - ago_s))


def step(n, agent, label, status, summary):
    return {"n": n, "role": "impl", "agent": agent, "agent_label": label,
            "note": "", "status": status,
            "started_at": time.strftime("%H:%M:%S"), "ended_at": None,
            "duration_s": 30.0, "exit_code": 0 if status != "failed" else 1,
            "summary": summary, "log": None, "cost_usd": 0.0, "tokens": 0}


def run(ago, seq, title, task_id, status, steps):
    rid = "r-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(now - ago)) + "-" + seq
    d = data / "runs" / rid
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps({
        "id": rid, "kind": "orchestration" if task_id else "mgmt", "title": title,
        "task_id": task_id, "entry_id": None, "op": None, "status": status,
        "steps": steps, "created_at": ts(ago), "started_at": ts(ago),
        "ended_at": ts(max(0, ago - 60)), "cost_usd": 0.0, "tokens": 0,
        "error": "" if status != "failed" else "造数失败", "verdict": None, "summary": "",
    }, ensure_ascii=False), encoding="utf-8")
    return rid


# 任务（store.load_all 直接 json.loads 进 _TASKS；UI 只用 id/title/status/workdir/archived）
(data / "tasks").mkdir(parents=True, exist_ok=True)
(data / "tasks" / (TASK + ".json")).write_text(json.dumps({
    "id": TASK, "title": "右键菜单核验-原始名", "type": "novel", "goal": "核验右键菜单",
    "workdir": str(workdir), "status": "done", "archived": False, "created_at": ts(7200),
}, ensure_ascii=False), encoding="utf-8")

rid_done = run(2 * 3600, "0001", "右键菜单核验-原始名", TASK, "done", [
    step(1, "claude", "Claude Code", "done", "规划完成"),
    step(2, "codex", "Codex", "done", "成稿"),
])
rid_failed = run(30 * 3600, "0002", "右键菜单核验-原始名", TASK, "failed", [
    step(1, "codex", "Codex", "failed", "退出码 1"),
])
rid_mgmt = run(5 * 60, "0003", "管理运行-复制日志目录", "", "done", [
    step(1, "codex", "Codex", "done", "管理操作"),
])

print("seeded ->", data)
print("  task:", TASK, "workdir:", workdir)
print("  runs:", rid_done, rid_failed, rid_mgmt)
