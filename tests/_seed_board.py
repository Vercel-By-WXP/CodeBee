# -*- coding: utf-8 -*-
"""任务驾驶舱（/board.html）验证造数 + 同进程起服务。

大屏要展示「运行中」卡片，但 load_all 把 queued/running 判为中断残骸、
启动收尸会全部改判 failed——所以这里先 patch 掉收尸，再造数，最后在
同一进程里起服务（造数已落盘，main() 里的 load_all 会从盘上读回来）。

用法：TUTTI_DATA=<dir> TUTTI_PET_DISABLED=1 python tests/_seed_board.py 18977 --no-browser
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from core import jobs, paths, store, usage  # noqa: E402

# 1) 关启动收尸：大屏演示需要活着的 running。
# 改判发生在两处——recover_orphaned_runs 与 load_all 读盘时（running/无退避
# 的 queued 就地改 failed）。这里直接换成「只装载、不恢复」的 load_all 替身。
def _load_all_keep_active():
    with store.LOCK:
        for p in paths.TASKS_DIR.glob("*.json"):
            try:
                t = json.loads(p.read_text(encoding="utf-8"))
                store._TASKS[t["id"]] = t
            except Exception:
                pass
        for p in paths.RUNS_DIR.glob("*/run.json"):
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
                r.pop("cancel_event", None)
                store._RUNS[r["id"]] = r
            except Exception:
                pass


store.recover_orphaned_runs = lambda: 0
store.recover_interrupted_mgmt = lambda: 0
store.load_all = _load_all_keep_active
# 启动恢复三兄弟会把 queued/failed 的种子 run 真拉起来跑（隔离环境没凭据
# 必失败还污染台账），演示服务一律 no-op
jobs.resume_interrupted = lambda *a, **k: 0
jobs.requeue_pending = lambda *a, **k: 0
jobs.restore_deferred_resumes = lambda *a, **k: 0

NOW = time.time()


def ts(ago_s):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW - ago_s))


def hm(ago_s):
    return time.strftime("%H:%M:%S", time.localtime(NOW - ago_s))


# 2) 造数 ---------------------------------------------------------------
paths.ensure_dirs()

# 厂商表（models.json）：给「模型→厂商」反查供数（大屏卡片的厂商徽章）
(paths.DATA_DIR / "models.json").write_text(json.dumps({
    "providers": [
        {"id": "bigmodel", "name": "智谱", "enabled": True,
         "models": [{"name": "glm-5.3", "enabled": True, "priority": 10},
                    {"name": "glm-5.1", "enabled": True, "priority": 20}]},
        {"id": "anthropic", "name": "Anthropic", "enabled": True,
         "models": [{"name": "claude-sonnet-4-5", "enabled": True, "priority": 10}]},
        {"id": "moonshot", "name": "月之暗面", "enabled": True,
         "models": [{"name": "kimi-k2.6", "enabled": True, "priority": 10}]},
    ],
    "bindings": {},
}, ensure_ascii=False), encoding="utf-8")

w1 = paths.DATA_DIR / "workspace" / "board-t1"
w2 = paths.DATA_DIR / "workspace" / "board-t2"
w3 = paths.DATA_DIR / "workspace" / "board-t3"
w4 = paths.DATA_DIR / "workspace" / "board-t4"
for w in (w1, w2, w3, w4):
    w.mkdir(parents=True, exist_ok=True)
(w1 / "chapter-39.md").write_text("# 第 39 章（起草中）\n", encoding="utf-8")

tasks = [
    {"id": "t-board-1", "type": "serial_novel", "engine": "review",
     "title": "《蜂巢纪元》第 39 章起草与评审", "goal": "连载第 39 章：蜂群叛乱前夜",
     "workdir": str(w1), "status": "running", "archived": False,
     "mode": "auto", "created_at": ts(1800)},
    {"id": "t-board-2", "type": "research", "engine": "review",
     "title": "禅道 Bug 闭环竞品专项调研", "goal": "调研竞品的 bug 自动修复闭环",
     "workdir": str(w2), "status": "running", "archived": False,
     "mode": "auto", "created_at": ts(420)},
    {"id": "t-board-3", "type": "code", "engine": "code",
     "title": "修复禅道 #27697 计划锚路径误报", "goal": "排查并修复误报",
     "workdir": str(w3), "status": "queued", "archived": False,
     "mode": "auto", "created_at": ts(60)},
    {"id": "t-board-5", "type": "article", "engine": "review",
     "title": "知乎回答《智能体编排的护城河是什么》", "goal": "一篇回答",
     "workdir": str(w4), "status": "queued", "archived": False,
     "mode": "auto", "created_at": ts(120)},
    {"id": "t-board-6", "type": "translation", "engine": "review",
     "title": "英文版产品手册第二章翻译", "goal": "翻译并保持术语一致",
     "workdir": str(w3), "status": "queued", "archived": False,
     "mode": "fast", "created_at": ts(180)},
    {"id": "t-board-7", "type": "research", "engine": "review",
     "title": "短视频赛道周榜观察与选题建议", "goal": "每周例行调研",
     "workdir": str(w2), "status": "queued", "archived": False,
     "mode": "auto", "created_at": ts(240)},
    {"id": "t-board-8", "type": "doc", "engine": "review",
     "title": "API 接入文档补全示例代码", "goal": "文档补全",
     "workdir": str(w4), "status": "queued", "archived": False,
     "mode": "auto", "created_at": ts(300)},
    {"id": "t-board-4", "type": "article", "engine": "review",
     "title": "公众号文章《多智能体的回声》", "goal": "一篇深度稿",
     "workdir": str(w4), "status": "done", "archived": False,
     "mode": "auto", "created_at": ts(5400)},
]
for t in tasks:
    (paths.TASKS_DIR / (t["id"] + ".json")).write_text(
        json.dumps(t, ensure_ascii=False), encoding="utf-8")


_MODEL_OF = {"codex-cli": "glm-5.3", "claude-code": "claude-sonnet-4-5",
             "kimi-cli": "kimi-k2.6"}


def step(n, label, status, summary, ago, dur=95.0, model="", live_tail="",
         thinking_only=False):
    s = {"n": n, "role": "impl" if n % 2 else "review", "agent": label,
         "agent_label": label, "note": "", "model": model or _MODEL_OF.get(label, "glm-5.3"),
         "status": status, "started_at": hm(ago),
         "ended_at": None if status == "running" else hm(ago - dur),
         "duration_s": None if status == "running" else dur,
         "exit_code": None if status == "running" else 0,
         "summary": summary, "log": None, "cost_usd": 0.0, "tokens": 0}
    if status == "running":
        if not thinking_only:
            body = live_tail or ("正文第 %d 段：蜂群在暮色里列阵，引擎的低鸣像远雷。%s" % (n, "嗡" * 40))
            s["stream"] = ("\n".join(body for _ in range(11)))[-4000:]
        s["thinking"] = ("\n".join(
            (live_tail or ("评审视角：先压抑后爆发，第 %d 节的转场是否生硬。%s" % (n, "。" * 50)))
            for _ in range(8)))[-4000:]
        s["live"] = 7
    return s


def make_run(rid, task_id, title, status, steps, ago_created, ago_started=None,
             ago_ended=None, error="", run_tokens=0, run_cost=0.0):
    run = {"id": rid, "kind": "orchestration", "title": title, "task_id": task_id,
           "entry_id": None, "op": None, "status": status, "steps": steps,
           "messages": [], "created_at": ts(ago_created),
           "started_at": ts(ago_started if ago_started is not None else ago_created),
           "ended_at": ts(ago_ended) if ago_ended else None,
           "cost_usd": run_cost, "tokens": run_tokens, "error": error,
           "verdict": None, "summary": "",
           "estimated_duration_s": 2400 if rid.endswith("0001") else None}
    d = paths.RUNS_DIR / rid
    (d / "steps").mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    return run


# t1：4 步（3 done + 1 running），已跑 22 分钟，预估 40 分钟
# running 步骤带完整评审输出流（大屏「输出」窗）+ 本次累计消耗
make_run("r-board-230000-0001", "t-board-1", "《蜂巢纪元》第 39 章起草与评审",
         "running",
         [step(1, "codex-cli", "done", "大纲已通过：三幕结构，反转点落在第 4 节", 1300, 210),
          step(2, "claude-code", "done", "前情摘要注入完成，一致性校验通过", 1050, 160),
          step(3, "codex-cli", "done", "第 39 章正文草稿已落盘 chapter-39.md", 800, 430),
          step(4, "claude-code", "running", "正在逐节评审：第 3 节转场略生硬", 300,
               live_tail="第 3 节转场评语：从蜂巢议事厅到荒原的切得过快，建议补一段暮色行军过渡；第 4 节反转铺垫到位，反转点保留。")],
         1320, 1320, run_tokens=18420, run_cost=0.042)

# t2：2 步（1 done + 1 running），已跑 6 分钟；running 只有思考流（演示「思考」窗）
make_run("r-board-230000-0002", "t-board-2", "禅道 Bug 闭环竞品专项调研",
         "running",
         [step(1, "codex-cli", "done", "检索到 5 个竞品的 bug 修复自动化方案", 340, 180),
          step(2, "kimi-cli", "running", "正在对比各家的转派状态机设计", 120,
               thinking_only=True,
               live_tail="对比维度先列转派状态机的关键差异：竞品 B 的 claim 钉死领单 run_id，重试换 run 后对账不知情会误报失败——这正是我们踩过的坑，写进对比表第三列。")],
         400, 400, None, "", run_tokens=6280, run_cost=0.017)
# t2 的预估给短一点，展示「进度≈」行
p2 = paths.RUNS_DIR / "r-board-230000-0002" / "run.json"
r2 = json.loads(p2.read_text(encoding="utf-8"))
r2["estimated_duration_s"] = 900
p2.write_text(json.dumps(r2, ensure_ascii=False), encoding="utf-8")

# t3：排队 + 另 4 个排队（演示排队条横向滚动）
for i, (rid, tid, ttl, ago) in enumerate([
        ("r-board-230000-0003", "t-board-3", "修复禅道 #27697 计划锚路径误报", 60),
        ("r-board-230000-0005", "t-board-5", "知乎回答《智能体编排的护城河是什么》", 120),
        ("r-board-230000-0006", "t-board-6", "英文版产品手册第二章翻译", 180),
        ("r-board-230000-0007", "t-board-7", "短视频赛道周榜观察与选题建议", 240),
        ("r-board-230000-0008", "t-board-8", "API 接入文档补全示例代码", 300)]):
    make_run(rid, tid, ttl, "queued", [], ago)

# t4：今天 40 分钟前完成，耗时 21 分钟
make_run("r-board-230000-0004", "t-board-4", "公众号文章《多智能体的回声》",
         "done",
         [step(1, "codex-cli", "done", "提纲与素材齐备", 2700, 260),
          step(2, "claude-code", "done", "成稿 3200 字，三处金句已加粗", 2440, 620),
          step(3, "codex-cli", "done", "标题 A/B 两版已生成", 1820, 90)],
         2760, 2700, 1470)

# 今日失败一条（挂在 t4 名下演示失败行）+ 昨天完成一条（不进今日计数）
make_run("r-board-220000-0005", "t-board-3", "七猫建书·都市频道",
         "failed",
         [step(1, "codex-cli", "failed", "上游网关 503：provider unavailable", 9100, 45)],
         9150, 9100, 9050, "上游网关 503：provider unavailable")
make_run("r-board-220000-0006", "t-board-4", "昨日连载第 38 章定稿",
         "done",
         [step(1, "codex-cli", "done", "第 38 章定稿并归档", 86400 - 400, 380)],
         86400, 86400 - 400, 86400 - 20)

# 3) 近 7 日 usage 台账（今天密集 + 前几天递增曲线；一行一对象正序） ----
line = paths.DATA_DIR / "usage" / "usage-202609.jsonl"
line.parent.mkdir(parents=True, exist_ok=True)
rows = []
for d in range(6, 0, -1):
    day_ts = NOW - d * 86400
    day = time.strftime("%Y-%m-%d", time.localtime(day_ts))
    base = 40000 + (6 - d) * 26000
    for i, (model, share) in enumerate((("glm-5.3", .5), ("claude-sonnet-4-5", .3), ("deepseek-v4", .2))):
        rows.append({"ts": day + " 1%d:0%d:00" % (i, i), "day": day, "source": "pipeline",
                     "run_id": "", "step": 1, "task_id": "", "task_type": "research",
                     "role": "impl", "agent": "codex-cli", "agent_label": "codex-cli",
                     "tool": "codex", "model": model, "provider": "", "ok": True,
                     "duration_s": 120.0, "cost_usd": round(base * share / 1e6 * 3, 4),
                     "input": int(base * share * .7), "output": int(base * share * .3),
                     "cached": 0, "reasoning": 0, "total": int(base * share)})
today = time.strftime("%Y-%m-%d")
for i, (model, share) in enumerate((("glm-5.3", .45), ("claude-sonnet-4-5", .35), ("kimi-k2.6", .2))):
    base = 52000
    rows.append({"ts": today + " 0%d:1%d:00" % (9 + i, i), "day": today, "source": "pipeline",
                 "run_id": "", "step": 1, "task_id": "", "task_type": "serial_novel",
                 "role": "impl", "agent": "codex-cli", "agent_label": "codex-cli",
                 "tool": "codex", "model": model, "provider": "", "ok": True,
                 "duration_s": 180.0, "cost_usd": round(base * share / 1e6 * 3, 4),
                 "input": int(base * share * .7), "output": int(base * share * .3),
                 "cached": 0, "reasoning": 0, "total": int(base * share)})
with open(line, "a", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

print("[seed-board] tasks=%d runs=6 usage_rows=%d data=%s"
      % (len(tasks), len(rows), paths.DATA_DIR), flush=True)

# 4) 同进程起服务（收尸已 patch，running 能活下来）--------------------
sys.argv = ["main.py", "--port", sys.argv[1] if len(sys.argv) > 1 else "18977",
            "--no-browser"]
import main  # noqa: E402
main.main()
