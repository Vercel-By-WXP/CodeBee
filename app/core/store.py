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

# ---------------------------------------------------------------- 状态版本（SSE 事件驱动）
# 任何落盘写都算状态变化：SSE 连接等版本号变化才构建/推送全量状态，
# 空闲时连接零开销（此前每连接每 0.8s 盲构建全量 payload，多端并发会放大成假死）
_VER_CV = threading.Condition()
_STATE_VER = 1


def bump_state():
    global _STATE_VER
    with _VER_CV:
        _STATE_VER += 1
        _VER_CV.notify_all()


def state_version():
    return _STATE_VER


def wait_state_change(last_ver, timeout):
    """阻塞直到版本号超过 last_ver 或超时。返回当前版本号。"""
    with _VER_CV:
        if _STATE_VER != last_ver:
            return _STATE_VER
        _VER_CV.wait(timeout)
        return _STATE_VER


def _new_id(prefix):
    return "%s-%s-%04d" % (prefix, time.strftime("%Y%m%d-%H%M%S"), secrets.randbelow(10000))


def _safe_name(s):
    return re.sub(r"[^0-9A-Za-z_-]+", "-", str(s))[:40].strip("-") or "x"


# ---------------------------------------------------------------- 任务

def create_task(payload):
    """校验并创建任务。payload 至少含 type/goal/workdir。

    type 必须是 flows.py 里的有效流程 ID；流程参数（引擎/维度/阈值/轮数/产出
    文件/提示词覆盖）在创建时固化到任务上，之后修改流程定义不影响已建任务。
    """
    from . import flows as flows_mod
    flow = flows_mod.get_flow(payload.get("type"))
    if flow is None:
        raise ValueError("未知任务类型：%s（可选：%s）"
                         % (payload.get("type"), "、".join(f["id"] for f in flows_mod.list_flows())))
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
        "id": _new_id("t"), "type": flow["id"], "engine": flow["engine"],
        "title": title, "goal": goal,
        "context": (payload.get("context") or "").strip(),
        "workdir": str(wd),
        "mode": mode,
        "difficulty": difficulty,
        "implementer": payload.get("implementer") or "",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "created",
    }
    if flow["engine"] == "code":
        task["verify_command"] = (payload.get("verify_command") or "").strip()
    else:
        ms = (payload.get("manuscript") or flow.get("manuscript") or "manuscript.md").strip()
        ms = re.sub(r"[\\/]", "_", ms)  # 只允许工作目录内的相对文件名
        ms = re.sub(r"\.{2,}", "_", ms).lstrip(".")  # 顺带清掉残留的 ..
        task["manuscript"] = ms
        try:
            task["rounds"] = max(1, min(5, int(payload.get("rounds") or flow.get("rounds") or 2)))
        except Exception:
            task["rounds"] = 2
        try:
            task["threshold"] = max(1.0, min(10.0,
                                             float(payload.get("threshold") or flow.get("threshold") or 7.0)))
        except Exception:
            task["threshold"] = 7.0
        dims = payload.get("rubric")
        if not (isinstance(dims, list) and dims):
            dims = flow.get("rubric")
        if isinstance(dims, list) and dims:
            task["rubric"] = [str(d).strip() for d in dims if str(d).strip()][:8]
        for key in ("draft_prompt", "critique_prompt"):  # 自定义流程的提示词覆盖
            if flow.get(key):
                task[key] = flow[key]
        # 连载模式：逐章起草/评审/修订（任务级 serial 覆盖流程默认）
        serial = payload.get("serial") if isinstance(payload.get("serial"), dict) else flow.get("serial")
        if isinstance(serial, dict) and serial.get("chapters"):
            try:
                task["serial"] = {
                    "chapters": max(2, min(20, int(serial["chapters"]))),
                    "words_per_chapter": max(500, min(8000,
                                                      int(serial.get("words_per_chapter") or 2500))),
                }
            except Exception:
                pass
    critics = payload.get("critics")
    if isinstance(critics, list) and critics:
        task["critics"] = [str(c) for c in critics]
    resume = payload.get("resume")
    if isinstance(resume, dict) and resume.get("agent") and resume.get("session"):
        task["resume"] = {"agent": str(resume["agent"])[:40],
                          "session": str(resume["session"])[:80],
                          "preview": str(resume.get("preview") or "")[:140]}
        # 会话所属项目目录：续会话时 CLI 必须在该目录下启动（opencode/qwen 按 cwd 定位会话）
        proj = str(resume.get("project") or "")[:260]
        if proj:
            task["resume"]["project"] = proj
    with LOCK:
        _TASKS[task["id"]] = task
        _save_json(paths.TASKS_DIR / (task["id"] + ".json"), task)
    return task


def _save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    bump_state()


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


def latest_run_by_task():
    """每个任务的最近一次运行（含 steps），全量扫描不受 list_runs 窗口限制。

    侧栏靠它给每个任务展示真实近况；从未运行过的任务不在返回值里。
    run id 含时间戳，字典序即新旧序。
    """
    with LOCK:
        best = {}
        for r in _RUNS.values():
            tid = r.get("task_id")
            if not tid:
                continue
            cur = best.get(tid)
            if cur is None or r["id"] > cur["id"]:
                best[tid] = r
        return {tid: dict(r) for tid, r in best.items()}


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
    bump_state()
    return True, ""


def recover_orphaned_runs():
    """启动时调用：把上一轮进程崩溃遗留的 status='running' run 标记为 failed。

    设计稿：docs/migration/01-defense-patterns.md §1E。
    参考 dsh docs/subsystems/persistence.zh.md:108-114 interruptedTurnClosers。
    调用时机：load_all 之后，启动 HTTP 服务之前。
    返回恢复的 run 数。
    """
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOCK:
        # status=running 视为崩溃遗留（包括 ended_at 已设的异常状态，统一兜底）
        candidates = [r["id"] for r in _RUNS.values() if r.get("status") == "running"]
    recovered = 0
    for run_id in candidates:
        # update_run 内部再次加 LOCK（RLock 允许重入），并把终态回填到 task
        update_run(run_id,
                   status="failed",
                   ended_at=now,
                   error="interrupted at startup (auto-recovered)")
        recovered += 1
    if recovered:
        bump_state()
    return recovered


def run_workdir(run_id):
    """该 run 的工作目录（经其 task 关联）；无任务的 run（如管理操作）返回空串。"""
    run = get_run(run_id)
    if not run:
        return ""
    task = get_task(run.get("task_id") or "") if run.get("task_id") else None
    return str(task.get("workdir") or "") if task else ""


_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode"}


def run_artifacts(run_id, limit=50):
    """列一次 run 的「成品文件」：工作目录里在该 run 开始之后新产生/修改过的文件。

    返回 (workdir, files)；files 按 mtime 新→旧，name 为工作目录内相对路径，
    已跳过 .git / node_modules 等噪音目录，最多 limit 个。
    """
    wd = run_workdir(run_id)
    if not wd:
        return "", []
    run = get_run(run_id) or {}
    t0 = str(run.get("started_at") or run.get("created_at") or "")
    try:
        t0 = time.mktime(time.strptime(t0, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        t0 = 0
    root = Path(wd)
    if not root.is_dir():
        return wd, []
    files = []
    try:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_mtime < t0:
                continue
            files.append({"name": str(p.relative_to(root)).replace("\\", "/"),
                          "size": st.st_size, "mtime": int(st.st_mtime)})
            if len(files) >= 400:  # 防超大目录拖垮接口；截断后再排序取最新
                break
    except OSError:
        pass
    files.sort(key=lambda f: -f["mtime"])
    return wd, files[:limit]


def read_run_file(run_id, rel):
    """读取 run 工作目录内的一个文件（防目录穿越）。返回 (bytes, 错误)。"""
    wd = run_workdir(run_id)
    if not wd:
        return b"", "该运行没有关联的工作目录"
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if not rel:
        return b"", "缺少文件名"
    root = Path(wd).resolve()
    p = (root / rel).resolve()
    if root != p and root not in p.parents:
        return b"", "非法路径"
    if not p.is_file():
        return b"", "文件不存在"
    try:
        return p.read_bytes(), None
    except OSError as e:
        return b"", "读取失败: %s" % e


def delete_runs(run_ids):
    """批量删除运行记录。跳过排队中/运行中的记录。

    返回 (删除数, 跳过数, 错误信息)：非法 ID 与运行中的记录都计入跳过，
    合法且已结束的记录照常删除，不因个别非法 ID 整体失败。
    """
    if not isinstance(run_ids, (list, tuple)):
        return 0, 0, "ids 必须是数组"
    deleted, skipped, bad = 0, 0, 0
    for run_id in run_ids:
        rid = str(run_id)
        if not re.match(r"^[A-Za-z][0-9A-Za-z_-]*$", rid):
            bad += 1
            continue
        with LOCK:
            run = _RUNS.get(rid)
            if not run or run.get("status") in ("queued", "running"):
                skipped += 1
                continue
            del _RUNS[rid]
        shutil.rmtree(paths.RUNS_DIR / rid, ignore_errors=True)
        deleted += 1
    if deleted:
        bump_state()
    return deleted, skipped, ("有 %d 条非法记录 ID" % bad) if bad else ""


def clear_runs():
    """一键清除全部运行记录（跳过排队中/运行中的）。返回 (删除数, 跳过数)。"""
    with LOCK:
        targets, skipped = [], 0
        for rid, r in _RUNS.items():
            if r.get("status") in ("queued", "running"):
                skipped += 1
            else:
                targets.append(rid)
        for rid in targets:
            del _RUNS[rid]
    for rid in targets:
        shutil.rmtree(paths.RUNS_DIR / rid, ignore_errors=True)
    bump_state()
    return len(targets), skipped


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
    bump_state()
    return True, ""


def retry_task(task_id):
    """手动重试：为失败/已取消的任务再创建一次新运行。返回 (ok, 错误, run)。

    连载任务断点续跑：若上一次运行已产出大纲与部分章节成稿，新运行继承
    （inherit）大纲与已完成章号——流水线跳过这些章的起草（成稿/评审分数
    直接复用或重评），避免几十万字长篇因一次超时全部重来。
    """
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
        if task.get("serial"):
            prev_runs = sorted((r for r in _RUNS.values()
                                if r.get("task_id") == task_id and r["id"] != run["id"]),
                               key=lambda r: r["id"], reverse=True)
            for prev in prev_runs:
                outline = prev.get("outline")
                # 降级大纲没有真实情节，继承只会让每一遍都按空模板写废——
                # 跳过它，让新运行重新生成（编排者恢复后即可拿到真大纲）。
                # mock 模板的 outline 无 degraded 标记，正常继承不受影响。
                if not outline or outline.get("degraded"):
                    continue
                done = sorted({int(s["role"].split("c")[-1])
                               for s in (prev.get("steps") or [])
                               if s.get("status") == "done"
                               and (s.get("role") or "").startswith("draft-c")
                               and str(s.get("role")).split("c")[-1].isdigit()})
                if done:
                    run["inherit"] = {
                        "outline": outline,
                        "done_chapters": done,
                        "chapter_scores": ((prev.get("verdict") or {}).get("chapter_scores") or []),
                    }
                    break
        task["status"] = "queued"
        _save_json(paths.TASKS_DIR / (task_id + ".json"), task)
        _save_json(paths.RUNS_DIR / run["id"] / "run.json", run)
    return True, "", run


def rename_task(task_id, title):
    """重命名任务：同步更新任务与全部运行记录的标题。

    侧栏任务树按 run.title 显示组名，只改任务会让历史运行仍顶着旧名，
    所以两者一起改（运行 json 逐个回写）。
    """
    if not re.match(r"^[A-Za-z][0-9A-Za-z_-]*$", str(task_id)):
        return False, "非法的任务 ID"
    title = str(title or "").strip()
    if not title:
        return False, "标题不能为空"
    if len(title) > 120:
        return False, "标题过长（最多 120 字）"
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False, "任务不存在"
        if task.get("title") == title:
            return True, ""
        task["title"] = title
        _save_json(paths.TASKS_DIR / (task_id + ".json"), task)
        for r in _RUNS.values():
            if r.get("task_id") == task_id:
                r["title"] = title
                _save_json(paths.RUNS_DIR / r["id"] / "run.json", r)
        bump_state()
    return True, ""


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
                cost_usd=0.0, tokens=0.0, duration_s=None, model=None):
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
                if model:
                    s["model"] = str(model)[:80]
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
    bump_state()
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
