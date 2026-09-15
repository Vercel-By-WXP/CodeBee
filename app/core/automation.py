# -*- coding: utf-8 -*-
"""自动化（定时任务）：按计划到点自动拉起一次编排运行，无人值守推进日常工作。

数据落盘 <data>/automation.json（tmp + os.replace 原子写；data 目录由 paths.py
决定，TUTTI_DATA 环境变量感知）。调度是一个 daemon 线程，每 TICK_SECONDS 秒
扫一遍到期任务：到点即复用与 /api/tasks 完全相同的链路（store.create_task →
store.create_run → jobs.enqueue）拉起一次真实运行，不自己造运行器。单次触发
失败只把 last_status 记为 error 并推进 next_run，调度线程绝不允许因异常退出。

任务模型：
  {id, name, prompt, workdir, kind: daily|interval|weekly|once, time: "HH:MM",
   interval_hours, weekday(0-6 周一=0), run_at(once 用, ISO), flow(编排流程 id),
   enabled, created_at, last_run, next_run, run_count, last_status}

重启语义：错过的 once 不补跑（启动恢复时直接停用）；daily/weekly/interval
重算 next_run 到下一个未来时刻即可，不追赶停机期间错过的周期。

主进程接线（main.py，服务启动时调用一次）：
    from core import automation
    n = automation.start()
HTTP 分支契约见仓库集成说明：GET/POST /api/automation、
GET/POST /api/automation/<id>、POST /api/automation/<id>/(toggle|run|delete)。
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import jobs, paths, store

log = logging.getLogger(__name__)

_LOCK = threading.RLock()
_FILE = paths.DATA_DIR / "automation.json"
_TASKS = {}           # id → task dict（内存真源；落盘为 {"version":1,"tasks":[全部任务]}）
_LOADED = False
_STARTED = False

TICK_SECONDS = 25     # 调度扫描间隔：到点触发误差不超过半个周期
KINDS = ("daily", "interval", "weekly", "once")
INTERVAL_MIN, INTERVAL_MAX = 1, 720   # interval_hours 合法区间（小时）
DEFAULT_FLOW = "doc"  # 未指定编排流程时的兜底：review 引擎产出文档，适配巡检/整理类提示词

_TIME_RE = re.compile(r"\s*(\d{1,2}):(\d{1,2})\s*")

# 字段缺省（加载历史文件/脏数据时兜底；enabled 兜底为 False，绝不意外触发）
_DEFAULTS = {"id": "", "name": "", "prompt": "", "workdir": "", "kind": "", "time": "",
             "interval_hours": 0, "weekday": -1, "run_at": "", "flow": "",
             "enabled": False, "created_at": "", "last_run": "", "next_run": "",
             "run_count": 0, "last_status": ""}

# 允许通过 update() 修改的字段（id/created_at/run_count 等运行痕迹不可改）
_UPDATABLE = ("name", "prompt", "workdir", "kind", "time", "interval_hours",
              "weekday", "run_at", "flow", "enabled")


# ---------------------------------------------------------------- 时间工具

def _parse_dt(s):
    """宽松解析 ISO/常用时间串（%Y-%m-%d %H:%M[:S]、带 T 的 ISO 均可）。失败返回 None。"""
    try:
        return datetime.fromisoformat(str(s or "").strip())
    except ValueError:
        return None


def _fmt_dt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _norm_hhmm(v):
    """归一 "HH:MM"（24 小时制，容忍 9:5 这类输入）。非法返回 None。"""
    m = _TIME_RE.match(str(v or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return "%02d:%02d" % (h, mi)


def compute_next_run(t, now=None):
    """按任务的 kind 计算下一次运行时间（本地时间串）。算不出返回空串（tick 跳过）。

    daily   = 每天 HH:MM（已过今天点 → 明天，正确跨天）
    weekly  = 指定星期几的 HH:MM（已过本周点 → 下周，正确跨周）
    interval= 每 N 小时：锚点取 last_run（无则 created_at），锚点早已过去时按 N
              小时整步推进到第一个未来时刻（停机不追赶、不突发补跑）
    once    = 即 run_at 本身（错过的由启动恢复停用，正常路径到点触发后即停用）
    """
    now = now or datetime.now()
    kind = t.get("kind")
    if kind == "daily":
        hhmm = _norm_hhmm(t.get("time"))
        if hhmm is None:
            return ""
        h, mi = hhmm.split(":")
        cand = now.replace(hour=int(h), minute=int(mi), second=0, microsecond=0)
        if cand <= now:
            cand += timedelta(days=1)
        return _fmt_dt(cand)
    if kind == "weekly":
        hhmm = _norm_hhmm(t.get("time"))
        try:
            wd = int(t.get("weekday"))
        except (TypeError, ValueError):
            return ""
        if hhmm is None or not 0 <= wd <= 6:
            return ""
        h, mi = hhmm.split(":")
        cand = now.replace(hour=int(h), minute=int(mi), second=0, microsecond=0)
        cand += timedelta(days=(wd - now.weekday()) % 7)
        if cand <= now:
            cand += timedelta(days=7)
        return _fmt_dt(cand)
    if kind == "interval":
        try:
            n = max(INTERVAL_MIN, int(t.get("interval_hours")))
        except (TypeError, ValueError):
            return ""
        anchor = _parse_dt(t.get("last_run")) or _parse_dt(t.get("created_at")) or now
        if anchor > now:
            return _fmt_dt(anchor)
        k = int((now - anchor).total_seconds() // (n * 3600)) + 1
        return _fmt_dt(anchor + timedelta(hours=n * k))
    if kind == "once":
        dt = _parse_dt(t.get("run_at"))
        return _fmt_dt(dt) if dt else ""
    return ""


# ---------------------------------------------------------------- 校验

def _validate_core(t, check_workdir=True):
    """name/prompt/workdir 校验与归一（就地改写 t）。workdir 为空 = 跟随「默认
    保存路径」（与 store.create_task 口径一致，到点触发时由它兜底创建）。"""
    name = str(t.get("name") or "").strip()
    if not name:
        raise ValueError("任务名称不能为空")
    t["name"] = name[:60]
    prompt = str(t.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("执行提示词（prompt）不能为空")
    t["prompt"] = prompt
    workdir = str(t.get("workdir") or "").strip()
    if workdir:
        p = Path(workdir).expanduser()
        if not p.is_absolute():
            raise ValueError("工作目录必须是绝对路径")
        if check_workdir and not p.is_dir():
            raise ValueError("工作目录不存在: %s" % workdir)
        t["workdir"] = str(p)
    else:
        t["workdir"] = ""


def _apply_schedule(t, check_flow=True):
    """按 kind 校验并归一调度字段（就地改写 t）。缺字段/越界/once 时间已过均抛
    ValueError——错过的一次性任务不补跑，所以创建/修改时直接拒绝过去时间。"""
    kind = t.get("kind")
    if kind not in KINDS:
        raise ValueError("kind 必须是 %s 之一" % "、".join(KINDS))
    if kind in ("daily", "weekly"):
        hhmm = _norm_hhmm(t.get("time"))
        if hhmm is None:
            raise ValueError("time 必须是 24 小时制 HH:MM")
        t["time"] = hhmm
    if kind == "weekly":
        try:
            wd = int(t.get("weekday"))
        except (TypeError, ValueError):
            raise ValueError("weekday 必须是 0-6 的整数（0=周一）")
        if not 0 <= wd <= 6:
            raise ValueError("weekday 必须是 0-6 的整数（0=周一）")
        t["weekday"] = wd
    if kind == "interval":
        try:
            n = int(t.get("interval_hours"))
        except (TypeError, ValueError):
            raise ValueError("interval_hours 必须是 %d-%d 的整数" % (INTERVAL_MIN, INTERVAL_MAX))
        if not INTERVAL_MIN <= n <= INTERVAL_MAX:
            raise ValueError("interval_hours 必须是 %d-%d 的整数" % (INTERVAL_MIN, INTERVAL_MAX))
        t["interval_hours"] = n
    if kind == "once":
        dt = _parse_dt(t.get("run_at"))
        if dt is None:
            raise ValueError("once 任务需要 run_at（ISO 时间，如 2026-09-20T09:30）")
        if dt <= datetime.now():
            raise ValueError("once 的 run_at 必须是未来时间（错过的一次性任务不补跑）")
        t["run_at"] = _fmt_dt(dt)
    flow = str(t.get("flow") or "").strip()
    if flow:
        if check_flow:
            from . import flows as flows_mod
            if flows_mod.get_flow(flow) is None:
                raise ValueError("未知编排流程：%s（可选见 /api/flows）" % flow)
        t["flow"] = flow
    else:
        t["flow"] = DEFAULT_FLOW


# ---------------------------------------------------------------- 持久化

def _list_locked():
    return [_TASKS[i] for i in sorted(_TASKS.keys())]


def _save_locked():
    """整体落盘：tmp + os.replace 原子替换，坏一半也不会丢旧数据。"""
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "tasks": _list_locked()},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(_FILE))
    store.bump_state()  # 落盘即状态变化：多端 SSE 尽快看到


def _normalize(d):
    """把磁盘上的历史条目收敛到当前模型（脏字段兜底，绝不抛异常）。"""
    t = dict(_DEFAULTS)
    if isinstance(d, dict):
        t.update({k: v for k, v in d.items() if k in _DEFAULTS})
    if t["kind"] not in KINDS:
        t["kind"] = ""
    try:
        t["run_count"] = max(0, int(t["run_count"]))
    except (TypeError, ValueError):
        t["run_count"] = 0
    t["enabled"] = bool(t["enabled"])
    return t


def load(force=False):
    """从磁盘加载任务（幂等）。返回任务数。测试可重绑 _FILE 后 force=True。"""
    global _LOADED
    with _LOCK:
        if _LOADED and not force:
            return len(_TASKS)
        _TASKS.clear()
        try:
            data = json.loads(_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        raw = data.get("tasks") if isinstance(data, dict) else data
        for d in raw or []:
            t = _normalize(d)
            if t["id"]:
                _TASKS[t["id"]] = t
        _LOADED = True
        return len(_TASKS)


def _ensure_loaded():
    if not _LOADED:
        load()


# ---------------------------------------------------------------- 启动恢复

def _recover_after_restart():
    """服务重启后的调度对账：错过的 once 直接停用（不补跑）；其余 kind 重算
    next_run 到下一个未来时刻。必须在 load() 之后调用。返回改动条数。"""
    now = datetime.now()
    changed = 0
    with _LOCK:
        for t in _TASKS.values():
            if not t.get("enabled"):
                continue
            if t.get("kind") == "once":
                nr = _parse_dt(t.get("next_run") or "")
                if nr is None or nr <= now:
                    t["enabled"] = False
                    t["next_run"] = ""
                    t["last_status"] = "missed"
                    changed += 1
            else:
                t["next_run"] = compute_next_run(t, now=now)
                changed += 1
        if changed:
            _save_locked()
    return changed


# ---------------------------------------------------------------- 运行拉起

def _launch_run(t):
    """拉起一次真实编排运行：与 main.py 的 /api/tasks 走同一条链路
    （store.create_task → store.create_run → jobs 入队），返回 run_id。
    测试可把本函数打成 stub，绝不真调 LLM/CLI。"""
    payload = {"type": t.get("flow") or DEFAULT_FLOW,
               "title": ("%s %s" % (t.get("name") or "定时任务",
                                    time.strftime("%m-%d %H:%M"))).strip()[:40],
               "goal": t.get("prompt") or "",
               "workdir": (t.get("workdir") or "").strip()}
    task = store.create_task(payload)
    run = store.create_run("orchestration", task["title"], task_id=task["id"])
    store.update_task_status(task["id"], "queued")
    jobs.enqueue({"kind": "orchestration", "run_id": run["id"], "task_id": task["id"]})
    return run["id"]


def _fire(snapshot, now):
    """触发一个到期任务：拉起运行并推进 last_run/next_run/run_count/last_status。
    once 触发完自动停用。拉起失败只记 error，next_run 照常推进（下个周期重试）。"""
    status = "queued"
    try:
        _launch_run(snapshot)
    except Exception as e:
        log.warning("automation: 定时触发 %s 失败: %r", snapshot.get("id"), e)
        status = "error"
    with _LOCK:
        cur = _TASKS.get(snapshot.get("id"))
        if cur is None:
            return
        cur["last_run"] = _fmt_dt(now)
        cur["run_count"] = int(cur.get("run_count") or 0) + 1
        cur["last_status"] = status
        if cur.get("kind") == "once":
            cur["enabled"] = False
            cur["next_run"] = ""
        else:
            cur["next_run"] = compute_next_run(cur, now=now)
        _save_locked()


# ---------------------------------------------------------------- 调度线程

def _tick():
    """扫描一遍到期任务并逐个触发。单任务异常只跳过该条，绝不上抛。"""
    now = datetime.now()
    due = []
    with _LOCK:
        for tid in sorted(_TASKS.keys()):
            t = _TASKS[tid]
            if not t.get("enabled"):
                continue
            nr = _parse_dt(t.get("next_run") or "")
            if nr is not None and nr <= now:
                due.append(tid)
    for tid in due:
        try:
            with _LOCK:
                t = _TASKS.get(tid)
            if t is None or not t.get("enabled"):
                continue  # 等待期间被删/被停：跳过
            _fire(dict(t), now)
        except Exception:
            log.exception("automation: tick 处理任务 %s 异常，已跳过", tid)


def _loop():
    while True:
        try:
            _tick()
        except Exception:
            log.exception("automation: 调度 tick 异常，继续下一轮")
        time.sleep(TICK_SECONDS)


def start():
    """主进程启动时调用一次：加载任务、做重启对账并拉起调度线程。返回任务数。
    重复调用是安全的（幂等，不会起第二条线程）。"""
    global _STARTED
    with _LOCK:
        if _STARTED:
            return len(_TASKS)
        _STARTED = True
        n = load()
        _recover_after_restart()
    try:
        threading.Thread(target=_loop, name="automation-scheduler",
                         daemon=True).start()
    except Exception:
        log.exception("automation: 调度线程启动失败（定时任务本轮不会触发）")
    return n


# ---------------------------------------------------------------- CRUD + 控制

def list_tasks():
    """全部定时任务（新在前）。返回副本，调用方随便改。"""
    _ensure_loaded()
    with _LOCK:
        return [dict(_TASKS[i]) for i in sorted(_TASKS.keys(), reverse=True)]


def get_task(tid):
    _ensure_loaded()
    with _LOCK:
        t = _TASKS.get(tid)
        return dict(t) if t else None


def create(payload):
    """创建定时任务（payload 为请求体 dict）。校验失败抛 ValueError。"""
    payload = payload if isinstance(payload, dict) else {}
    _ensure_loaded()
    t = dict(_DEFAULTS)
    t.update({k: payload.get(k) for k in _UPDATABLE})
    t["id"] = "auto-%s-%04d" % (time.strftime("%Y%m%d-%H%M%S"), secrets.randbelow(10000))
    t["enabled"] = bool(payload.get("enabled", True))
    t["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    t["kind"] = payload.get("kind") or "daily"
    _validate_core(t)
    _apply_schedule(t)
    t["next_run"] = compute_next_run(t)
    with _LOCK:
        _TASKS[t["id"]] = t
        _save_locked()
    return dict(t)


def update(tid, patch):
    """部分更新（patch 只影响出现的字段）。调度字段变动后重算 next_run。
    任务不存在返回 None；校验失败抛 ValueError（不落盘、不改内存）。"""
    _ensure_loaded()
    patch = patch if isinstance(patch, dict) else {}
    with _LOCK:
        cur = _TASKS.get(tid)
        if cur is None:
            return None
        merged = dict(cur)
        for k in _UPDATABLE:
            if k in patch:
                merged[k] = patch[k]
        _validate_core(merged, check_workdir=("workdir" in patch))
        _apply_schedule(merged, check_flow=("flow" in patch))
        merged["next_run"] = compute_next_run(merged)
        _TASKS[tid] = merged
        _save_locked()
    return dict(merged)


def set_enabled(tid, enabled):
    """启用/停用。启用时重算 next_run；once 启用即成过去式则按「错过」停用。"""
    _ensure_loaded()
    with _LOCK:
        t = _TASKS.get(tid)
        if t is None:
            return None
        t["enabled"] = bool(enabled)
        t["next_run"] = compute_next_run(t) if t["enabled"] else ""
        if t["enabled"] and t.get("kind") == "once":
            nr = _parse_dt(t.get("next_run") or "")
            if nr is None or nr <= datetime.now():
                t["enabled"] = False
                t["next_run"] = ""
                t["last_status"] = "missed"
        _save_locked()
    return dict(t)


def run_now(tid):
    """立即执行一次：不影响 next_run（到点仍照常触发）。返回 (task, run_id)；
    任务不存在返回 (None, None)；拉起失败 last_status=error、run_id 为空串。"""
    _ensure_loaded()
    with _LOCK:
        exists = tid in _TASKS
    if not exists:
        return None, None
    now = datetime.now()
    run_id = ""
    status = "queued"
    try:
        with _LOCK:
            snapshot = dict(_TASKS[tid])
        run_id = _launch_run(snapshot)
    except Exception as e:
        log.warning("automation: 手动触发 %s 失败: %r", tid, e)
        status = "error"
    with _LOCK:
        t = _TASKS.get(tid)
        if t is None:
            return None, None
        t["last_run"] = _fmt_dt(now)
        t["run_count"] = int(t.get("run_count") or 0) + 1
        t["last_status"] = status
        _save_locked()
        return dict(t), run_id


def delete(tid):
    """删除任务（运行产生的任务/运行记录不受影响，仍在任务列表里可查）。"""
    _ensure_loaded()
    with _LOCK:
        if tid not in _TASKS:
            return False
        del _TASKS[tid]
        _save_locked()
    return True


# ---------------------------------------------------------------- 内置模板

TEMPLATES = [
    {
        "id": "daily-repo-inspection",
        "name": "每日仓库巡检",
        "desc": "每天早上自动巡检一次仓库：汇总未提交变更、梳理 TODO/FIXME 待办与风险，"
                "生成当天的巡检报告 INSPECTION.md。",
        "prompt": "对本工作目录的代码仓库做一次巡检，只读分析，不要修改任何源码文件：\n"
                  "1. 运行 git status 与 git diff --stat，汇总当前未提交的变更；"
                  "再用 git log --oneline -15 概览最近的提交脉络。\n"
                  "2. 扫描源码里的 TODO、FIXME、HACK 标记，列出仍然悬挂的条目，"
                  "标注所在文件与行号。\n"
                  "3. 检查是否有临时产物混进源码（调试脚本、遗留的 print/console 调试输出、"
                  "没进忽略规则的生成文件）。\n"
                  "4. 输出一份 markdown 巡检报告保存为 INSPECTION.md，分三节："
                  "「变更概览」「待办与风险」「今日建议」，每节不超过 10 条，直接给结论。",
        "suggested_kind": "daily",
        "suggested_time": "09:00",
        "suggested_flow": "research",
    },
    {
        "id": "serial-novel-advance",
        "name": "连载定时推进",
        "desc": "配合连载小说任务：到点自动读取故事圣经与已有章节，续写下一段剧情，"
                "并自检人设、时间线与伏笔的一致性。",
        "prompt": "继续推进本工作目录里的连载小说：\n"
                  "1. 先读故事圣经（若有）与全部已有章节，回顾主线剧情、人物设定与未回收的伏笔，"
                  "用 3 到 5 句话确认「接下来该发生什么」再动笔。\n"
                  "2. 按既有单章字数与文风续写下一章：承接收尾章节的节奏，"
                  "至少推进一条主线冲突，人物言行必须与圣经设定一致。\n"
                  "3. 写完后自检：人称与时态是否统一、伏笔是否被误收、与前文章节有无矛盾，"
                  "发现问题立即修订。\n"
                  "4. 成稿按已有章节的命名规则保存，章末附一句话「下一章预告」。"
                  "不要改动已完成章节的任何内容。",
        "suggested_kind": "interval",
        "suggested_time": "",
        "suggested_interval_hours": 12,
        "suggested_flow": "novel",
    },
    {
        "id": "weekly-experience-cleanup",
        "name": "经验库周整理",
        "desc": "每周一自动整理经验库：合并重复教训、按主题归纳、标记疑似过时的条目，"
                "产出精简整理报告（不直接改写原文件）。",
        "prompt": "整理本机 CodeBee 经验库（数据目录下 skills.json 的教训条目，"
                  "含 id/title/content/category 等字段），只读分析原文件，把整理结果写成报告：\n"
                  "1. 通读全部教训条目，找出语义重复或高度相似的条目，"
                  "给出合并建议（保留哪条、并入哪些、合并后的表述）。\n"
                  "2. 按「环境安装、编排流程、提示词技巧、踩坑预警」等主题归纳分组，"
                  "指出每组里最经得起复用的 2 到 3 条。\n"
                  "3. 标记疑似过时的条目（依赖已升级、场景已下线）并说明理由。\n"
                  "4. 输出 markdown 报告保存为 EXPERIENCE-REVIEW.md：先给汇总数字"
                  "（总数、重复组数、疑似过时数），再给分组明细与合并建议清单。"
                  "不要直接改写 skills.json，合并动作等人工确认。",
        "suggested_kind": "weekly",
        "suggested_time": "09:30",
        "suggested_weekday": 0,
        "suggested_flow": "doc",
    },
    {
        "id": "weekly-dependency-security-scan",
        "name": "依赖与安全扫描",
        "desc": "每周五自动对项目做一次只读的依赖与安全巡检：清点直接依赖、"
                "排查敏感文件与明文密钥风险，给出升级整改建议。",
        "prompt": "对本工作目录的项目做一次只读的依赖与安全巡检，"
                  "不要安装、升级或修改任何文件：\n"
                  "1. 识别项目类型与依赖清单（package.json、requirements.txt、"
                  "pyproject.toml、go.mod 等），列出直接依赖及声明版本。\n"
                  "2. 排查敏感信息风险：.env、密钥文件、含明文 token 的配置是否被 git 跟踪"
                  "（用 git ls-files 核对）；源码中硬编码的 password、token、secret 字样。\n"
                  "3. 检查依赖健康度：明显落后的大版本、已停止维护的包"
                  "（凭依赖名与版本判断，不确定就标注「待人工确认」）。\n"
                  "4. 输出 markdown 报告保存为 SECURITY-REVIEW.md，分「依赖清单」"
                  "「风险发现（按严重程度排序）」「升级与整改建议」三节；只报告，不执行修复。",
        "suggested_kind": "weekly",
        "suggested_time": "18:00",
        "suggested_weekday": 4,
        "suggested_flow": "research",
    },
]


def templates():
    """内置模板（供前端渲染卡片）。返回副本。"""
    return [dict(t) for t in TEMPLATES]
