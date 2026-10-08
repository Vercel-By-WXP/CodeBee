# -*- coding: utf-8 -*-
"""对话时间线「跳到最新」UI 核验的种子：造一个已结束的 direct 任务，run 里
塞 24 个长输出步骤 + 3 条用户消息——时间线足够长能滚动，才能量「首开落底 /
跳最新 / 回看不打扰」。

打印 TASK_ID=… / RUN_ID=… 供 tests/ui_chat_jump_latest.mjs 取用。
不跑真实 CLI：直接落 run.json + 步骤，模拟「跑完多轮」的现场。
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

workdir = Path(data_dir) / "jump-work"
workdir.mkdir(parents=True, exist_ok=True)

task = store.create_task({
    "type": "direct",
    "title": "长对话跳最新核验",
    "goal": "把这份长文档逐段整理成网页",
    "workdir": str(workdir),
})
run = store.create_run("orchestration", task["title"], task_id=task["id"])

# 首条用户消息（已消费）
store.add_message(run["id"], "把这份长文档逐段整理成网页", sender="测试机")

# 24 个长输出步骤 + 中途两条追问：每步正文 ~1600 字，总高远超视口，保证可滚动
para = ("第{n}段整理说明：这一段详细描述了文档的结构与要点，覆盖标题层级、"
        "正文段落与列表样式的映射关系，同时补充了样式细节与排版约束，"
        "确保生成网页时每一段都能正确呈现，不会出现截断或溢出。") * 8
for i in range(1, 25):
    if i == 9:
        store.add_message(run["id"], "第 8 段之后的表格也要转成 HTML", sender="测试机")
    if i == 17:
        store.add_message(run["id"], "配色换成深色系", sender="测试机")
    step, _log = store.add_step(run["id"], "chat", "builtin", "CodeBee")
    body = ("已完成第 {n} 段的整理与渲染。\n{p}\nROUND_DONE: 段落 {n}\n"
            ).format(n=i, p=para.replace("{n}", str(i)))
    store.finish_step(run["id"], step["n"], "done",
                      summary=body[:200].strip(), output=body)

store.update_run(run["id"], status="done",
                 verdict={"type": "direct", "engine": "direct", "pass": True, "mode": "auto",
                          "direct": True, "turns": 3, "impl": "CodeBee（内置智能体）"},
                 summary="直连完成（多轮）：长文档逐段整理成网页")

print("TASK_ID=" + task["id"])
print("RUN_ID=" + run["id"])
