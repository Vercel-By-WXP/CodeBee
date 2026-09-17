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
import os
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
        # 未指定目录 → 用「默认保存路径」（设置里可改；内置回落 <data 同级>/workspace）。
        # 默认路径允许自动创建；用户显式给的目录仍必须已存在。
        from . import settings as settings_mod
        workdir = settings_mod.default_workdir()
        wd = Path(workdir)
        try:
            wd.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise ValueError("默认保存路径不可用: %s（%s）" % (workdir, e))
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
        "attachments": [],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "created",
    }
    # 代码版本：仅当引用合法才固化（流水线执行前据此检出任务分支）
    from . import gitmod
    git_rev = (payload.get("git_rev") or "").strip()
    if git_rev:
        # 前端下拉值带 kind 前缀（branch:main / tag:v1 / commit:abc），此处归一为纯 rev；
        # git 分支/标签名本身允许含冒号（罕见），前缀剥离只认这三种已知 kind
        git_rev = re.sub(r"^(branch|tag|commit):", "", git_rev)
        if not gitmod.valid_rev(git_rev):
            raise ValueError("非法的代码版本引用：%s" % git_rev[:40])
        task["git_rev"] = git_rev
    if flow["engine"] == "code":
        task["verify_command"] = (payload.get("verify_command") or "").strip()
    elif flow["engine"] == "direct":
        pass  # 直连任务：无验证命令也无评审参数，目标+附件即全部输入
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
                s = {
                    # 续写批次允许只续 1 章，下限放宽到 1（全新连载仍由前端约束 ≥2）
                    "chapters": max(1, min(20, int(serial["chapters"]))),
                    "words_per_chapter": max(500, min(8000,
                                                      int(serial.get("words_per_chapter") or 2500))),
                }
            except Exception:
                s = None
            if s:
                # 续写：从 start_chapter 章接着写（章节文件/步骤/评分都用全书章号）；
                # continues 指向上一批任务，生成大纲时回溯前情保证剧情衔接
                try:
                    sc = max(1, min(500, int(serial.get("start_chapter") or 1)))
                except Exception:
                    sc = 1
                if sc > 1:
                    s["start_chapter"] = sc
                cont = str(serial.get("continues") or "").strip()
                if re.match(r"^[A-Za-z][0-9A-Za-z_-]*$", cont) and get_task(cont):
                    s["continues"] = cont
                # 同章多稿赛马（dev-3.0）：1=关；2-3 = 每章并行起草 N 稿评审择优
                try:
                    v = max(1, min(3, int(serial.get("variants") or 1)))
                except Exception:
                    v = 1
                if v > 1:
                    s["variants"] = v
                task["serial"] = s
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
    # 附件：把待提交文件移入工作目录 _attachments/，清单注入 context（__CONTEXT__ 全链路可见）。
    # 两种形态：字符串 id = 待提交区文件（新建任务）；dict 清单 = 已落盘的附件
    # （继续连载/重试沿用同目录同文件，直接复制清单，不再移文件）。
    att_ids = payload.get("attachments")
    if isinstance(att_ids, list) and att_ids:
        from . import attachments as att_mod
        items = [a for a in att_ids if isinstance(a, dict) and a.get("path")]
        if not items:  # 纯 id 形态 → 从待提交区移入工作目录
            try:
                items = att_mod.commit_to_workdir(
                    str(wd), [str(x) for x in att_ids][:att_mod.MAX_FILES])
            except Exception as e:
                raise ValueError("附件落盘失败: %s" % e)
        if items:
            task["attachments"] = items
            blk = att_mod.context_block(items)
            # 复制清单场景下 context 已含附件块（随旧任务沿用），别重复追加
            if blk and "## 附件材料" not in task["context"]:
                task["context"] = (task["context"] + blk).strip()
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


def _valid_id(task_id):
    """task_id 白名单：字母开头，仅字母/数字/下划线/连字符。

    id 会被拼进磁盘路径（tasks/<id>.json 等）与 URL，`../` 这类穿越序列必须
    在入口处拒掉；store 里所有「id 直拼路径」的读写都要先过这道闸。
    """
    return bool(re.match(r"^[A-Za-z][0-9A-Za-z_-]*$", str(task_id)))


def set_book_meta(task_id, platform, entry):
    """写任务的作品信息状态（book_meta[platform] = {status, data?, error?, at}）。

    一键生成是后台线程跑的，前端靠任务 JSON 里的这个字段看进度。这里必须
    bump_state：SSE 存活时前端不主动拉状态，不推送则卡片停在旧状态（点生成
    不翻「生成中」、跑完不翻「已生成」）。任务不存在返回 False。"""
    if not _valid_id(task_id):
        return False
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False
        task.setdefault("book_meta", {})[platform] = entry
        _save_json(paths.TASKS_DIR / (task_id + ".json"), task)
    bump_state()
    return True


def get_task(task_id):
    if not _valid_id(task_id):
        return None
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


def migrate_task_workdirs(old_root, new_root):
    """把「旧默认保存路径」下的任务目录搬到新默认路径下，并更新任务记录。

    只动位于 old_root 内部的任务目录（当初由默认路径自动放置的）；用户显式
    指定的其他目录一律不碰。运行中/排队中的任务跳过（工作目录正被使用）。
    返回 (移动数, 跳过数)。
    """
    oldp = Path(old_root).resolve()
    newp = Path(new_root).resolve()
    if oldp == newp:
        return 0, 0
    moved = skipped = 0
    with LOCK:
        for tid in sorted(_TASKS.keys()):
            t = _TASKS[tid]
            wd = str(t.get("workdir") or "").strip()
            if not wd:
                continue
            try:
                wdp = Path(wd).resolve()
                rel = wdp.relative_to(oldp)
            except (ValueError, OSError):
                continue  # 不在旧默认路径下：不碰
            if t.get("status") in ("queued", "running"):
                skipped += 1
                continue
            if not wdp.is_dir():
                continue
            target = newp / rel
            if target.exists():
                target = newp / (rel.name + "_migrated_" + tid[-4:])
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(wdp), str(target))
            except Exception:
                skipped += 1
                continue
            t["workdir"] = str(target)
            _save_json(paths.TASKS_DIR / (tid + ".json"), t)
            moved += 1
    return moved, skipped


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
        "status": "queued", "steps": [], "messages": [],
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


def task_run_stats():
    """每个任务的运行次数与步骤总数（全量扫描，不受 run 窗口限制）。

    侧栏「查看全部 N 次运行 · M 步」的计数来源——从窗口里数会因刷屏/窗口滑动而算错。
    """
    with LOCK:
        stats = {}
        for r in _RUNS.values():
            tid = r.get("task_id")
            if not tid:
                continue
            s = stats.setdefault(tid, {"runs": 0, "steps": 0})
            s["runs"] += 1
            s["steps"] += len(r.get("steps") or [])
        return stats


def task_runs(task_id):
    """某任务的全部运行（新→旧，含 steps）。任务级详情视图用，按需拉全量。"""
    with LOCK:
        runs = [dict(r) for r in _RUNS.values() if r.get("task_id") == task_id]
    runs.sort(key=lambda r: r["id"], reverse=True)
    return runs


def task_side(task_id):
    """任务检查器（右缘停靠列）的轻量聚合：最新 run 摘要 + 进度步骤 +
    git 分支/裁决状态 + 变更行级统计 + 成品文件。

    刻意不带 diff 文本与消息历史——面板每 2s 轮询一次，响应必须保持 KB 级；
    diff 按需走 /api/runs/<id> 单拉。任务不存在返回 None。

    变更统计取双路径：任务运行中 → 实时 numstat（工作区正检出在任务分支，
    未提交改动即产物）；已结束 → 读最新 run 的 changes 快照（finalize 已把
    产物提交进任务分支并切回，实时统计恒为 0，只能用落盘快照）。
    """
    task = get_task(task_id)
    if not task:
        return None
    runs = task_runs(task_id)
    latest = runs[0] if runs else None
    steps = (latest or {}).get("steps") or []
    done = sum(1 for s in steps if s.get("status") == "done")
    current = next((s for s in steps if s.get("status") == "running"), None)
    active = bool(latest) and latest.get("status") in ("queued", "running")
    # 分支上下文取最近一次带 git 信息的 run（重试/续跑会带出同一任务分支）
    git = {}
    for r in runs:
        if r.get("git"):
            git = {k: r["git"].get(k) for k in
                   ("rev", "branch", "commit", "from_branch", "base_commit",
                    "restored", "restore_error")}
            break
    git["state"] = task.get("git_state") or ""
    changes = {"count": 0, "add_total": None, "del_total": None, "files": []}
    if active:
        from . import gitmod
        live = gitmod.collect_changes(task.get("workdir"))
        changes["count"] = len(live.get("files") or [])
        changes["add_total"] = live.get("add_total") or 0
        changes["del_total"] = live.get("del_total") or 0
        changes["files"] = [
            {"status": f.get("status"), "path": f.get("path"),
             "add": f.get("add") or 0, "del": f.get("del") or 0}
            for f in (live.get("files") or [])[:50]]
        git["branch"] = git.get("branch") or gitmod.branch_name(task_id)
    elif latest is not None:
        snap = latest.get("changes") or {}
        snap_files = snap.get("files") or []
        changes["count"] = len(snap_files)
        changes["add_total"] = snap.get("add_total")
        changes["del_total"] = snap.get("del_total")
        changes["files"] = [
            {"status": f.get("status"), "path": f.get("path"),
             "add": f.get("add"), "del": f.get("del")}
            for f in snap_files[:50]]
    total_steps = sum(len(r.get("steps") or []) for r in runs)
    # 成品口径闸：任务一步都没跑出来过（如历次都在检出前失败）→ 工作目录里的
    # 文件变动全是并行活动的噪音，不算这个任务的成品
    wd, arts = run_artifacts(latest["id"], limit=50) if (latest and total_steps > 0) else ("", [])
    return {
        "task": {k: task.get(k) for k in ("id", "title", "status", "workdir", "git_state",
                                          "git_rev")},
        "run": ({k: latest.get(k) for k in ("id", "status", "cost_usd", "tokens", "error",
                                            "created_at", "started_at", "ended_at")}
                if latest else None),
        "progress": {"total": len(steps), "done": done,
                     "current": ({k: current.get(k) for k in
                                  ("n", "role", "agent_label", "summary", "status")}
                                 if current else None)},
        "steps": [{k: s.get(k) for k in ("n", "role", "agent_label", "summary",
                                         "status", "duration_s", "log")}
                  for s in steps[:20]],
        "git": git,
        "changes": changes,
        "workdir": wd,
        "files": arts,
        "stats": {"runs": len(runs),
                  "steps": total_steps,
                  "cost_usd": sum(float(r.get("cost_usd") or 0) for r in runs),
                  "tokens": sum(int(r.get("tokens") or 0) for r in runs)},
    }


def update_run(run_id, expected_status=None, **fields):
    """更新运行字段。expected_status 非 None 时做 CAS（§2C）：
    当前状态不等于 expected_status 则拒绝写入并返回 None，
    防止陈旧执行方（被取消的 worker、崩溃恢复前的旧线程）覆盖新状态
    ——防御模式「异步状态不是同步状态」。不传则保持原行为。"""
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return None
        if expected_status is not None and run.get("status") != expected_status:
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
    if not _valid_id(run_id):
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
    # 步骤级兜底：终态 run 里卡在 queued/running 的步骤落「已取消」。
    # 既清崩溃遗留，也清取消收尾修复前的僵尸步骤（运行已取消、格子永转）；
    # 进行中的 run 不碰——其步骤是活流程，收尾由 pipeline._run_step 负责。
    healed = 0
    step_now = time.strftime("%H:%M:%S")
    with LOCK:
        for r in _RUNS.values():
            if r.get("status") not in ("done", "failed", "cancelled"):
                continue
            changed = False
            for s in r.get("steps") or []:
                if s.get("status") in ("queued", "running"):
                    s["status"] = "cancelled"
                    s["ended_at"] = s.get("ended_at") or step_now
                    if not s.get("summary"):
                        s["summary"] = "步骤未正常收尾（取消/中断自动恢复）"
                    changed = True
            if changed:
                _save_json(paths.RUNS_DIR / r["id"] / "run.json", r)
                healed += 1
    if healed:
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

# 构建产物/依赖缓存目录：里面的文件是工具链再生成的，不是任务成品
_BUILD_DIRS = {
    "target", "build", "dist", "out", "bin", "obj",           # 通用构建输出
    "surefire", "failsafe-reports", "test-output", "reports",  # 测试/报告输出
    ".next", ".nuxt", ".output", ".gradle", ".gradle-home",    # 前端/Gradle
    "__MACOSX",
}


def task_first_start(task_id, fallback=""):
    """该任务最早一次运行的开始时间（含回退：任务创建时间 → 指定回退值）。"""
    stamps = []
    with LOCK:
        for r in _RUNS.values():
            if r.get("task_id") == task_id:
                stamps.append(str(r.get("started_at") or r.get("created_at") or ""))
    task = get_task(task_id) if task_id else None
    if task:
        stamps.append(str(task.get("created_at") or ""))
    stamps.append(str(fallback or ""))
    best = ""
    for s in stamps:
        if s and (not best or s < best):
            best = s
    return best


def run_artifacts(run_id, limit=200):
    """列一次运行的「成品文件」：工作目录里自该任务首次运行以来新产生/修改的文件。

    断点续跑会拆成多条 run，只按本 run 过滤会漏掉早期章节；这里以「任务首跑」
    为起点。返回 (workdir, files)；files 按 mtime 新→旧，name 为工作目录内
    相对路径，已跳过 .git / 隐藏目录 / node_modules / target 等构建产物目录，
    最多 limit 个。
    """
    wd = run_workdir(run_id)
    if not wd:
        return "", []
    run = get_run(run_id) or {}
    t0 = task_first_start(run.get("task_id") or "",
                           run.get("started_at") or run.get("created_at") or "")
    try:
        t0 = time.mktime(time.strptime(t0, "%Y-%m-%d %H:%M:%S")) - 1
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
            if any(part in _BUILD_DIRS for part in p.parts[:-1]):
                continue
            # 隐藏目录一律是工具过程文件（.mimocode/.zcode/.claude/.codex…），
            # 不是成品；按前缀通排，免得每来一个新 agent CLI 就补一次白名单
            if any(part.startswith(".") for part in p.parts[:-1]):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_mtime < t0:
                continue
            files.append({"name": str(p.relative_to(root)).replace("\\", "/"),
                          "size": st.st_size, "mtime": int(st.st_mtime)})
            if len(files) >= 800:  # 防超大目录拖垮接口；截断后再排序取最新
                break
    except OSError:
        pass
    files.sort(key=lambda f: -f["mtime"])
    return wd, files[:limit]


def task_step_count(task_id):
    """该任务所有 run 的步骤总数（含进行中）。

    成品口径的闸：一步都没跑出来过的任务（历次都在检出等前置环节失败）
    没有成品可言，工作目录里的文件变动全是并行活动的噪音。
    """
    if not task_id:
        return 0
    return sum(len(r.get("steps") or []) for r in task_runs(task_id))


# ---------------------------------------------------------------- 故事圣经（story-bible.md）

BIBLE_FILE = "story-bible.md"
BIBLE_MAX_CHARS = 20000


def _bible_path(workdir):
    """工作目录内圣经文件绝对路径；目录穿越直接返回 None（不读外面任何东西）。"""
    wd = str(workdir or "").strip()
    if not wd or not os.path.isdir(wd):
        return None
    root = Path(wd).resolve()
    p = (root / BIBLE_FILE).resolve()
    if root != p and root not in p.parents:
        return None
    return p


def read_story_bible(task_id):
    """读取任务工作目录里的故事圣经。返回 (文件路径, 文本内容, None) 或
    (None, None, 错误信息)。"""
    from . import store as _self
    task = get_task(task_id)
    if not task:
        return None, None, "任务不存在"
    p = _bible_path(task.get("workdir"))
    if p is None:
        return None, None, "工作目录不存在或路径越界"
    try:
        if p.is_file():
            return str(p), runner.read_text_any_enc(p), None
        return str(p), "", None   # 文件未创建：空内容
    except OSError as e:
        return None, None, "读取失败: %s" % e


def write_story_bible(task_id, text):
    """写入故事圣经到任务工作目录。守卫：
    - 任务不存在/工作目录越界 → 拒绝；
    - 任务正在运行（queued/running）→ 拒绝（圣经是中流砥柱，运行中不能换骨架）；
    - 文本超长（BIBLE_MAX_CHARS）→ 拒绝；
    - 写入失败 → 报错。
    返回 (ok, 错误信息)。"""
    task = get_task(task_id)
    if not task:
        return False, "任务不存在"
    if task.get("status") in ("queued", "running"):
        return False, "任务正在运行，不能修改故事圣经（请等运行结束后再编辑）"
    p = _bible_path(task.get("workdir"))
    if p is None:
        return False, "工作目录不存在或路径越界"
    text = (text or "").strip()
    if len(text) > BIBLE_MAX_CHARS:
        return False, "故事圣经超长（最大 %d 字符，当前 %d 字符）" % (BIBLE_MAX_CHARS, len(text))
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return True, ""
    except OSError as e:
        return False, "写入失败: %s" % e


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
        if not _valid_id(rid):
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
    """归档/取消归档：归档后从默认列表与侧栏隐藏，数据保留，可随时恢复。
    运行中/排队也允许归档——归档只是隐藏，运行照常继续，不删任何东西。"""
    if not _valid_id(task_id):
        return False, "非法的任务 ID"
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False, "任务不存在"
        task["archived"] = bool(archived)
        _save_json(paths.TASKS_DIR / (task_id + ".json"), task)
    return True, ""


def delete_task(task_id):
    """删除任务及其全部运行记录（含日志与报告目录）。返回 (ok, 错误信息)。"""
    if not _valid_id(task_id):
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
    if not _valid_id(task_id):
        return False, "非法的任务 ID", None
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False, "任务不存在", None
        for r in _RUNS.values():
            if r.get("task_id") == task_id and r.get("status") in ("queued", "running"):
                return False, "任务仍在运行中，不能重试", None
        run = create_run("orchestration", task.get("title") or task_id, task_id=task_id)
        # 运行中指挥继承：旧 run 里未消费的用户纠偏指令带入新 run——
        # 指令是针对目标的意图，不因一次超时/失败而丢（自动续跑同享）。
        # 旧 run 的消息保留原样（历史回放可见），新 run 里是未消费副本。
        inherited = []
        for prev in _RUNS.values():
            if prev.get("task_id") != task_id or prev["id"] == run["id"]:
                continue
            for m in prev.get("messages") or []:
                if not m.get("consumed"):
                    inherited.append(dict(m))
        if inherited:
            run["messages"] = inherited[-50:]  # 封顶防多轮重试滚雪球
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
                        # 分数优先取 verdict（整轮成功时的最终账）；
                        # failed/cancelled 的 run 没有 verdict，退回每章实时
                        # 落账的 chapter_scores——否则多轮失败恢复会把全部
                        # 已过线章节重新评审（实测一晚白烧数百万 token）
                        "chapter_scores": ((prev.get("verdict") or {}).get("chapter_scores")
                                           or prev.get("chapter_scores") or []),
                    }
                    break
        task["status"] = "queued"
        _save_json(paths.TASKS_DIR / (task_id + ".json"), task)
        _save_json(paths.RUNS_DIR / run["id"] / "run.json", run)
    return True, "", run


_CH_FILE_RE = re.compile(r"^chapter-(\d{1,4})\.md$")


def serial_book_progress(task):
    """这本书已经写到第几章：工作目录里的 chapter-*.md 是事实标准（续写批次
    共用同一目录，天然包含全部历史章）；文件缺失时沿 continues 链推算兜底。"""
    last = 0
    wd = str(task.get("workdir") or "")
    if wd:
        try:
            for p in Path(wd).glob("chapter-*.md"):
                m = _CH_FILE_RE.match(p.name)
                if m:
                    last = max(last, int(m.group(1)))
        except OSError:
            pass
    if last:
        return last
    seen = set()
    cur = task
    while cur and cur.get("serial") and cur["id"] not in seen and len(seen) < 20:
        seen.add(cur["id"])
        s = cur["serial"]
        try:
            start = int(s.get("start_chapter") or 1)
        except Exception:
            start = 1
        runs = task_runs(cur["id"])
        batch = 0
        for r in runs:
            o = r.get("outline")
            if o and o.get("chapters") and not o.get("degraded"):
                batch = len(o["chapters"])
                break
        # 从没跑过的任务不计入进度：计划章数 ≠ 已写成章数
        if runs:
            batch = batch or int(s.get("chapters") or 0)
        if batch:
            last = max(last, start + batch - 1)
        cur = get_task(str(s.get("continues") or ""))
    return last


def continue_info(task_id):
    """「继续连载」弹框数据：能否续、已写到第几章、默认续几章。任务不存在返回 None。"""
    task = get_task(task_id)
    if not task:
        return None
    serial = task.get("serial") or {}
    try:
        default_ch = int(serial.get("chapters") or 8)
    except Exception:
        default_ch = 8
    info = {"task_id": task_id, "serial": bool(serial),
            "last_chapter": 0, "default_chapters": default_ch,
            "words_per_chapter": int(serial.get("words_per_chapter") or 2500),
            "can": False, "reason": ""}
    if not serial:
        info["reason"] = "只有连载任务支持继续连载"
        return info
    if task.get("status") in ("queued", "running"):
        info["reason"] = "任务还在运行中，等结束或取消后再续写"
        return info
    for r in _RUNS.values():
        if r.get("task_id") == task_id and r.get("status") in ("queued", "running"):
            info["reason"] = "有运行中的记录，请先取消"
            return info
    last = serial_book_progress(task)
    info["last_chapter"] = last
    if last < 1:
        info["reason"] = "还没写成任何一章（工作目录里没有 chapter-*.md），先跑完一次连载"
        return info
    info["can"] = True
    return info


def continue_task(task_id, chapters=None):
    """在此基础上新建任务继续连载：沿用目标/上下文/目录/评审设置，从已写到
    的下一章接着写（章节文件与成书合并按全书章号衔接，旧章不动）。
    返回 (ok, 错误, 新任务)。"""
    task = get_task(task_id)
    if not task:
        return False, "任务不存在", None
    info = continue_info(task_id)
    if not info.get("can"):
        return False, info.get("reason") or "当前不能继续连载", None
    serial = task["serial"]
    try:
        batch = max(1, min(20, int(chapters or serial.get("chapters") or 8)))
    except Exception:
        batch = int(serial.get("chapters") or 8)
    # 标题：去掉历史「·续N」后缀取根名，按链条代数标 ·续 / ·续2 / ·续3…
    base_title = re.sub(r"·续\d*$", "", task.get("title") or "").strip() or task_id
    gen, cur, seen = 0, task, set()
    while cur and cur["id"] not in seen and len(seen) < 20:
        seen.add(cur["id"])
        gen += 1
        cur = get_task(str((cur.get("serial") or {}).get("continues") or ""))
    payload = {
        "type": task["type"],
        "title": base_title + ("·续" if gen <= 1 else "·续%d" % gen),
        "goal": task.get("goal") or "",
        "context": task.get("context") or "",
        "workdir": task.get("workdir") or "",
        "mode": task.get("mode") or "auto",
        "difficulty": task.get("difficulty") or "auto",
        "implementer": task.get("implementer") or "",
        "critics": task.get("critics") or [],
        "manuscript": task.get("manuscript") or "manuscript.md",
        "threshold": task.get("threshold") or 7.0,
        "serial": {"chapters": batch,
                   "words_per_chapter": int(serial.get("words_per_chapter") or 2500),
                   "start_chapter": info["last_chapter"] + 1,
                   "continues": task_id},
    }
    if serial.get("variants"):   # 赛马配置随链条沿用
        payload["serial"]["variants"] = serial["variants"]
    for key in ("draft_prompt", "critique_prompt"):  # 自定义提示词覆盖一并沿用
        if task.get(key):
            payload[key] = task[key]
    try:
        new = create_task(payload)
    except ValueError as e:
        return False, str(e), None
    return True, "", new


def set_task_git_state(task_id, state):
    """任务分支裁决状态：isolated（有分支待裁决）/ merged / discarded / None（清除）。

    run 检出任务分支时置 isolated，人审合并/丢弃后置终态，新一轮 run 又会
    重置回 isolated。返回 (ok, 错误信息)。
    """
    if not _valid_id(task_id):
        return False, "非法的任务 ID"
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False, "任务不存在"
        if state:
            task["git_state"] = state
        else:
            task.pop("git_state", None)
        _save_json(paths.TASKS_DIR / (task_id + ".json"), task)
        bump_state()
    return True, ""


def rename_task(task_id, title):
    """重命名任务：同步更新任务与全部运行记录的标题。

    侧栏任务树按 run.title 显示组名，只改任务会让历史运行仍顶着旧名，
    所以两者一起改（运行 json 逐个回写）。
    """
    if not _valid_id(task_id):
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
                cost_usd=0.0, tokens=0.0, duration_s=None, model=None, output=None):
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
                if output is not None:
                    s["output"] = str(output)[:6000]
                break
        run["cost_usd"] = round(run.get("cost_usd", 0.0) + cost_usd, 4)
        run["tokens"] = run.get("tokens", 0) + tokens
        _save_json(paths.RUNS_DIR / run_id / "run.json", run)


# ---------------------------------------------------------------- 运行中指挥（消息信箱）
# 用户可在任务运行中往编排者「递话」：文字 + 附件（截图/文件）。消息先进信箱，
# 下一个智能体步骤开始前由 pipeline drain 出来注入 prompt——不插进正在跑的进程
# （无头 CLI 没有交互 stdin），而是在最近的轮间安全点生效。consumed 标记防重复注入。

def _ensure_messages(run):
    if "messages" not in run or not isinstance(run.get("messages"), list):
        run["messages"] = []
    return run["messages"]


def add_message(run_id, text, sender="本机", attachments=None):
    """往运行信箱追加一条用户指令。attachments 为已落盘到 workdir 的
    附件相对路径清单（由 main.py 提交后传入）。返回消息 dict 或 None。"""
    text = str(text or "").strip()
    atts = [str(a) for a in (attachments or []) if a][:12]
    if not text and not atts:
        return None
    if len(text) > 4000:
        text = text[:4000]
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return None
        msgs = _ensure_messages(run)
        if len(msgs) >= 200:  # 信箱封顶：只保留最近 200 条，防 run.json 无限膨胀
            del msgs[:len(msgs) - 199]
        # id 取「现存最大 +1」而非 len+1：撤回/封顶删除后 len 会回落，
        # 用 len+1 会和存活消息撞号，前端按 id 撤回就错杀。
        nxt = 1
        for m in msgs:
            try:
                nxt = max(nxt, int(m.get("id") or 0) + 1)
            except (TypeError, ValueError):
                pass
        msg = {
            "id": "%06d" % nxt,
            "text": text, "sender": str(sender or "")[:24],
            "attachments": atts,
            "created_at": time.strftime("%H:%M:%S"),
            "consumed": False,
        }
        msgs.append(msg)
        _save_json(paths.RUNS_DIR / run_id / "run.json", run)
        return msg


def drain_messages(run_id, consumed_by=None):
    """取出并标记全部未消费消息（运行中指挥注入点调用）。

    consumed_by：{"step": 步号, "role": 角色}——送达回执，记录指令最终进了
    哪个步骤，详情页「✓已下达」据此显示去向。返回 [{text,attachments,...}]。"""
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return []
        pend = [m for m in _ensure_messages(run) if not m.get("consumed")]
        if not pend:
            return []
        receipt = None
        if consumed_by:
            try:
                receipt = {"step": int(consumed_by.get("step") or 0),
                           "role": str(consumed_by.get("role") or "")[:40]}
            except Exception:
                receipt = None
        for m in pend:
            m["consumed"] = True
            if receipt:
                m["consumed_by"] = dict(receipt)
        _save_json(paths.RUNS_DIR / run_id / "run.json", run)
        return [{"text": m.get("text", ""), "attachments": m.get("attachments", []),
                 "sender": m.get("sender", ""), "created_at": m.get("created_at", "")}
                for m in pend]


def peek_messages(run_id):
    """只读未消费消息（不标记）：规划步骤合入上下文用，执行步骤仍会 drain 注入。"""
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return []
        return [{"text": m.get("text", ""), "attachments": m.get("attachments", []),
                 "sender": m.get("sender", ""), "created_at": m.get("created_at", "")}
                for m in _ensure_messages(run) if not m.get("consumed")]


def retract_message(run_id, msg_id):
    """撤回一条尚未下达（consumed=False）的指令：从信箱删除。

    只删未消费的——已随步骤注入执行的删不掉（那是审计事实，且 prompt 已喂出）。
    返回 (ok, err)：ok=False 时 err 说明原因，供前端 toast。"""
    msg_id = str(msg_id or "").strip()
    if not msg_id:
        return False, "缺少消息 id"
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return False, "运行不存在"
        msgs = _ensure_messages(run)
        for i, m in enumerate(msgs):
            if str(m.get("id")) == msg_id:
                if m.get("consumed"):
                    cb = m.get("consumed_by") or {}
                    where = ("已随步骤 #" + str(cb.get("step")) + " 送达") if cb.get("step") \
                        else "已送达执行"
                    return False, "指令" + where + "，无法撤回"
                del msgs[i]
                _save_json(paths.RUNS_DIR / run_id / "run.json", run)
                return True, ""
        return False, "消息不在信箱中（可能已被撤回）"


def set_paused(run_id, paused):
    """暂停/放行：标志位挂在下一个步骤开始前（pipeline 轮间闸门读取）。"""
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return False
    update_run(run_id, paused=bool(paused))
    return True


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


def read_step_log(run_id, rel_path, tail=paths.LOG_TAIL_CHARS, pretty=False):
    p = (paths.RUNS_DIR / run_id / rel_path).resolve()
    try:
        # 防目录穿越：必须落在本 run 目录内
        if paths.RUNS_DIR.resolve() not in p.parents:
            return ""
        data = p.read_bytes()
        if len(data) > tail:
            text = "...(已截断)...\n" + runner.tail_decoded(data, tail)
        else:
            text = runner.decode_output(data)
        # 折叠遥测刷屏（时间戳不同的重复 WARN）；pretty 再把 codex JSONL
        # 事件流翻译成【消息】【命令】等可读行，日志抽屉直读"蜂在干什么"
        text = runner.collapse_dup_lines(text)
        if pretty:
            text = runner.pretty_cli_log(text)
        return text
    except Exception:
        return ""
