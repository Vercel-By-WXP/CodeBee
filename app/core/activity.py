"""Small, read-only projections for the sidebar activity view."""
from __future__ import annotations

from . import contracts


def run_activity(run):
    steps = [step for step in (run.get("steps") or []) if isinstance(step, dict)]
    current = next((step for step in reversed(steps)
                    if step.get("status") == "running"), None)
    if current is None and steps:
        current = steps[-1]
    failed = next((step for step in reversed(steps)
                   if step.get("status") in ("failed", "timeout")), None)
    result = {}
    for name, step in (("current_step", current), ("failed_step", failed)):
        if step:
            result[name] = {key: step[key] for key in
                            ("n", "role", "agent", "agent_label", "status") if key in step}
            result[name]["summary"] = " ".join(str(step.get("summary") or "").split())[:240]
    return result


def task_attention(tasks, latest):
    """Tasks needing the human eye: approval pending and unresolved agent
    blockers (reverse channel, 2026-10-22). Approval state is authoritative;
    never infer approval from a read marker."""
    from . import store_reports
    result = {}
    for task in tasks:
        run = latest.get(task["id"]) or {}
        attn = {}
        contract = contracts.get(task["id"]) or {}
        requires_approval = bool(task.get("approval_required") or
                                 contract.get("approval_required"))
        if (requires_approval and run.get("status") == "done"
                and (contract.get("approval") or {}).get("status") != "approved"):
            attn["approval_pending"] = True
        blockers = store_reports.open_blockers(run)
        if blockers:
            last = blockers[-1]
            attn["blocker"] = {"count": len(blockers),
                               "text": str(last.get("text") or "")[:120],
                               "at": last.get("at") or "",
                               "run_id": run.get("id") or ""}
        if attn:
            result[task["id"]] = attn
    return result
