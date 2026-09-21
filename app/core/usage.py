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
import math
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


def _quality_file(day):
    return paths.USAGE_DIR / ("routing-quality-%s.jsonl" % day[:7].replace("-", ""))


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
            _ROUTING_CACHE["data"].clear()
            _ROUTING_RECORDS_CACHE.clear()
            _HOURLY_CACHE["ts"] = 0.0
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
_ROUTING_CACHE = {"data": {}}
_ROUTING_RECORDS_CACHE = {}
_ROUTING_TTL = 15.0


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
                # record() 将规范化后的 token 总数落在顶层 ``total``；旧实现
                # 误读不存在的嵌套 usage 字段，导致配额路由永远认为本小时
                # 用量为 0，超过供应商额度也不会降权。
                total[a] = total.get(a, 0) + max(0, _parse_int(r.get("total")))
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
    since_month = since_day[:7].replace("-", "") if since_day else ""
    for p in files:
        if since_month and p.stem.rsplit("-", 1)[-1] < since_month:
            continue
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


def _iter_quality_records(days):
    """读取独立质量台账；不混入用量统计的调用数、token 和成本。"""
    out = []
    try:
        files = sorted(paths.USAGE_DIR.glob("routing-quality-*.jsonl")) \
            if paths.USAGE_DIR.is_dir() else []
    except Exception:
        return out
    since_day = ""
    if days:
        import datetime
        since_day = (datetime.date.today()
                     - datetime.timedelta(days=int(days) - 1)).isoformat()
    since_month = since_day[:7].replace("-", "") if since_day else ""
    for path in files:
        if since_month and path.stem.rsplit("-", 1)[-1] < since_month:
            continue
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict) and (not since_day or row.get("day", "") >= since_day):
                    out.append(row)
        except Exception:
            continue
    return out


def _routing_records(days):
    """同一轮候选评分共享一次台账解析，避免每个候选重复扫全盘。"""
    key = (str(paths.USAGE_DIR), int(days))
    now = time.time()
    with LOCK:
        cached = _ROUTING_RECORDS_CACHE.get(key)
        if cached and now - cached[0] <= _ROUTING_TTL:
            return list(cached[1]), list(cached[2])
    calls, quality = _iter_records(days), _iter_quality_records(days)
    with LOCK:
        _ROUTING_RECORDS_CACHE[key] = (now, list(calls), list(quality))
    return calls, quality


def record_quality_for_run(run_id, quality_ok, agent=""):
    """把验收结论归因到最终实际产出者，不重复计入用量。"""
    try:
        run_id = str(run_id or "")[:64]
        if not run_id:
            return 0
        calls = [r for r in _iter_records(2)
                 if r.get("run_id") == run_id and r.get("ok") is True]
        prefixes = ("implement", "fix", "draft", "revise", "polish", "direct", "author")
        calls = [r for r in calls if str(r.get("role") or "").lower().startswith(prefixes)]
        agent = str(agent or "")[:40]
        unique = {}
        for row in calls:
            key = tuple(str(row.get(k) or "") for k in
                        ("task_id", "task_type", "role", "agent", "model", "provider"))
            unique[key] = row
        if not unique:
            return 0
        existing = {(r.get("run_id"), r.get("role"), r.get("agent"),
                     r.get("model"), r.get("provider"))
                    for r in _iter_quality_records(2)}
        day = time.strftime("%Y-%m-%d")
        rows = []
        for row in unique.values():
            identity = (run_id, row.get("role"), row.get("agent"),
                        row.get("model"), row.get("provider"))
            if identity in existing:
                continue
            # 成功调用不等于产出通过。换将前的实现者即使传输成功，也已被质量
            # 门淘汰，必须留下负样本；最终实际产出者才继承本次验收结论。
            row_quality_ok = bool(quality_ok)
            if agent and str(row.get("agent") or "") != agent:
                row_quality_ok = False
            rows.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "day": day,
                         "run_id": run_id, "task_id": row.get("task_id") or "",
                         "task_type": row.get("task_type") or "unknown",
                         "role": row.get("role") or "unknown",
                         "agent": row.get("agent") or "unknown",
                         "model": row.get("model") or "(默认)",
                         "provider": row.get("provider") or "",
                         "quality_ok": row_quality_ok})
        if not rows:
            return 0
        with LOCK:
            paths.USAGE_DIR.mkdir(parents=True, exist_ok=True)
            with open(_quality_file(day), "a", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            _ROUTING_CACHE["data"].clear()
            _ROUTING_RECORDS_CACHE.clear()
        return len(rows)
    except Exception:
        return 0


def _num(r, key):
    return _parse_int(r.get(key))


def _percentile(values, percentile):
    """线性插值百分位，空样本返回 0。"""
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    pos = (len(ordered) - 1) * float(percentile)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return round(ordered[lo], 2)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo), 2)


def routing_stats(task_type="", role="", agent="", model="", provider="",
                  days=90, min_samples=3):
    """返回路由用的近期真实运行指标。

    只读取脱敏用量台账；精确维度样本不足时依次回退到任务+角色、任务、
    全局，成功率使用 Beta(3, 1) 平滑，避免单次失败永久压低新候选。
    """
    try:
        task_type = str(task_type or "")[:32]
        role = str(role or "")[:40]
        agent = str(agent or "")[:40]
        model = str(model or "")[:80]
        provider = str(provider or "")[:60]
        days = max(0, int(days))
        min_samples = max(1, int(min_samples))
    except (TypeError, ValueError):
        days, min_samples = 90, 3
    # DATA_DIR 可在测试、多实例或运行时配置切换；纳入缓存键，避免跨目录
    # 复用另一套台账的线上指标。
    key = (str(paths.DATA_DIR), task_type, role, agent, model, provider,
           days, min_samples)
    now = time.time()
    with LOCK:
        cached = _ROUTING_CACHE["data"].get(key)
        if cached and now - cached[0] <= _ROUTING_TTL:
            return dict(cached[1])
    records, quality_records = _routing_records(days)

    def matches(record, filters):
        return all(str(record.get(field) or "") == value
                   for field, value in filters.items() if value)

    # 先保留所有已提供的维度；后续逐层放宽，确保模型和 CLI 都能共享一套查询。
    exact = {"task_type": task_type, "role": role, "agent": agent,
             "model": model, "provider": provider}
    fallbacks = [(exact, "exact")]
    task_role = {"task_type": task_type, "role": role}
    if task_role != exact:
        fallbacks.append((task_role, "task-role"))
    if task_type:
        fallbacks.append(({"task_type": task_type}, "task"))
    fallbacks.append(({}, "global"))
    selected, label = [], "global"
    for filters, candidate_label in fallbacks:
        candidate = [r for r in records if matches(r, filters)]
        if len(candidate) >= min_samples or (candidate_label == "global" and candidate):
            selected, label = candidate, candidate_label
            break
    chosen_filters = next((filters for filters, candidate_label in fallbacks
                           if candidate_label == label), {})
    quality_selected = [r for r in quality_records if matches(r, chosen_filters)]
    success_rows = quality_selected or selected
    success_key = "quality_ok" if quality_selected else "ok"
    successes = sum(1 for r in success_rows if bool(r.get(success_key)))
    durations = [max(0.0, _parse_float(r.get("duration_s"))) for r in selected]
    costs = [max(0.0, _parse_float(r.get("cost_usd"))) for r in selected]
    samples = len(selected)
    success_samples = len(success_rows)
    result = {
        "samples": samples,
        "quality_samples": len(quality_selected),
        "success_samples": success_samples,
        "successes": successes,
        "success_rate": round((successes + 3.0) / (success_samples + 4.0), 4),
        "p50_duration_s": _percentile(durations, 0.50),
        "p95_duration_s": _percentile(durations, 0.95),
        "avg_cost_usd": round(sum(costs) / samples, 6) if samples else 0.0,
        "fallback": label,
    }
    with LOCK:
        _ROUTING_CACHE["data"][key] = (now, dict(result))
    return result


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


def _model_display_map(records):
    """模型名归一映射 {casefold: 展示名}：同一模型不同大小写（GLM-5.3-Flash 与
    glm-5.3-flash）会被拆成两行统计，这里把 casefold 相同的归为一组，
    展示名取组内出现次数最多的原始写法（平局取更长写法，信息更全）。"""
    canon = {}
    for r in records:
        name = str(r.get("model") or "") or "unknown"
        c = canon.setdefault(name.casefold(), {})
        c["total"] = c.get("total", 0) + 1
        c["names"] = c.get("names") or {}
        c["names"][name] = (c["names"].get(name) or 0) + 1
    display = {}
    for k, c in canon.items():
        display[k] = max(c["names"].items(),
                         key=lambda kv: (kv[1], len(kv[0])))[0]
    return display


def _remap_model(records, display):
    """把 records 的 model 字段替换为归一后的展示名（不改动原记录）。"""
    out = []
    for r in records:
        name = str(r.get("model") or "") or "unknown"
        r2 = dict(r)
        r2["model"] = display.get(name.casefold(), name)
        out.append(r2)
    return out


def _dim_rows(records, key, normalize=False):
    rows = []
    if normalize:
        records = _remap_model(records, _model_display_map(records))
    for name, g in _group(records, key).items():
        rows.append({"key": name, **g})
    rows.sort(key=lambda x: -x["tokens"])
    return rows


def _streaks(dayset):
    """连续活跃天数：current=从今天（今天没用量则从昨天）往回的连续天数；
    longest=历史上最长连续段。dayset 为 ISO 日期字符串集合。"""
    import datetime
    ds = set()
    for s in dayset:
        try:
            ds.add(datetime.date.fromisoformat(str(s)))
        except (ValueError, TypeError):
            continue
    if not ds:
        return 0, 0
    today = datetime.date.today()
    anchor = today if today in ds else today - datetime.timedelta(days=1)
    current = 0
    d = anchor
    while d in ds:
        current += 1
        d -= datetime.timedelta(days=1)
    longest = run = 0
    prev = None
    for d in sorted(ds):
        run = run + 1 if (prev is not None and (d - prev).days == 1) else 1
        longest = max(longest, run)
        prev = d
    return current, longest


def summary(days=30, recent_limit=30):
    """多维聚合。days=0 表示全部历史。by_day 连续补零，方便前端直接画趋势。

    一次全量扫描后在内存里按范围过滤（_iter_records 本就逐文件全读，
    这样热力图/连续天数所需的全历史数据不再二次扫描）：
    - all_by_day：全历史按日 tokens（热力图、连续天数）；
    - by_day_model：范围内按日按模型 tokens（多模型趋势折线，模型名已归一）；
    - totals 增 peak_tokens / max_duration_s / streak_current / streak_longest。
    """
    import datetime
    with LOCK:
        all_records = _iter_records(0)
    now = datetime.date.today()
    span = int(days) if days else 0
    since = (now - datetime.timedelta(days=max(span, 1) - 1)) if span else None
    if span:
        since_s = since.isoformat()
        records = [r for r in all_records if str(r.get("day") or "") >= since_s]
    else:
        records = all_records

    # 模型名归一：维度行与按日趋势共用同一展示名映射
    model_display = _model_display_map(records)
    model_records = _remap_model(records, model_display)

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

    # 按日按模型 tokens（范围内；与 by_day 同一天序列，无数据天给空表）
    day_model = {}
    for r in model_records:
        d = str(r.get("day") or "")
        if not d:
            continue
        m = day_model.setdefault(d, {})
        name = str(r.get("model") or "unknown")
        m[name] = m.get(name, 0) + _num(r, "total")
    by_day_model = [{"day": bd["day"], "models": day_model.get(bd["day"], {})}
                    for bd in by_day]

    # 全历史按日 tokens（热力图 / 连续天数；不补零，按日期排序）
    all_day_groups = _group(all_records, "day")
    all_by_day = [{"day": d, "tokens": all_day_groups[d]["tokens"]}
                  for d in sorted(all_day_groups)]

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
    totals["peak_tokens"] = max((bd["tokens"] for bd in by_day), default=0)
    totals["max_duration_s"] = round(max((float(r.get("duration_s") or 0.0) for r in records), default=0.0), 1)
    cur, lng = _streaks(str(r.get("day") or "") for r in all_records)
    totals["streak_current"] = cur
    totals["streak_longest"] = lng

    recent = sorted(records, key=lambda r: str(r.get("ts", "")), reverse=True)[:recent_limit]
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "range": {"days": span, "since": since.isoformat() if since else ""},
        "totals": totals,
        "by_day": by_day,
        "by_day_model": by_day_model,
        "all_by_day": all_by_day,
        "by_tool": _dim_rows(records, "tool"),
        "by_agent": _dim_rows(records, "agent"),
        "by_model": _dim_rows(records, "model", normalize=True),
        "by_role": _dim_rows(records, "role"),
        "by_task_type": _dim_rows(records, "task_type"),
        "recent": recent,
    }


_DURATION_BASELINES = {
    "direct": 90, "rank_scan": 240, "email": 180, "weekly_report": 240,
    "translation": 240, "speech": 360, "video_script": 360, "article": 480,
    "doc": 480, "resume": 420, "code": 600, "tech_proposal": 720,
    "research": 900, "novel": 720, "serial_novel": 2400,
}


def estimate(task_type="", days=90, mode="auto", thinking="standard", rounds=None):
    """同类任务开跑前成本与耗时预估（数据源为本地用量台账）。

    同一 run 的多条步骤记录加总为一个样本，优先用成功 run，给中位数/平均/P90。
    无历史样本时使用流程级保守基线；模式与思考程度只做透明倍率修正。"""
    span = max(1, min(3650, int(days or 90)))
    with LOCK:
        records = _iter_records(span)
    runs = {}
    for r in records:
        tt = str(r.get("task_type") or "unknown")
        if task_type and tt != task_type:
            continue
        rid = str(r.get("run_id") or "")
        if not rid:
            continue
        g = runs.setdefault(rid, {"tokens": 0, "cost": 0.0,
                                  "duration": 0.0, "ok": False})
        g["tokens"] += _num(r, "total")
        g["cost"] += _parse_float(r.get("cost_usd"))
        g["duration"] += max(0.0, _parse_float(r.get("duration_s")))
        if r.get("ok"):
            g["ok"] = True
    ok_runs = [g for g in runs.values() if g["ok"]]
    basis = ok_runs or list(runs.values())
    mode_factor = {"fast": 0.60, "auto": 1.0, "expert": 1.65,
                   "manual": 1.0}.get(str(mode or "auto"), 1.0)
    thinking_factor = {"low": 0.78, "standard": 1.0, "high": 1.45,
                       "auto": 1.0}.get(str(thinking or "standard"), 1.0)
    factor = mode_factor * thinking_factor
    try:
        if rounds is not None and int(rounds) > 2:
            factor *= 1.0 + min(3, int(rounds) - 2) * 0.18
    except (TypeError, ValueError):
        pass
    if not basis:
        baseline = _DURATION_BASELINES.get(task_type, 480)
        median_s = max(30, int(baseline * factor))
        return {"task_type": task_type or "", "days": span, "samples": 0,
                "estimated_duration_s": median_s,
                "p90_duration_s": max(median_s + 30, int(median_s * 1.7)),
                "duration_source": "baseline"}
    toks = sorted(g["tokens"] for g in basis)
    costs = sorted(g["cost"] for g in basis)
    durations = sorted(g["duration"] for g in basis if g["duration"] > 0)
    n = len(toks)
    mid = n // 2
    med = toks[mid] if n % 2 else (toks[mid - 1] + toks[mid]) / 2.0
    med_c = costs[mid] if n % 2 else (costs[mid - 1] + costs[mid]) / 2.0
    import datetime
    import math
    if durations:
        dn = len(durations)
        dmid = dn // 2
        dmed = (durations[dmid] if dn % 2
                else (durations[dmid - 1] + durations[dmid]) / 2.0)
        dp90 = durations[max(0, min(dn - 1, math.ceil(0.9 * dn) - 1))]
        duration_source = "history"
    else:
        dmed = _DURATION_BASELINES.get(task_type, 480)
        dp90 = dmed * 1.7
        duration_source = "baseline"
    return {
        "task_type": task_type or "",
        "days": span,
        "samples": n,
        "ok_samples": len(ok_runs),
        "avg_tokens": int(sum(toks) / n),
        "median_tokens": int(med),
        "p90_tokens": toks[max(0, min(n - 1, math.ceil(0.9 * n) - 1))],
        "avg_cost_usd": round(sum(costs) / n, 4),
        "median_cost_usd": round(med_c, 4),
        "estimated_duration_s": max(30, int(dmed * factor)),
        "p90_duration_s": max(60, int(dp90 * factor)),
        "duration_samples": len(durations),
        "duration_source": duration_source,
        "since": (datetime.date.today() - datetime.timedelta(days=span - 1)).isoformat(),
    }
