# -*- coding: utf-8 -*-
"""用量台账：每次真实 LLM 调用（CLI 智能体 / 编排者直连 API）追加一条记录，
支持按天/工具(CLI)/智能体/模型/角色/任务类型多维聚合——用量统计页的数据源。

落盘：data/usage/usage-YYYYMM.jsonl（按月分文件，append-only，一行一记录）。
并发安全：全局锁 + 追加写；读侧每次全量扫描再聚合（调用频次为分钟级，
文件规模可控；聚合在请求线程内完成，不引入后台任务）。

记一条的入口是 record()：字段残缺不抛错（埋点失败绝不能影响任务执行）。
"""
from __future__ import annotations

import json
import threading
import time

from . import paths

LOCK = threading.RLock()

# 维度 → 显示名（聚合接口与前端共用）
DIMENSIONS = {
    "tool": "工具（CLI）",
    "agent": "智能体",
    "model": "模型",
    "role": "步骤角色",
    "task_type": "任务类型",
}

FIELDS = ("ts", "day", "run_id", "step", "task_id", "task_type", "role", "agent", "agent_label",
          "tool", "model", "provider", "ok", "duration_s",
          "input", "output", "cached", "reasoning", "total", "cost_usd", "source")


def _month_file(day):
    return paths.USAGE_DIR / ("usage-%s.jsonl" % day[:7].replace("-", ""))


def _parse_int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _parse_float(v):
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _parse_bool(v):
    if v is None:
        return False
    return bool(v)


def record(source="", run_id="", task_id="", task_type="", role="", step=0,
           agent="", agent_label="", tool="", model="", provider="",
           ok=True, duration_s=0.0, cost_usd=0.0, usage=None):
    """追加一条用量记录。usage 为细分 dict：{input, output, cached, reasoning, total}；
    缺省字段按 0 处理。任何异常都吞掉——统计永远不能拖垮业务调用方。

    step 为该运行内的步骤号：与 backfill_from_runs 的去重键一致，缺了会导致
    启动回填把同一步骤重复入账。
    """
    try:
        u = usage or {}
        rec = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "day": time.strftime("%Y-%m-%d"),
            "source": str(source or "pipeline")[:24],
            "run_id": str(run_id or "")[:64],
            "step": _parse_int(step),
            "task_id": str(task_id or "")[:64],
            "task_type": str(task_type or "")[:32] or "unknown",
            "role": str(role or "")[:40] or "unknown",
            "agent": str(agent or "")[:40] or "unknown",
            "agent_label": str(agent_label or "")[:60],
            "tool": str(tool or "")[:24] or "unknown",
            "model": str(model or "")[:80] or "(默认)",
            "provider": str(provider or "")[:60],
            "ok": _parse_bool(ok),
            "duration_s": round(_parse_float(duration_s), 1),
            "cost_usd": round(_parse_float(cost_usd), 4),
        }
        inp = max(0, _parse_int(u.get("input")))
        out = max(0, _parse_int(u.get("output")))
        cach = max(0, _parse_int(u.get("cached")))
        reas = max(0, _parse_int(u.get("reasoning")))
        total = _parse_int(u.get("total")) or (inp + out + cach)
        rec.update({"input": inp, "output": out, "cached": cach,
                    "reasoning": reas, "total": total})
        line = json.dumps(rec, ensure_ascii=False)
        with LOCK:
            paths.USAGE_DIR.mkdir(parents=True, exist_ok=True)
            with open(_month_file(rec["day"]), "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


def backfill_from_runs():
    """把历史 run.json 里已记录的 token 用量回填进台账（启动时调用，幂等）。

    埋点是后加的：此前的运行只在 run.json 的步骤里存了 tokens/cost_usd 总量，
    没有输入/输出/缓存细分，所以回填记录 input/output/cached 记 0、只有 total，
    并以 source="backfill" 标记便于区分。已回填的 (run_id, 步骤号) 会跳过，
    重复启动不会产生重复记录。
    """
    try:
        runs_dir = paths.RUNS_DIR
        if not runs_dir.is_dir():
            return 0
        # 已入账的 (run_id, role, 步骤号) 集合——含真实埋点与既往回填
        seen = set()
        for r in _iter_records(0):
            seen.add((str(r.get("run_id") or ""), str(r.get("role") or ""),
                      _parse_int(r.get("step"))))
        added = 0
        # 旧 run 未存任务类型，从 tasks/*.json 补齐（缺了就记 unknown）
        task_types = {}
        try:
            for tp in paths.TASKS_DIR.glob("*.json"):
                try:
                    t = json.loads(tp.read_text(encoding="utf-8"))
                    task_types[t.get("id")] = t.get("type") or ""
                except Exception:
                    continue
        except Exception:
            pass
        for p in sorted(runs_dir.glob("*/run.json")):
            try:
                run = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(run, dict):
                continue
            run_id = str(run.get("id") or p.parent.name)
            day = str(run.get("started_at") or run.get("created_at") or "")[:10]
            if not day:
                continue
            for s in (run.get("steps") or []):
                if not isinstance(s, dict):
                    continue
                total = _parse_int(s.get("tokens"))
                if total <= 0:
                    continue          # 无 token 的步骤（验证/合并/mock）不入账
                n = _parse_int(s.get("n"))
                key = (run_id, str(s.get("role") or ""), n)
                if key in seen:
                    continue
                seen.add(key)
                rec = {
                    "ts": "%s %s" % (day, str(s.get("started_at") or "00:00:00")[:8]),
                    "day": day, "source": "backfill", "run_id": run_id,
                    "step": n,
                    "task_id": str(run.get("task_id") or ""),
                    "task_type": task_types.get(str(run.get("task_id") or "")) or "unknown",
                    "role": str(s.get("role") or "")[:40] or "unknown",
                    "agent": str(s.get("agent") or "")[:40] or "unknown",
                    "agent_label": str(s.get("agent_label") or "")[:60],
                    "tool": _tool_of(str(s.get("agent") or "")),
                    "model": str(s.get("model") or "")[:80] or "(历史未记录)",
                    "provider": "",
                    "ok": str(s.get("status") or "") == "done",
                    "duration_s": round(_parse_float(s.get("duration_s")), 1),
                    "cost_usd": round(_parse_float(s.get("cost_usd")), 4),
                    "input": 0, "output": 0, "cached": 0, "reasoning": 0,
                    "total": total,
                }
                with LOCK:
                    paths.USAGE_DIR.mkdir(parents=True, exist_ok=True)
                    with open(_month_file(day), "a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                added += 1
        return added
    except Exception:
        return 0


# 智能体 id → 工具 kind（回填时旧记录没有 tool 字段，只能从 id 推断）
_AGENT_TOOL = {"codex-cli": "codex", "claude-code": "claude", "qwen-cli": "qwen",
               "qwencode": "qwen", "opencode": "opencode", "aider": "aider",
               "orchestrator": "orchestrator"}


def _tool_of(agent_id):
    a = str(agent_id or "").lower()
    if a in _AGENT_TOOL:
        return _AGENT_TOOL[a]
    for k, v in _AGENT_TOOL.items():
        if a.startswith(k):
            return v
    return a or "unknown"


_HOURLY_CACHE = {"ts": 0.0, "val": {}}
_HOURLY_TTL = 60.0   # 秒：路由调用频繁但台账追加低频，60s 缓存足够新鲜


def agent_tokens_recent(agent, hours=1):
    """该智能体近 N 小时的 token 总量（路由配额惩罚用）。按 ts 前缀过滤，
    60 秒 TTL 进程内缓存——运行中每次路由都查也只扫两天的台账。"""
    try:
        agent = str(agent or "")
        now = time.time()
        if now - _HOURLY_CACHE["ts"] > _HOURLY_TTL:
            bound = time.strftime("%Y-%m-%d %H:%M:%S",
                                  time.localtime(now - hours * 3600.0))
            total = {}
            for r in _iter_records(2):
                if str(r.get("ts") or "") < bound:
                    continue
                a = str(r.get("agent") or "")
                total[a] = total.get(a, 0) + max(0, _parse_int((r.get("usage") or {}).get("total")))
            _HOURLY_CACHE.update(ts=now, val=total)
        return int(_HOURLY_CACHE["val"].get(agent) or 0)
    except Exception:
        return 0


def _iter_records(days):
    """按时间范围读取台账（days=0 表示全部）。返回按写入顺序的记录列表。"""
    out = []
    try:
        files = sorted(paths.USAGE_DIR.glob("usage-*.jsonl")) if paths.USAGE_DIR.is_dir() else []
    except Exception:
        return out
    since_day = ""
    if days:
        import datetime
        d = (datetime.date.today() - datetime.timedelta(days=int(days) - 1)).isoformat()
        since_day = d
    for p in files:
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if not isinstance(r, dict):
                    continue
                if since_day and str(r.get("day", "")) < since_day:
                    continue
                out.append(r)
        except Exception:
            continue
    return out


def _num(r, key):
    return _parse_int(r.get(key))


def _group(records, key):
    """按 key 聚合：{name: {calls, ok, tokens, input, output, cached, cache_rate, cost_usd, duration_s}}。"""
    groups = {}
    for r in records:
        name = str(r.get(key) or "") or "unknown"
        g = groups.setdefault(name, {"calls": 0, "ok": 0, "tokens": 0,
                                     "input": 0, "output": 0, "cached": 0,
                                     "cost_usd": 0.0, "duration_s": 0.0})
        g["calls"] += 1
        if r.get("ok"):
            g["ok"] += 1
        g["tokens"] += _num(r, "total")
        g["input"] += _num(r, "input")
        g["output"] += _num(r, "output")
        g["cached"] += _num(r, "cached")
        g["cost_usd"] = round(g["cost_usd"] + float(r.get("cost_usd") or 0.0), 4)
        g["duration_s"] = round(g["duration_s"] + float(r.get("duration_s") or 0.0), 1)
    # §07 验收指标：缓存命中率（cached / (input + cached)），与 totals.cache_rate 同口径
    for g in groups.values():
        denom = g["input"] + g["cached"]
        g["cache_rate"] = round(g["cached"] * 100.0 / denom, 1) if denom else 0.0
    return groups


def _dim_rows(records, key):
    rows = []
    for name, g in _group(records, key).items():
        rows.append({"key": name, **g})
    rows.sort(key=lambda x: -x["tokens"])
    return rows


def summary(days=30, recent_limit=30):
    """多维聚合。days=0 表示全部历史。by_day 连续补零，方便前端直接画趋势。"""
    import datetime
    with LOCK:
        records = _iter_records(days)
    now = datetime.date.today()
    span = int(days) if days else 0
    since = (now - datetime.timedelta(days=max(span, 1) - 1)) if span else None

    day_groups = _group(records, "day")
    by_day = []
    if span:
        for i in range(span):
            d = (now - datetime.timedelta(days=span - 1 - i)).isoformat()
            g = day_groups.get(d, {})
            by_day.append({"day": d, "calls": g.get("calls", 0),
                           "tokens": g.get("tokens", 0),
                           "input": g.get("input", 0), "output": g.get("output", 0),
                           "cached": g.get("cached", 0),
                           "cache_rate": g.get("cache_rate", 0.0),
                           "cost_usd": round(g.get("cost_usd", 0.0), 4)})
    else:
        for d in sorted(day_groups):
            g = day_groups[d]
            by_day.append({"day": d, "calls": g["calls"], "tokens": g["tokens"],
                           "input": g["input"], "output": g["output"],
                           "cached": g.get("cached", 0),
                           "cache_rate": g.get("cache_rate", 0.0),
                           "cost_usd": g["cost_usd"]})

    totals = {
        "calls": len(records),
        "ok": sum(1 for r in records if r.get("ok")),
        "tokens": sum(_num(r, "total") for r in records),
        "input": sum(_num(r, "input") for r in records),
        "output": sum(_num(r, "output") for r in records),
        "cached": sum(_num(r, "cached") for r in records),
        "reasoning": sum(_num(r, "reasoning") for r in records),
        "cost_usd": round(sum(float(r.get("cost_usd") or 0.0) for r in records), 4),
        "duration_s": round(sum(float(r.get("duration_s") or 0.0) for r in records), 1),
    }
    totals["failed"] = totals["calls"] - totals["ok"]
    totals["days_active"] = len(day_groups)
    totals["avg_tokens_per_call"] = int(totals["tokens"] / totals["calls"]) if totals["calls"] else 0
    denom = totals["input"] + totals["cached"]
    totals["cache_rate"] = round(totals["cached"] * 100.0 / denom, 1) if denom else 0.0

    recent = sorted(records, key=lambda r: str(r.get("ts", "")), reverse=True)[:recent_limit]
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "range": {"days": span, "since": since.isoformat() if since else ""},
        "totals": totals,
        "by_day": by_day,
        "by_tool": _dim_rows(records, "tool"),
        "by_agent": _dim_rows(records, "agent"),
        "by_model": _dim_rows(records, "model"),
        "by_role": _dim_rows(records, "role"),
        "by_task_type": _dim_rows(records, "task_type"),
        "recent": recent,
    }
