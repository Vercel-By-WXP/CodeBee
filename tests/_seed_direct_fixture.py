# -*- coding: utf-8 -*-
"""直连任务 UI 核验的种子：造一个已结束的 direct run（含用户消息与步骤输出）。

打印 TASK_ID=… / RUN_ID=… 供 tests/ui_direct_chat.mjs 取用。
不跑真实 CLI：直接落 run.json + 步骤日志，模拟「跑完一轮」的现场。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# TUTTI_DATA 由测试进程指定；paths 在导入期读环境变量，必须先设好
data_dir = os.environ.get("TUTTI_DATA") or ""
if not data_dir:
    raise SystemExit("需要 TUTTI_DATA")

from app.core import paths  # noqa: E402

paths.DATA_DIR = Path(data_dir)
paths.TASKS_DIR = paths.DATA_DIR / "tasks"
paths.RUNS_DIR = paths.DATA_DIR / "runs"
paths.USAGE_DIR = paths.DATA_DIR / "usage"
paths.CATALOG_FILE = paths.DATA_DIR / "catalog.json"
paths.ENABLED_FILE = paths.DATA_DIR / "orchestration.json"
paths.ensure_dirs()

from app.core import store  # noqa: E402

workdir = Path(data_dir) / "direct-work"
workdir.mkdir(parents=True, exist_ok=True)

task = store.create_task({
    "type": "direct",
    "title": "直连任务核验",
    "goal": "把 README 的错别字修掉",
    "workdir": str(workdir),
})
run = store.create_run("orchestration", task["title"], task_id=task["id"])

# 用户起手说的话（已消费：随首步送达）
store.add_message(run["id"], "顺便把标题也改一下", sender="测试机")
step, log_abs = store.add_step(run["id"], "direct", "mock-a", "Mock A")
body = "已修复 README 中的 3 处错别字，并调整了标题。\nDIRECT_DONE: 修复 README 错别字\n"
if log_abs:
    log_abs.write_text(body, encoding="utf-8")
store.finish_step(run["id"], step["n"], "done", summary=body[:200].strip())
store.update_run(run["id"], status="done", direct_session={"agent": "mock-a", "session": ""},
                 verdict={"type": "direct", "engine": "direct", "pass": True, "mode": "auto",
                          "direct": True, "turns": 1, "impl": "mock-a"},
                 summary="直连完成（1 轮）：修复 README 错别字")
try:
    store.write_report(run["id"], "# 直连任务：%s\n\n- 执行者：Mock A（1 轮对话）\n\n## 最近一轮输出\n\n%s\n"
                       % (task["title"], body))
except Exception:
    pass

print("TASK_ID=%s" % task["id"])
print("RUN_ID=%s" % run["id"])
