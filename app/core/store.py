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

from . import paths, runner, volumes

LOCK = threading.RLock()
_TASKS = {}
_RUNS = {}

# 流式实时落盘的节流表：(run_id, 步骤号) -> 上次落盘时刻。步骤收尾即清，
# 长跑也不会无限增长（finish_step 里调 clear_stream_state）。
_STREAM_LOCK = threading.Lock()
_STREAM_TS = {}
LIVE_TEXT_MAX = 20000   # 单步骤实时思考/正文的保留上限（与 builtin_agent 同口径）

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
    if not isinstance(payload, dict):
        raise ValueError("任务参数必须是 JSON 对象")

    def _text(value, field):
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError("%s 必须是文本" % field)
        return value.strip()

    from . import flows as flows_mod
    flow = flows_mod.get_flow(payload.get("type"))
    if flow is None:
        raise ValueError("未知任务类型：%s（可选：%s）"
                         % (payload.get("type"), "、".join(f["id"] for f in flows_mod.list_flows())))
    title = _text(payload.get("title"), "title")
    goal = _text(payload.get("goal"), "goal")
    workdir = _text(payload.get("workdir"), "workdir")
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
    if mode not in ("auto", "fast", "expert", "manual"):
        mode = "manual" if payload.get("implementer") else "auto"
    difficulty = payload.get("difficulty")
    if difficulty not in ("auto", "easy", "hard", "default"):
        difficulty = "auto"
    task = {
        "id": _new_id("t"), "type": flow["id"], "engine": flow["engine"],
        "title": title, "goal": goal,
        "context": _text(payload.get("context"), "context"),
        "workdir": str(wd),
        "mode": mode,
        "difficulty": difficulty,
        "implementer": payload.get("implementer") or "",
        "attachments": [],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "created",
    }
    thinking = str(payload.get("thinking") or "standard").strip().lower()
    task["thinking"] = thinking if thinking in ("auto", "low", "standard", "high") else "auto"
    if flow["engine"] == "direct":
        task["direct_provider_id"] = _text(payload.get("direct_provider_id"),
                                            "direct_provider_id")[:80]
        task["direct_model"] = _text(payload.get("direct_model"), "direct_model")[:160]
        if task["direct_model"] and not task["direct_provider_id"]:
            raise ValueError("指定对话模型时必须同时指定厂商")
    # 代码版本：仅当引用合法才固化（流水线执行前据此检出任务分支）
    from . import gitmod
    git_rev = _text(payload.get("git_rev"), "git_rev")
    if git_rev:
        # 前端下拉值带 kind 前缀（branch:main / tag:v1 / commit:abc），此处归一为纯 rev；
        # git 分支/标签名本身允许含冒号（罕见），前缀剥离只认这三种已知 kind
        git_rev = re.sub(r"^(branch|tag|commit):", "", git_rev)
        if not gitmod.valid_rev(git_rev):
            raise ValueError("非法的代码版本引用：%s" % git_rev[:40])
        task["git_rev"] = git_rev
    if flow["engine"] == "code":
        task["verify_command"] = _text(payload.get("verify_command"), "verify_command")
    elif flow["engine"] == "direct":
        pass  # 直连任务：无验证命令也无评审参数，目标+附件即全部输入
    else:
        ms = _text(payload.get("manuscript") or flow.get("manuscript") or "manuscript.md",
                   "manuscript")
        ms = re.sub(r"[\\/]", "_", ms)  # 只允许工作目录内的相对文件名
        ms = re.sub(r"\.{2,}", "_", ms).lstrip(".")  # 顺带清掉残留的 ..
        task["manuscript"] = ms
        try:
            task["rounds"] = max(1, min(5, int(payload.get("rounds") or flow.get("rounds") or 2)))
        except Exception:
            task["rounds"] = 2
        try:
            # Best-of-N 赛马候选数（非连载单稿；连载走 serial.variants 的同章赛马）
            task["best_of"] = max(1, min(3, int(payload.get("best_of") or flow.get("best_of") or 1)))
        except Exception:
            task["best_of"] = 1
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
        # 连载模式：逐章起草/评审/修订（任务级 serial 覆盖流程默认）。
        # payload 中显式传 null 表示关闭流程默认连载；字段缺失才沿用流程默认，
        # 这样前端把章节清空时不会被 serial_novel 的默认值悄悄重新打开。
        serial_unset = object()
        serial_value = payload.get("serial", serial_unset)
        if serial_value is None or (isinstance(serial_value, dict) and
                                    serial_value.get("enabled") is False):
            serial = None
        elif isinstance(serial_value, dict):
            serial = serial_value
        else:
            serial = flow.get("serial")
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
                # 分卷：显式卷表优先（新建任务时用户明确给出的分卷信息），
                # 其次每卷章数等长切卷。卷边界一律由 volumes 模块从「章号」
                # 确定性推导——续写批次沿用同一份卷表/每卷章数即自动接上同一卷，
                # 不会因分批而错位。
                vol_spec = volumes.norm_spec(serial.get("volumes"))
                if not vol_spec:
                    # 否则从文字里识别：表单「指定卷结构」框 > 任务目标 > 上下文。
                    # 保守识别——认不出返回空，绝不把普通文字误判成分卷。
                    vol_spec = volumes.parse_spec_text(serial.get("volumes_text")) \
                        or volumes.parse_spec_text(goal) \
                        or volumes.parse_spec_text(task.get("context") or "")
                if vol_spec:
                    s["volumes"] = vol_spec
                vper = volumes.norm_per(serial.get("volume_chapters"))
                if vper:
                    s["volume_chapters"] = vper
                task["serial"] = s
    critics = payload.get("critics")
    if isinstance(critics, list) and critics:
        task["critics"] = [str(c) for c in critics]
    # 初始故事圣经必须在任务入队前落盘，保证首个章节步骤就能读到设定。
    # 只对带连载引擎的任务接收；已有不同内容的圣经拒绝覆盖，避免新任务误伤旧书设定。
    initial_bible = str(payload.get("story_bible") or "").strip()
    if initial_bible:
        if not task.get("serial"):
            raise ValueError("初始故事圣经仅适用于连载小说任务")
        if len(initial_bible) > BIBLE_MAX_CHARS:
            raise ValueError("故事圣经超长（最大 %d 字符，当前 %d 字符）" %
                             (BIBLE_MAX_CHARS, len(initial_bible)))
    resume = payload.get("resume")
    if isinstance(resume, dict) and resume.get("agent") and resume.get("session"):
        task["resume"] = {"agent": str(resume["agent"])[:40],
                          "session": str(resume["session"])[:80],
                          "preview": str(resume.get("preview") or "")[:140]}
        # 会话所属项目目录：续会话时 CLI 必须在该目录下启动（opencode/qwen 按 cwd 定位会话）
        proj = str(resume.get("project") or "")[:260]
        if proj:
            task["resume"]["project"] = proj
    # 初始圣经先于附件提交：如果目录已有设定，尽早拒绝，避免附件已移动却
    # 因故事圣经冲突导致任务创建失败。相同内容的重试是幂等的（例如附件
    # 提交中断后重试），不会覆盖已有设定。
    if initial_bible:
        _write_initial_story_bible(str(wd), initial_bible)
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
            task["context"] = att_mod.merge_context(
                task["context"], items, workdir=str(wd))
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


def set_cover_gen(task_id, entry):
    """写任务的封面生成状态（cover_gen = {status, file?, run_id?, model?, error?, at}）。

    与 set_book_meta 同理必须 bump_state（SSE 推送翻卡片）。任务不存在返回 False。"""
    if not _valid_id(task_id):
        return False
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False
        task["cover_gen"] = entry
        _save_json(paths.TASKS_DIR / (task_id + ".json"), task)
    bump_state()
    return True


def set_auto_publish(task_id, ap):
    """写任务的定时发布配置（auto.py 每日到点读它触发批量发布）。

    校验在 publish/auto.norm_auto_publish（路由层先归一再落这里）；存 None
    表示清除。bump_state 同 set_book_meta：前端卡片即时反映开关态。"""
    if not _valid_id(task_id):
        return False
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False
        if ap is None:
            task.pop("auto_publish", None)
        else:
            task["auto_publish"] = ap
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


def _sanitize_run_text(r):
    """就地清洗 run 与各步骤里的 CLI 文本字段（错误串/摘要/输出）。

    写入侧与读盘侧共用：runner 已经把 ANSI/覆写/乱码墙洗过一遍，这里兜住
    「错误串是我们自己拼的」「历史数据是旧版本写的」两条漏网路径。只动字符串
    值，不碰结构——清洗是幂等的，重复调用无副作用。"""
    for k in ("error", "summary"):
        v = r.get(k)
        if isinstance(v, str) and v:
            r[k] = runner.clean_cli_text(v)
    for s in r.get("steps") or []:
        if not isinstance(s, dict):
            continue
        for k in ("summary", "error"):
            v = s.get(k)
            if isinstance(v, str) and v:
                s[k] = runner.clean_cli_text(v)
        # output 是智能体正文：只剥 ANSI（同 finish_step 的口径）
        if isinstance(s.get("output"), str) and s["output"]:
            s["output"] = runner.strip_ansi(s["output"])


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
                # 存量清洗：早期版本把 CLI 的 ANSI 转义/控制字符原样写进了摘要与
                # 错误串（`[91m[1mError:`、U+FFFD 乱码墙），读盘时统一洗一遍——
                # 老运行不必等重跑才干净（2026-09-20「咋还有乱码」实测）
                _sanitize_run_text(r)
                # 直接执行线程不跨进程：running 与无截止时间的 queued 是上次
                # 进程中断残骸。带 resume_enqueue_at 的 queued 是有意的自动续跑
                # 退避，保留给 jobs.restore_deferred_resumes 重建 Timer。
                interrupted = (r.get("status") == "running" or
                               (r.get("status") == "queued" and
                                not r.get("resume_enqueue_at")))
                if interrupted:
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

_RUN_ESTIMATOR = None


def set_run_estimator(estimator):
    """由应用入口注入 ETA 估算器，保持存储层不反向依赖用量模块。"""
    global _RUN_ESTIMATOR
    _RUN_ESTIMATOR = estimator if callable(estimator) else None


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
    if kind == "orchestration" and task_id:
        task = get_task(task_id) or {}
        try:
            eta = _RUN_ESTIMATOR(
                task_type=task.get("type") or "", mode=task.get("mode") or "auto",
                thinking=task.get("thinking") or "standard",
                rounds=task.get("rounds"))
            run["estimated_duration_s"] = eta.get("estimated_duration_s")
            run["estimated_p90_s"] = eta.get("p90_duration_s")
            run["estimate_source"] = eta.get("duration_source")
        except Exception:
            pass
    rdir = paths.RUNS_DIR / run["id"]
    (rdir / "steps").mkdir(parents=True, exist_ok=True)
    with LOCK:
        # 先完成原子落盘，再发布到内存索引。旧顺序在 _save_json 失败时会
        # 留下只存在于 _RUNS 的“幽灵 run”，后续 UI 看到排队记录却永远无法
        # 读取/恢复其 run.json。
        try:
            _save_json(rdir / "run.json", run)
        except Exception:
            _RUNS.pop(run["id"], None)
            raise
        _RUNS[run["id"]] = run
    # 新 run（排队/起跑）必须唤醒 SSE：否则侧栏 task_latest 感知不到
    # 「最新一次运行」已易主，字形/时间停在上一轮（2026-09-22 侧栏假在跑案）
    bump_state()
    return run


def get_run(run_id):
    with LOCK:
        return _RUNS.get(run_id)


def list_runs(limit=60):
    with LOCK:
        ids = sorted(_RUNS.keys(), reverse=True)
        selected = ids if limit is None else ids[:limit]
        return [_RUNS[i] for i in selected]


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
        for k in ("error", "summary"):
            if isinstance(fields.get(k), str):
                # 错误串多是我们自己拼的 CLI 尾巴（含 ANSI/覆写/乱码墙）：
                # 落内存前洗一遍，UI/台账读到的就是干净文本
                fields[k] = runner.clean_cli_text(fields[k])
        run.update(fields)
        # 终态收尸：run 已结束却还挂 queued/running 的步骤统一落 cancelled，
        # 语义与 recover_orphaned_runs 的启动清扫对齐（那套清跨进程遗留，
        # 这套在落终态瞬间即时收口——打磨组长被取消/异常打断时 UI 不闪
        # 假「工作中」）。run 终态后不可能还有步骤在真跑。
        st = fields.get("status")
        if st in ("done", "failed", "cancelled"):
            end_s = time.strftime("%H:%M:%S")
            for s in run["steps"]:
                if s.get("status") in ("queued", "running"):
                    s["status"] = "cancelled"
                    s["ended_at"] = s.get("ended_at") or end_s
                    if not s.get("summary"):
                        s["summary"] = "步骤未正常收尾（终态自动恢复）"
        _save_json(paths.RUNS_DIR / run_id / "run.json", run)
        # 状态回填：起跑与结束都同步任务状态。只回填终态的话，run 在跑、
        # 任务永远显示「排队中」（2026-09-18 实案：「# 重写·续」run 已 running
        # 写了一小时，任务卡 queued，用户以为卡死连点重试）。
        if st in ("running", "done", "failed", "cancelled"):
            tid = run.get("task_id")
            task = _TASKS.get(tid) if tid else None
            if task:
                task["status"] = st
                _save_json(paths.TASKS_DIR / (tid + ".json"), task)
        if st in ("done", "failed", "cancelled"):
            # run_end 钩子（2026-09-22）：终态即触发，后台跑副作用型脚本
            # （回写知识库等）；失败/超时静默，绝不拖慢收尾路径
            try:
                import threading
                from . import hooks
                threading.Thread(
                    target=hooks.run_event, args=("run_end",), daemon=True,
                    kwargs={"task_id": run.get("task_id") or "", "run_id": run_id,
                            "ctx": {"status": st, "title": run.get("title") or "",
                                    "error": str(run.get("error") or "")[:300]}}).start()
            except Exception:
                pass
    # 每次成功写入都唤醒 SSE：run 的状态/步骤变化从前只能靠无关操作
    # （自动化落盘、删记录）顺带广播，空闲时侧栏 task_latest 会冻在
    # 「在跑」直到下一次无关 bump（2026-09-22 侧栏假在跑案）。
    # 调用都是步骤级边界，不会形成推送风暴；CAS 拒绝/无此 run 的早退
    # 路径不动版本号。
    bump_state()
    return run


def run_dir(run_id):
    return paths.RUNS_DIR / run_id


def _schedule_run_dir_cleanup(run_id):
    """把运行目录快速移出可见路径，再后台删除大日志目录。

    删除任务/运行记录不能让 HTTP 请求同步递归扫描数千个日志文件。
    同盘 rename 是 O(1)，原路径立即消失；后台失败也不会影响内存状态。
    """
    source = paths.RUNS_DIR / str(run_id)
    if not source.exists():
        return
    target = paths.RUNS_DIR / (".deleting-%s-%s" % (run_id, secrets.token_hex(4)))
    try:
        source.rename(target)
    except OSError:
        target = source

    def _remove():
        shutil.rmtree(target, ignore_errors=True)

    threading.Thread(target=_remove, name="codebee-delete-%s" % run_id[:12],
                     daemon=True).start()


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
    _schedule_run_dir_cleanup(run_id)
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


def recover_interrupted_mgmt():
    """启动时调用：把崩溃遗留的 queued 管理操作 run 标记为 failed。

    running 的 mgmt run 已由 recover_orphaned_runs 统一收尸；queued 不在
    其候选里——通用恢复故意留着 queued 给连载任务的 resume_interrupted
    复活，但 mgmt job 只存在于内存队列，重启后永远没人认领，留着还会
    堵住同条目的去重闸门。workers 尚未启动，此时 queued 必是遗留。
    """
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOCK:
        candidates = [r["id"] for r in _RUNS.values()
                      if r.get("kind") == "mgmt" and r.get("status") == "queued"]
    for run_id in candidates:
        update_run(run_id, status="failed", ended_at=now,
                   error="interrupted at startup (mgmt auto-recovered)")
    return len(candidates)


def active_mgmt_run(entry_id):
    """该目录条目当前进行中（queued/running）的管理操作 run；没有则 None。

    供「同条目同时只跑一个安装/升级/卸载」去重闸使用：两个同名全局 npm
    并发装同一包会互锁（2026-09-18 codex 双开案）。内存索引即真源——
    重启后 recover_* 已把遗留 run 收成终态。
    """
    with LOCK:
        for r in _RUNS.values():
            if r.get("kind") == "mgmt" and r.get("entry_id") == entry_id \
                    and r.get("status") in ("queued", "running"):
                return dict(r)
    return None


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

# CLI 自身的历史/评审中间件不属于用户成果。它们通常落在工作目录根部，
# 只按隐藏目录过滤会漏掉 Aider 的 .aider.* 与流程生成的 *_review.json。
_PROCESS_ARTIFACT_NAMES = {
    ".aider.chat.history.md", ".aider.input.history", ".aider.input.history.md",
    ".aider.tags.cache.v4", ".aider.tags.cache.v3", ".aider.conf.yml",
}
_PROCESS_ARTIFACT_SUFFIXES = ("_review.json", ".review.json")
_PROCESS_ARTIFACT_PREFIXES = ("tutti_prompt_", "_tutti_prompt_")


def _is_process_artifact(rel_name):
    """判断工作目录文件是否为 CLI/评审过程产物，而非可交付成果。"""
    name = str(rel_name or "").replace("\\", "/")
    base = name.rsplit("/", 1)[-1].lower()
    if base in _PROCESS_ARTIFACT_NAMES:
        return True
    if base.startswith(".") and base.startswith(".aider"):
        return True
    if base.endswith(_PROCESS_ARTIFACT_SUFFIXES):
        return True
    if base.startswith(_PROCESS_ARTIFACT_PREFIXES):
        return True
    return False


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
            rel_name = str(p.relative_to(root)).replace("\\", "/")
            if _is_process_artifact(rel_name):
                continue
            files.append({"name": rel_name,
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


def _write_initial_story_bible(workdir, text):
    """创建任务时安全播种故事圣经。

    初始圣经写入发生在任务入队之前，多个请求可能同时指向同一个工作目录。
    旧逻辑先在锁外检查、再在锁外写入，两个请求都能通过检查，后写请求会
    覆盖先写的设定。这里把检查和写入放进同一进程锁，并在文件不存在时用
    ``O_EXCL`` 做最后一道独占创建；已有不同内容的文件始终拒绝覆盖。
    """
    p = _bible_path(workdir)
    if p is None:
        raise ValueError("故事圣经写入失败：工作目录不可用")
    with LOCK:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():
                if not p.is_file():
                    raise ValueError("故事圣经写入失败：目标路径不是文件")
                existing = runner.read_text_any_enc(p).strip()
                if existing:
                    if existing == text:
                        return
                    raise ValueError("工作目录已有 story-bible.md，请清空初始圣经输入或先编辑已有设定")
                # 空文件是合法的旧占位文件；在锁内覆盖，避免本服务内的并发写入。
                p.write_text(text, encoding="utf-8")
                return
            try:
                fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:
                # 其他进程可能刚创建了文件。相同内容可幂等返回；空文件仍可
                # 完成播种，非空不同内容绝不静默覆盖。
                if p.is_file():
                    existing = runner.read_text_any_enc(p).strip()
                    if existing == text:
                        return
                    if not existing:
                        p.write_text(text, encoding="utf-8")
                        return
                raise ValueError("工作目录已有 story-bible.md，请清空初始圣经输入或先编辑已有设定")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
        except ValueError:
            raise
        except OSError as e:
            raise ValueError("故事圣经写入失败：%s" % e)


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
    with LOCK:
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
        except OSError as e:
            return False, "写入失败: %s" % e
    # 故事圣经是提示词输入，写入后让 SSE/轮询端尽快看到新状态。
    bump_state()
    return True, ""


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
        _schedule_run_dir_cleanup(rid)
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
        _schedule_run_dir_cleanup(rid)
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
        _schedule_run_dir_cleanup(rid)
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
                v_scores = (prev.get("verdict") or {}).get("chapter_scores") or []
                # 重写未达标章：上一遍完整跑完但质量未过线（verdict.publishable
                # =False）时，未过线章的成稿与分数都不进继承——流水线对缺继承
                # 的章走正常起草+评审，等于只重写这几章；已过线章照常复用不烧
                # token。大纲始终继承（全章未过线时 done 清空也继承），保住全书
                # 结构——这正是「重写未达标章」按钮（前端 done+未达标态放行
                # retry）区别于断点续跑的语义。
                redo = set()
                if (prev.get("verdict") or {}).get("publishable") is False:
                    redo = {int(c["chapter"]) for c in v_scores
                            if not c.get("passed") and c.get("chapter") is not None}
                if redo:
                    done = [n for n in done if n not in redo]
                    v_scores = [c for c in v_scores if c.get("passed")]
                if done or redo:
                    if redo:
                        scores = v_scores          # 未达标重写：只带已过线章的分数
                    else:
                        scores = ((prev.get("verdict") or {}).get("chapter_scores")
                                  or prev.get("chapter_scores") or [])
                    run["inherit"] = {
                        "outline": outline,
                        "done_chapters": done,
                        # 分数优先取 verdict（整轮成功时的最终账）；
                        # failed/cancelled 的 run 没有 verdict，退回每章实时
                        # 落账的 chapter_scores——否则多轮失败恢复会把全部
                        # 已过线章节重新评审（实测一晚白烧数百万 token）
                        "chapter_scores": scores,
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
    # 分卷配置随链条沿用：卷边界必须跨批次稳定，否则同一卷会被续写批次重切
    if serial.get("volumes"):
        payload["serial"]["volumes"] = serial["volumes"]
    if serial.get("volume_chapters"):
        payload["serial"]["volume_chapters"] = serial["volume_chapters"]
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


def update_task_params(task_id, patch):
    """更新任务编排参数（对话条三件套：mode/thinking/direct_provider_id/direct_model）。
    只在任务未在跑时生效——运行中改参数不会影响当前执行，静默跳过避免误导。"""
    if not _valid_id(task_id):
        return False, "非法的任务 ID"
    with LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return False, "任务不存在"
        if task.get("status") in ("queued", "running"):
            return False, "任务运行中，参数不可改"
        changed = False
        mode = str((patch or {}).get("mode") or "").strip()
        if mode in ("auto", "fast", "expert", "manual"):
            if task.get("mode") != mode:
                task["mode"] = mode
                changed = True
        thinking = str((patch or {}).get("thinking") or "").strip()
        if thinking in ("auto", "low", "standard", "high"):
            if task.get("thinking") != thinking:
                task["thinking"] = thinking
                changed = True
        if task.get("engine") == "direct":
            pv = str((patch or {}).get("direct_provider_id") or "").strip()[:80]
            mv = str((patch or {}).get("direct_model") or "").strip()[:160]
            if mv and not pv:
                return False, "指定对话模型时必须同时指定厂商"
            if task.get("direct_provider_id") != pv or task.get("direct_model") != mv:
                task["direct_provider_id"] = pv
                task["direct_model"] = mv
                changed = True
        if changed:
            _save_json(paths.TASKS_DIR / (task_id + ".json"), task)
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


def stream_step(run_id, n, thinking=None, text=None, activity=None, min_secs=0.6):
    """步骤运行中实时落一段「思考过程/正文/活动」——对话时间线边跑边打印。

    直连对话此前整轮只有三点打字动画（32 秒黑箱，用户 2026-09-22 反馈）；
    模型的思维链与正文在流式回调里到达，这里节流落盘（默认 0.6s 一次），
    前端 2s 轮询一次读到的就是「它正在想什么」。

    节流按步骤记忆上次落盘时刻；收尾时 finish_step 会写入最终值并清掉
    live 标记。返回是否真的落盘（调用方可据此决定要不要做下一步）。
    """
    key = (run_id, int(n))
    now = time.time()
    with _STREAM_LOCK:
        last = _STREAM_TS.get(key, 0)
        if min_secs and now - last < min_secs:
            return False
        _STREAM_TS[key] = now
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return False
        for s in run["steps"]:
            if s["n"] != n:
                continue
            if thinking is not None:
                s["thinking"] = str(thinking)[-LIVE_TEXT_MAX:]
            if text is not None:
                s["stream"] = str(text)[-LIVE_TEXT_MAX:]
            if activity:
                acts = list(s.get("activity") or [])
                if not acts or acts[-1] != activity:
                    acts.append(str(activity)[:200])
                s["activity"] = acts[-8:]
            s["live"] = int(s.get("live") or 0) + 1   # 前端重绘签名（内容变即刷新）
            _save_json(paths.RUNS_DIR / run_id / "run.json", run)
            return True
    return False


def clear_stream_state(run_id=None, n=None):
    """清掉流式节流状态（步骤收尾/测试收场用），避免字典随长跑无限增长。"""
    with _STREAM_LOCK:
        if run_id is None:
            _STREAM_TS.clear()
            return
        for key in [k for k in _STREAM_TS if k[0] == run_id and (n is None or k[1] == n)]:
            _STREAM_TS.pop(key, None)


def finish_step(run_id, n, status, summary="", exit_code=None,
                cost_usd=0.0, tokens=0.0, duration_s=None, model=None, output=None,
                followups=None, thinking=None):
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return
        for s in run["steps"]:
            if s["n"] == n:
                s["status"] = status
                s["ended_at"] = time.strftime("%H:%M:%S")
                # 步骤摘要是 CLI 文本的汇聚点（失败时=错误尾巴，成功时=智能体结论）：
                # 落盘前统一清洗，避免 ANSI/覆写/乱码墙进 UI 与报告
                s["summary"] = runner.clean_cli_text(summary)
                s["exit_code"] = exit_code
                s["cost_usd"] = round(cost_usd, 4)
                s["tokens"] = tokens
                if model:
                    s["model"] = str(model)[:80]
                if duration_s is not None:
                    s["duration_s"] = round(duration_s, 1)
                if output is not None:
                    # output 是智能体正文（对话气泡直读）：只剥 ANSI，不做噪声折叠
                    # ——正文里的装饰性长串是作者写的，不能替它省略
                    s["output"] = runner.strip_ansi(str(output))[:6000]
                if thinking is not None:
                    s["thinking"] = runner.strip_ansi(str(thinking))[:LIVE_TEXT_MAX]
                if followups:
                    s["followups"] = list(followups)[:3]
                # 收尾即清运行中态：stream/activity/live 只是过程快照，留着会让
                # 前端把「已结束」的步骤仍当实时流渲染（也白占 run.json 体积）
                s.pop("stream", None)
                s.pop("activity", None)
                s.pop("live", None)
                break
        run["cost_usd"] = round(run.get("cost_usd", 0.0) + cost_usd, 4)
        run["tokens"] = run.get("tokens", 0) + tokens
        _save_json(paths.RUNS_DIR / run_id / "run.json", run)
    clear_stream_state(run_id, n)


# ---------------------------------------------------------------- 运行中指挥（消息信箱）
# 用户可在任务运行中往编排者「递话」：文字 + 附件（截图/文件）。消息先进信箱，
# 下一个智能体步骤开始前由 pipeline drain 出来注入 prompt——不插进正在跑的进程
# （无头 CLI 没有交互 stdin），而是在最近的轮间安全点生效。consumed 标记防重复注入。

def _ensure_messages(run):
    if "messages" not in run or not isinstance(run.get("messages"), list):
        run["messages"] = []
    return run["messages"]


def append_run_inject(run_id, text):
    """追加钩子注入文本到 run.hook_inject（message_submit 钩子通道）。
    直连轮间组装提示词时消费一次并清零。返回 bool。"""
    text = str(text or "").strip()
    if not text:
        return False
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return False
        old = str(run.get("hook_inject") or "")
        run["hook_inject"] = (old + ("\n\n" if old and text else "") + text)[:4000]
        _save_json(paths.RUNS_DIR / run_id / "run.json", run)
        return True


def pop_run_inject(run_id):
    """取出并清空 hook_inject（直连轮间提示词头一次性消费）。"""
    with LOCK:
        run = _RUNS.get(run_id)
        if not run:
            return ""
        txt = str(run.get("hook_inject") or "")
        if txt and "hook_inject" in run:
            run["hook_inject"] = ""
            try:
                _save_json(paths.RUNS_DIR / run_id / "run.json", run)
            except Exception:
                pass
        return txt


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
        # 先洗终端噪声（ANSI/覆写/乱码墙），再折叠遥测刷屏（时间戳不同的重复
        # WARN）；pretty 最后把 codex JSONL 事件流翻译成【消息】【命令】等可读行
        # ——顺序不能反：乱码墙会挤掉折叠分组，翻译也会把 ANSI 当正文
        text = runner.collapse_dup_lines(runner.clean_cli_text(text))
        if pretty:
            text = runner.pretty_cli_log(text)
        return text
    except Exception:
        return ""
