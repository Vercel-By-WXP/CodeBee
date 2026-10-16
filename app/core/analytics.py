"""Task cost/quality rollups for operator views."""
from __future__ import annotations

from collections import defaultdict
import datetime


def summary(store, since=None):
    """Return task rollups plus dashboard metrics.

    ``since`` is an optional UNIX timestamp used by the UI for today/week
    filters.  Missing legacy timestamps are retained rather than discarded.
    """
    by_task = defaultdict(lambda: {"runs": 0, "done": 0, "failed": 0,
                                   "tokens": 0, "cost_usd": 0.0, "quality_sum": 0.0,
                                   "quality_count": 0})
    cli = defaultdict(lambda: {"calls": 0, "done": 0, "failed": 0})
    total_duration = 0.0
    duration_count = 0
    degraded = 0
    runs_seen = 0
    for run in store.list_runs(limit=None):
        if since is not None:
            stamp = run.get("created_at") or run.get("started_at")
            try:
                if isinstance(stamp, str):
                    parsed = datetime.datetime.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S").timestamp()
                else:
                    parsed = float(stamp)
                if parsed < float(since):
                    continue
            except (TypeError, ValueError, OverflowError, OSError):
                pass
        runs_seen += 1
        tid = run.get("task_id") or "_unowned"
        row = by_task[tid]
        row["runs"] += 1
        row["done"] += int(run.get("status") == "done")
        row["failed"] += int(run.get("status") == "failed")
        row["tokens"] += int(run.get("tokens") or 0)
        row["cost_usd"] += float(run.get("cost_usd") or 0)
        duration = run.get("duration_s")
        try:
            if duration is not None and float(duration) >= 0:
                total_duration += float(duration)
                duration_count += 1
        except (TypeError, ValueError):
            pass
        verdict = run.get("verdict") or {}
        if verdict.get("review_degraded") or verdict.get("quality_status") == "degraded":
            degraded += 1
        quality = verdict.get("overall")
        if isinstance(quality, (int, float)):
            row["quality_sum"] += float(quality)
            row["quality_count"] += 1
    rows = []
    for task_id, row in by_task.items():
        item = dict(row)
        item["task_id"] = task_id
        item["cost_usd"] = round(item["cost_usd"], 6)
        item["quality_avg"] = round(item.pop("quality_sum") / item.pop("quality_count"), 2) \
            if item["quality_count"] else None
        item.pop("quality_count", None)
        rows.append(item)
    rows.sort(key=lambda x: (-x["cost_usd"], x["task_id"]))
    for run in store.list_runs(limit=None):
        if since is not None:
            stamp = run.get("created_at") or run.get("started_at")
            try:
                if isinstance(stamp, str):
                    parsed = datetime.datetime.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S").timestamp()
                else:
                    parsed = float(stamp)
                if parsed < float(since):
                    continue
            except (TypeError, ValueError, OverflowError, OSError):
                pass
        # Steps carry the resolved CLI identity; one run can call several CLIs.
        for step in run.get("steps") or []:
            if not isinstance(step, dict):
                continue
            name = str(step.get("agent") or step.get("cli") or "unknown")
            cli[name]["calls"] += 1
            cli[name]["done"] += int(step.get("status") == "done")
            cli[name]["failed"] += int(step.get("status") in ("failed", "error"))
    cli_rows = []
    for name, item in sorted(cli.items()):
        cli_rows.append(dict(item, cli=name,
                             success_rate=round(item["done"] / item["calls"], 3) if item["calls"] else 0.0))
    return {"tasks": rows, "cli": cli_rows, "metrics": {
        "total_tasks": len(rows), "total_runs": runs_seen,
        "success_rate": round(sum(x["done"] for x in rows) / runs_seen, 3) if runs_seen else 0.0,
        "average_duration_s": round(total_duration / duration_count, 2) if duration_count else None,
        "review_degraded": degraded,
    }, "totals": {
        "runs": sum(x["runs"] for x in rows),
        "tokens": sum(x["tokens"] for x in rows),
        "cost_usd": round(sum(x["cost_usd"] for x in rows), 6),
    }}
