# -*- coding: utf-8 -*-
"""对话结果卡 UI 核验的种子：造一个已结束的 direct run，工作目录里带
HTML + Markdown 两个成品（HTML 用于「运行展示」按钮与 /preview 挂载）。

打印 TASK_ID=… / RUN_ID=… 供 tests/ui_chat_round_card.mjs 取用。
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

workdir = Path(data_dir) / "round-work"
workdir.mkdir(parents=True, exist_ok=True)

task = store.create_task({
    "type": "direct",
    "title": "纸枪手工页核验",
    "goal": "给我生成一个国庆纸枪手工教程网页",
    "workdir": str(workdir),
})
run = store.create_run("orchestration", task["title"], task_id=task["id"])

# 用户起手说的话（已消费：随首步送达）
store.add_message(run["id"], "给我生成一个国庆纸枪手工教程网页", sender="测试机")
step, log_abs = store.add_step(run["id"], "chat", "builtin", "CodeBee")
body = "已生成教程页面。\nROUND_DONE: 纸枪教程页\n"
if log_abs:
    log_abs.write_text(body, encoding="utf-8")
store.finish_step(run["id"], step["n"], "done", summary=body[:200].strip())

# 成品：先建 run 再落文件（run_artifacts 按「任务首跑之后的新文件」挑成品）
html = ("<!doctype html><html lang=\"zh\"><head><meta charset=\"utf-8\">"
        "<title>国庆纸枪手工教程</title></head>"
        "<body><h1>国庆纸枪手工教程</h1>"
        "<p id=\"ok\">页面已运行</p></body></html>")
(workdir / "纸枪教程.html").write_text(html, encoding="utf-8")
(workdir / "纸枪教程.md").write_text("# 国庆纸枪手工教程\n\n- 步骤一\n- 步骤二\n", encoding="utf-8")

store.update_run(run["id"], status="done",
                 direct_session={"agent": "builtin", "session": ""},
                 verdict={"type": "direct", "engine": "direct", "pass": True, "mode": "auto",
                          "direct": True, "turns": 1, "impl": "CodeBee（内置智能体·glm-5.3-flash）"},
                 summary="直连完成（1 轮）：生成纸枪教程页")

print("TASK_ID=" + task["id"])
print("RUN_ID=" + run["id"])
print("WORKDIR=" + str(workdir))
