# -*- coding: utf-8 -*-
"""任务驾驶舱（/board 大屏）的轻量聚合数据源。

大屏 4 秒轮询：不能拉 /api/state 全量（MB 级），也不能每次全量扫 usage
台账（summary 全历史读入），所以这里只取大屏要看的字段，并把 usage 聚合
缓存 8 秒。只读 store/usage/jobs/health，不落新盘。
"""
from __future__ import annotations

import time

_CACHE = {"ts": 0.0, "data": None}
_TTL = 8.0

_TAIL_MAX = 150          # 跑马灯尾行截断
_TAIL_LINES = 6          # 实时流窗口行数
_TAIL_LINE_CHARS = 56    # 实时流单行截断（卡内 11px 等宽不换行的安全宽度）
_RECENT_LIMIT = 10       # 最近完成/失败条数
_RUN_WINDOW = 300        # 今日计数扫描的运行窗口（id 字典序=时间序）


def payload():
    now = time.time()
    if _CACHE["data"] is not None and now - _CACHE["ts"] < _TTL:
        return _CACHE["data"]
    data = _build()
    _CACHE["ts"] = now
    _CACHE["data"] = data
    return data


def _tail(step):
    """步骤实时流（正文优先，其次思考）的最后一行，给大屏跑马灯。"""
    for key in ("stream", "thinking"):
        v = step.get(key)
        if isinstance(v, str) and v.strip():
            lines = [ln.strip() for ln in v.strip().splitlines() if ln.strip()]
            if lines:
                return lines[-1][:_TAIL_MAX]
    return ""


def _live_block(step):
    """步骤实时流窗口：尾部多行（正文优先，其次思考），供大屏看它在想什么。

    返回 (来源标签 code, 文本)；标签是 code（output/thinking），由前端 i18n
    翻译——大屏要做多语言，后端不下发中文。都没有返回 ("", "")。
    """
    for key, code in (("stream", "output"), ("thinking", "thinking")):
        v = step.get(key)
        if not (isinstance(v, str) and v.strip()):
            continue
        lines = [ln.strip()[:_TAIL_LINE_CHARS] for ln in v.strip().splitlines()]
        lines = [ln for ln in lines if ln]
        if lines:
            return code, "\n".join(lines[-_TAIL_LINES:])
    return "", ""


def _model_provider_map():
    """模型名 → 厂商名（enabled 优先，同模型多厂商取 priority 最靠前的）。"""
    try:
        from . import modelhub
        rows = modelhub.models_view()
    except Exception:
        return {}
    best = {}
    for r in rows:
        name = r.get("name")
        if not name or not r.get("enabled", False):
            continue
        pri = r.get("priority", 0)
        cur = best.get(name)
        if cur is None or pri < cur[0]:
            best[name] = (pri, r.get("provider_name") or r.get("provider_id") or "")
    return {k: v[1] for k, v in best.items()}


def _parse_ts(s):
    try:
        return time.mktime(time.strptime(str(s), "%Y-%m-%d %H:%M:%S"))
    except (ValueError, TypeError):
        return 0.0


def _duration_s(run):
    """运行耗时（秒）：ended-started，缺 started 用 created 兜底。"""
    end = _parse_ts(run.get("ended_at"))
    start = _parse_ts(run.get("started_at")) or _parse_ts(run.get("created_at"))
    if end > 0 and start > 0 and end >= start:
        return int(end - start)
    return None


def _step_view(steps, prov_map):
    """进度概要：完成数 + 当前运行中步骤（含模型/厂商/实时流）。"""
    done = sum(1 for s in steps if s.get("status") == "done")
    cur = next((s for s in steps if s.get("status") == "running"), None)
    cur_v = None
    if cur:
        live_label, live_text = _live_block(cur)
        model = str(cur.get("model") or "")[:60]
        cur_v = {"n": cur.get("n"), "role": cur.get("role") or "",
                 "agent": cur.get("agent_label") or cur.get("agent") or "",
                 "summary": (cur.get("summary") or "")[:80],
                 "tail": _tail(cur),
                 "model": model, "provider": prov_map.get(model, "") if model else "",
                 "live_label": live_label, "live_text": live_text}
    return done, len(steps), cur_v


def _build():
    from . import flows, health, jobs, store, usage

    flow_map = {}
    try:
        for f in flows.list_flows():
            flow_map[f.get("id")] = f
    except Exception:
        pass

    prov_map = _model_provider_map()
    today = time.strftime("%Y-%m-%d")
    tasks = store.list_tasks(200, archived=False)
    latest = store.latest_run_by_task()

    running, queued = [], []
    for t in tasks:
        tid = t.get("id")
        run = latest.get(tid)
        status = t.get("status")
        if status == "running" and run and run.get("status") == "running":
            done, total, cur = _step_view(run.get("steps") or [], prov_map)
            running.append({
                "task_id": tid, "run_id": run.get("id"),
                "title": t.get("title") or "", "goal": (t.get("goal") or "")[:120],
                "type": t.get("type") or "",
                "type_name": (flow_map.get(t.get("type")) or {}).get("name") or (t.get("type") or ""),
                "status": status,
                "started_at": run.get("started_at") or run.get("created_at") or "",
                "steps_done": done, "steps_total": total,
                "current": cur,
                "run_tokens": run.get("tokens") or 0,
                "run_cost_usd": round(float(run.get("cost_usd") or 0.0), 4),
                "estimate_s": run.get("estimated_duration_s"),
            })
        elif status == "queued":
            queued.append({
                "task_id": tid, "run_id": (run or {}).get("id"),
                "title": t.get("title") or "",
                "type_name": (flow_map.get(t.get("type")) or {}).get("name") or (t.get("type") or ""),
                "created_at": (run or {}).get("created_at") or "",
            })
    running.sort(key=lambda c: _parse_ts(c["started_at"]))
    queued.sort(key=lambda c: _parse_ts(c["created_at"]))

    runs = store.list_runs(_RUN_WINDOW)
    today_done = today_failed = 0
    recent = []
    for r in runs:
        st = r.get("status")
        ended = str(r.get("ended_at") or "")
        if st in ("done", "failed", "cancelled") and ended.startswith(today):
            if st == "done":
                today_done += 1
            elif st == "failed":
                today_failed += 1
        if len(recent) < _RECENT_LIMIT and st in ("done", "failed", "cancelled") and ended:
            recent.append({
                "run_id": r.get("id"), "task_id": r.get("task_id"),
                "title": r.get("title") or "", "status": st,
                "ended_at": ended, "duration_s": _duration_s(r),
                "error": (r.get("error") or "")[:80],
            })

    trend, models, usage_today = [], [], {"tokens": 0, "calls": 0, "cost_usd": 0.0}
    try:
        s = usage.summary(days=7)
        trend = [{"day": d.get("day"), "tokens": d.get("tokens") or 0}
                 for d in (s.get("by_day") or [])]
        for row in (s.get("by_model") or [])[:5]:
            if not row.get("tokens"):
                continue
            models.append({"model": row.get("key") or "?",
                           "tokens": row.get("tokens") or 0,
                           "calls": row.get("calls") or 0})
        for d in s.get("by_day") or []:
            if str(d.get("day")) == today:
                usage_today = {"tokens": d.get("tokens") or 0,
                               "calls": d.get("calls") or 0,
                               "cost_usd": round(float(d.get("cost_usd") or 0.0), 3)}
                break
    except Exception:
        pass

    return {
        "now": time.strftime("%Y-%m-%d %H:%M:%S"),
        "today": today,
        "running": running,
        "queued": queued,
        "today_done": today_done,
        "today_failed": today_failed,
        "recent": recent,
        "trend": trend,
        "models": models,
        "usage_today": usage_today,
        "pool": jobs.workers_info(),
        "health": health.snapshot().get("providers") or [],
    }
