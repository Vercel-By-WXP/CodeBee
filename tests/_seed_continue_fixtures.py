# -*- coding: utf-8 -*-
"""「继续连载」UI 测试的造数：往 TUTTI_DATA 写
1 个已写完 2 章的连载任务（done 运行 + 大纲 + chapter-01/02.md + 成书）
+ 1 个非连载任务（负例：菜单不出现继续连载）。
只写磁盘、不起服务——全部用终态，避免 load_all 把 queued/running 判为中断残骸。
用法：TUTTI_DATA=/tmp/tutti-cont python tests/_seed_continue_fixtures.py"""
import json
import os
import time
from pathlib import Path

data = Path(os.environ.get("TUTTI_DATA") or "/tmp/tutti-cont")
now = time.time()

(data / "tasks").mkdir(parents=True, exist_ok=True)
# codex 启用参与编排（本机已装 → state.agents 里有真实智能体，可验证「选择器不出现 mock」）；
# claude 保持禁用。连载任务钉死 mock 实现/评审（manual），运行永远不会路由到真实 CLI
(data / "orchestration.json").write_text(json.dumps({
    "codex-cli": {"enabled": True}, "claude-code": {"enabled": False},
}, ensure_ascii=False), encoding="utf-8")

workdir = data / "serial-book"
workdir.mkdir(parents=True, exist_ok=True)
for i in (1, 2):
    (workdir / ("chapter-%02d.md" % i)).write_text(
        "# 第 %d 章\n\n本章正文（造数）。\n" % i, encoding="utf-8")
(workdir / "manuscript.md").write_text("# 造数之书\n\n第 1 章\n\n第 2 章\n", encoding="utf-8")

TASK = "task-cont"
PLAIN = "task-plain"


def ts(ago_s):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - ago_s))


def step(n, agent, label, status, summary):
    return {"n": n, "role": "impl", "agent": agent, "agent_label": label,
            "note": "", "status": status,
            "started_at": time.strftime("%H:%M:%S"), "ended_at": None,
            "duration_s": 30.0, "exit_code": 0, "summary": summary,
            "log": None, "cost_usd": 0.0, "tokens": 0}


(data / "tasks").mkdir(parents=True, exist_ok=True)
(data / "tasks" / (TASK + ".json")).write_text(json.dumps({
    "id": TASK, "title": "连载核验-原书", "type": "serial_novel",
    "goal": "造数：验证继续连载", "workdir": str(workdir), "status": "done",
    "archived": False, "created_at": ts(7200), "mode": "manual",
    "implementer": "mock-a", "critics": ["mock-a", "mock-b"],
    "manuscript": "manuscript.md", "threshold": 7.0,
    "serial": {"chapters": 2, "words_per_chapter": 800},
}, ensure_ascii=False), encoding="utf-8")
(data / "tasks" / (PLAIN + ".json")).write_text(json.dumps({
    "id": PLAIN, "title": "单稿件核验", "type": "novel", "goal": "造数：负例",
    "workdir": str(workdir), "status": "done", "archived": False, "created_at": ts(7200),
}, ensure_ascii=False), encoding="utf-8")

rid = "r-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(now - 3600)) + "-0001"
d = data / "runs" / rid
d.mkdir(parents=True, exist_ok=True)
(d / "run.json").write_text(json.dumps({
    "id": rid, "kind": "orchestration", "title": "连载核验-原书", "task_id": TASK,
    "entry_id": None, "op": None, "status": "done",
    "steps": [step(1, "mock-a", "演示智能体 A（mock）", "done", "大纲/成稿")],
    "created_at": ts(3600), "started_at": ts(3600), "ended_at": ts(3000),
    "cost_usd": 0.0, "tokens": 0, "error": "",
    "outline": {"book_title": "造数之书", "source": "template",
                "chapters": [{"title": "第 1 章", "beats": "开篇", "hook": "钩1"},
                             {"title": "第 2 章", "beats": "推进", "hook": "钩2"}]},
    "verdict": None, "summary": "",
}, ensure_ascii=False), encoding="utf-8")

print("seeded ->", data)
print("  tasks:", TASK, PLAIN, "workdir:", workdir)
print("  run:", rid)
