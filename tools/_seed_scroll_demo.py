# -*- coding: utf-8 -*-
"""造数：direct 任务 + done 运行 + running 步骤带长思维链（验证思考面板自动滚动）。"""
import io
import json
import os
import sys

assert os.environ.get("TUTTI_DATA"), "须设 TUTTI_DATA"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
from core import paths, store  # noqa: E402

paths.ensure_dirs()
WD = str(paths.DATA_DIR.parent / "workspace")
os.makedirs(WD, exist_ok=True)

t = store.create_task({"type": "direct", "title": "滚动验证",
                       "goal": "验证思考过程面板自动滚动", "workdir": WD})
r = store.create_run("orchestration", "滚动验证", task_id=t["id"])
think = "\n\n".join("第 %d 段思考：模型在推理本步骤应该怎么处理输入，分析约束、检查边界、" % i
                    for i in range(60))
step, log_abs = store.add_step(r["id"], "chat", "builtin", "内置智能体")
if log_abs:
    log_abs.write_text("（思考流）\n", encoding="utf-8")
with store.LOCK:
    run = store._RUNS[r["id"]]
    st = run["steps"][-1]
    st["thinking"] = think
    st["started_at"] = "12:00:00"
    run["status"] = "done"
    run["ended_at"] = "12:01:00"
    import time
    run["summary"] = "验证用"
    store._save_json(paths.RUNS_DIR / r["id"] / "run.json", run)
store.update_task_status(t["id"], "done")
print("run_id=", r["id"], "| thinking_len=", len(think))
