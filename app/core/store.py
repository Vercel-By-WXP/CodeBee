# -*- coding: utf-8 -*-
"""存储层：任务与运行（含管理操作）全部落盘，可回放。

目录约定：
  data/tasks/<task_id>.json
  data/runs/<run_id>/run.json
  data/runs/<run_id>/steps/<NN>-<role>-<agent>.log
  data/runs/<run_id>/report.md
"""
from __future__ import annotations

import json
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

from . import paths, runner

LOCK = threading.RLock()
_TASKS = {}
_RUNS = {}


def _new_id(prefix):
    return "%s-%s-%04d" % (prefix, time.strftime("%Y%m%d-%H%M%S"), secrets.randbelow(10000))


def _safe_name(s):
    return re.sub(r"[^0-9A-Za-z_-]+", "-", str(s))[:40].strip("-") or "x"


# ---------------------------------------------------------------- 任务

def create_task(payload):
    """校验并创建任务。payload 至少含 type/title/goal/workdir。"""
    ttype = payload.get("type")
    if ttype not in ("code", "novel"):
        raise ValueError("type 必须是 code 或 novel")
    title = (payload.get("title") or "").strip()
    goal = (payload.get("goal") or "").strip()
    workdir = (payload.get("workdir") or "").strip()
    if not goal:
        raise ValueError("目标描述不能为空")
    title = title or goal.splitlines()[0][:30]  # 标题可省略，自动取目标首行
    if not workdir:
        raise ValueError("工作目录不能为空")
    wd = Path(workdir)
    if not wd.is_absolute():
        raise ValueError("工作目录必须是绝对路径")
    if not wd.is_dir():
        raise ValueError("工作目录不存在: %s" % workdir)
    mode = payload.get("mode")
    if mode not in ("auto", "manual"):
        mode = "manual" if payload.get("implementer") else "auto"
    difficulty = payload.get("difficulty")
    if difficulty not in ("auto", "easy", "hard", "default"):
        difficulty = "auto"
    task = {
        "id": _new_id("t"), "type": ttype, "title": title, "goal": goal,
        "context": (payload.get("context") or "").strip(),
        "workdir": str(wd),
        "mode": mode,
        "difficulty": difficulty,
        "implementer": payload.get("implementer") or "",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "created",
    }
    if ttype == "code":
        task["verify_command"] = (payload.get("verify_command") or "").strip()
    else:
        ms = (payload.get("manuscript") or "manuscript.md").strip()
        ms = re.sub(r"[\\/]", "_", ms)  # 只允许工作目录内的相对文件名
        ms = re.sub(r"\.{2,}", "_", ms).lstrip(".")  # 顺带清掉残留的 ..
        task["manuscript"] = ms
        try:
            task["rounds"] = max(1, min(5, int(payload.get("rounds") or 2)))
        except Exception:
            task["rounds"] = 2
        try:
            task["threshold"] = max(1.0, min(10.0, float(payload.get("threshold") or 7.0)))
        except Exception:
            task["threshold"] = 7.0
        dims = payload.get("rubric")
        if isinstance(dims, list) and dims:
            task["rubric"] = [str(d).strip() for d in dims if str(d).strip()][:8]
    critics = payload.get("critics")
    if isinstance(critics, list) and critics:
        task["critics"] = [str(c) for c in critics]
    resume = payload.get("resume")
    if isinstance(resume, dict) and resume.get("agent") and resume.get("session"):
        task["resume"] = {"agent": str(resume["agent"])[:40],
                          "session": str(resume["session"])[:80],
                          "preview": str(resume.get("preview") or "")[:140]}
    with LOCK:
        _TASKS[task["id"]] = task
        _save_json(paths.TASKS_DIR / (task["id"] + ".json"), task)
    return task


def _save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def update_task_status(task_id, status):
    with LOCK:
        task = _TASKS.get(task_id)
        if task:
            task["status"] = status
            _save_json(paths.TASKS_DIR / (task_id + ".json"), task)


def get_task(task_id):
    with LOCK:
        if task_id in _TASKS:
            return _TASKS[task_id]
    p = paths.TASKS_DIR / (task_id + ".json")
    if p.is_file():
        try:
            t = json.loads(p.read_text(encoding="utf-8"))
            _TASKS[task_id] = t
            return t
        except Exception:
            return None
    return None


def list_tasks(limit=100, archived=None):
    """archived=None 返回全部；False 仅未归档；True 仅已归档。按 id（含时间戳）倒序。"""
    with LOCK:
        ids = sorted(_TASKS.keys(), reverse=True)
        out = []
        for i in ids:
            t = _TASKS[i]
            if archived is not None and bool(t.get("archived")) != archived:
                continue
            out.append(t)
            if len(out) >= limit:
                break
        return out


def load_all():
    with LOCK:
        for p in paths.TASKS_DIR.glob("*.json"):
            try:
                t = json.loads(p.read_text(encoding="utf-8"))
                _TASKS[t["id"]] = t
            except Exception:
                pass
        for p in paths.RUNS_DIR.glob("*/run.json"):
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
                r.pop("cancel_event", None)
                # 队列不跨进程持久化：磁盘上仍是 queued/running 的运行必是上次进程中断的残骸
                if r.get("status") in ("queued", "running"):
                    r["status"] = "failed"
                    r["error"] = r.get("error") or "服务重启中断，可重试"
                    _save_json(p, r)
                _RUNS[r["id"]] = r
            except Exception:
                pass
        # 回填历史遗留：运行已终态而任务仍停在 created/queued/running 的脏状态
        for t in _TASKS.values():
            if t.get("status") not in ("created", "queued", "running"):
                continue
            runs = [r for r in _RUNS.values() if r.get("task_id") == t["id"]]
            if not runs:
                # 卡在排队却从未有运行：创建流程被中断，标记失败允许重试
                t["status"] = "failed"
                t["error"] = "没有运行记录（创建可能被中断），可重试"
                _save_json(paths.TASKS_DIR / (t["id"] + ".json"), t)
                continue
            latest = max(runs, key=lambda r: r["id"])
            if latest.get("status") in ("done", "failed", "cancelled"):
                t["status"] = latest["status"]
                _save_json(paths.TASKS_DIR / (t["id"] + ".json"), t)


# ---------------------------------------------------------------- 运行（含管理操作）

def create_run(kind, title, task_id=None, entry_id=None, op=None):
    run = {
        "id": _new_id("r" if kind == "orchestration" else "m"),
        "kind": kind,  # orchestration | mgmt
        "title": title,
        "task_id": task_id, "entry_id": entry_id, "op": op,
        "status": "queued", "steps": [],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "started_at": None, "ended_at": None,
        "cost_usd": 0.0, "tokens": 0, "error": "",
        "verdict": None, "summary": "",
    }
    rdir = paths.RUNS_DIR / run["id"]
    (rdir / "steps").mkdir(parents=True, exist_ok=True)
    with LOCK:
        _RUNS[run["id"]] = run
        _save_json(rdir / "run.json", run)
    return run


def get_run(run_id):
    with LOCK:
        return _RUNS.get(run_id)


def list_runs(limit=60):
    with LOCK:
        ids = sorted(_RUNS.keys(), reverse=True)
        return [_RUNS[i] for i in ids[:limit]]


def update_run(run_id, **fields):
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return None
        run.update(fields)
        _save_json(paths.RUNS_DIR / run_id / "run.json", run)
        # 终态回填：运行结束（成功/失败/取消）时同步任务状态，否则任务永远停在 queued
        st = fields.get("status")
        if st in ("done", "failed", "cancelled"):
            tid = run.get("task_id")
            task = _TASKS.get(tid) if tid else None
            if task:
                task["status"] = st
                _save_json(paths.TASKS_DIR / (tid + ".json"), task)
        return run


def run_dir(run_id):
    return paths.RUNS_DIR / run_id


def delete_run(run_id):
    """删除一条运行记录（内存 + 磁盘目录）。返回 (ok, 错误信息)。"""
    if not re.match(r"^[A-Za-z][0-9A-Za-z_-]*$", str(run_id)):
        return False, "非法的记录 ID"
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return False, "记录不存在"
        if run.get("status") in ("queued", "running"):
            return False, "运行中的记录不能删除，请先取消"
        del _RUNS[run_id]
    shutil.rmtree(paths.RUNS_DIR / run_id, ignore_errors=True)
    return True, ""


def archive_task(task_id, archived=True):
    """归档/取消归档：归档后从默认列表与侧栏隐藏，数据保留，可随时恢复。"""
    if not re.match(r"^[A-Za-z][0-9A-Za-z_-]*$", str(task_id)):
        return False, "非法的任务 ID"
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False, "任务不存在"
        if archived:
            if task.get("status") in ("queued", "running"):
                return False, "运行中的任务不能归档，请先取消"
            for r in _RUNS.values():
                if r.get("task_id") == task_id and r.get("status") in ("queued", "running"):
                    return False, "有运行中的记录，请先取消"
        task["archived"] = bool(archived)
        _save_json(paths.TASKS_DIR / (task_id + ".json"), task)
    return True, ""


def delete_task(task_id):
    """删除任务及其全部运行记录（含日志与报告目录）。返回 (ok, 错误信息)。"""
    if not re.match(r"^[A-Za-z][0-9A-Za-z_-]*$", str(task_id)):
        return False, "非法的任务 ID"
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False, "任务不存在"
        if task.get("status") in ("queued", "running"):
            return False, "运行中的任务不能删除，请先取消"
        run_ids = []
        for rid, r in _RUNS.items():
            if r.get("task_id") != task_id:
                continue
            if r.get("status") in ("queued", "running"):
                return False, "有运行中的记录，请先取消"
            run_ids.append(rid)
        del _TASKS[task_id]
        for rid in run_ids:
            _RUNS.pop(rid, None)
    for rid in run_ids:
        shutil.rmtree(paths.RUNS_DIR / rid, ignore_errors=True)
    (paths.TASKS_DIR / (task_id + ".json")).unlink(missing_ok=True)
    return True, ""


def retry_task(task_id):
    """手动重试：为失败/已取消的任务再创建一次新运行。返回 (ok, 错误, run)。"""
    if not re.match(r"^[A-Za-z][0-9A-Za-z_-]*$", str(task_id)):
        return False, "非法的任务 ID", None
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False, "任务不存在", None
        for r in _RUNS.values():
            if r.get("task_id") == task_id and r.get("status") in ("queued", "running"):
                return False, "任务仍在运行中，不能重试", None
        run = create_run("orchestration", task.get("title") or task_id, task_id=task_id)
        task["status"] = "queued"
        _save_json(paths.TASKS_DIR / (task_id + ".json"), task)
    return True, "", run


def add_step(run_id, role, agent_id, agent_label, note=""):
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return None, None
        n = len(run["steps"]) + 1
        step = {
            "n": n, "role": role, "agent": agent_id, "agent_label": agent_label,
            "note": note,
            "status": "running", "started_at": time.strftime("%H:%M:%S"),
            "ended_at": None, "duration_s": None, "exit_code": None,
            "summary": "", "log": None, "cost_usd": 0.0, "tokens": 0,
        }
        run["steps"].append(step)
        log_rel = "steps/%02d-%s-%s.log" % (n, _safe_name(role), _safe_name(agent_id))
        step["log"] = log_rel
        _save_json(paths.RUNS_DIR / run_id / "run.json", run)
        log_abs = paths.RUNS_DIR / run_id / log_rel
        log_abs.parent.mkdir(parents=True, exist_ok=True)
        return step, log_abs


def finish_step(run_id, n, status, summary="", exit_code=None,
                cost_usd=0.0, tokens=0.0, duration_s=None):
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return
        for s in run["steps"]:
            if s["n"] == n:
                s["status"] = status
                s["ended_at"] = time.strftime("%H:%M:%S")
                s["summary"] = summary
                s["exit_code"] = exit_code
                s["cost_usd"] = round(cost_usd, 4)
                s["tokens"] = tokens
                if duration_s is not None:
                    s["duration_s"] = round(duration_s, 1)
                break
        run["cost_usd"] = round(run.get("cost_usd", 0.0) + cost_usd, 4)
        run["tokens"] = run.get("tokens", 0) + tokens
        _save_json(paths.RUNS_DIR / run_id / "run.json", run)


def write_report(run_id, markdown):
    p = paths.RUNS_DIR / run_id / "report.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(markdown, encoding="utf-8")
    with LOCK:
        run = _RUNS.get(run_id)
        if run:
            run["report"] = "report.md"
    return p


def read_step_log(run_id, rel_path, tail=paths.LOG_TAIL_CHARS):
    p = (paths.RUNS_DIR / run_id / rel_path).resolve()
    try:
        # 防目录穿越：必须落在本 run 目录内
        if paths.RUNS_DIR.resolve() not in p.parents:
            return ""
        data = p.read_bytes()
        if len(data) > tail:
            return "...(已截断)...\n" + runner.decode_output(data[-tail:])
        return runner.decode_output(data)
    except Exception:
        return ""
