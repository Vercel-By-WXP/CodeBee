"""Task cost/quality rollups for operator views."""
from __future__ import annotations

from collections import defaultdict


def summary(store):
    by_task = defaultdict(lambda: {"runs": 0, "done": 0, "failed": 0,
                                   "tokens": 0, "cost_usd": 0.0, "quality_sum": 0.0,
                                   "quality_count": 0})
    for run in store.list_runs(limit=None):
        tid = run.get("task_id") or "_unowned"
        row = by_task[tid]
        row["runs"] += 1
        row["done"] += int(run.get("status") == "done")
        row["failed"] += int(run.get("status") == "failed")
        row["tokens"] += int(run.get("tokens") or 0)
        row["cost_usd"] += float(run.get("cost_usd") or 0)
        verdict = run.get("verdict") or {}
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
    return {"tasks": rows, "totals": {
        "runs": sum(x["runs"] for x in rows),
        "tokens": sum(x["tokens"] for x in rows),
        "cost_usd": round(sum(x["cost_usd"] for x in rows), 6),
    }}
