# -*- coding: utf-8 -*-
"""Project-space aggregation for the desktop and phone control surfaces.

The project view is deliberately a read model.  It joins task/workspace
identity with the existing task runs, governed project memory and automation
records without creating a second source of truth.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


def _key(workdir):
    raw = str(workdir or "").strip()
    if not raw:
        return ""
    try:
        normalized = str(Path(raw).expanduser().resolve()).replace("\\", "/").rstrip("/")
    except (OSError, RuntimeError, ValueError):
        normalized = raw.replace("\\", "/").rstrip("/")
    return hashlib.sha256(normalized.casefold().encode("utf-8")).hexdigest()[:20]


def _path_label(workdir):
    raw = str(workdir or "").strip()
    if not raw:
        return "默认工作区"
    return Path(raw).name or raw


def _task_summary(task):
    return {key: task.get(key) for key in (
        "id", "title", "type", "status", "workdir", "created_at", "updated_at",
        "archived", "expert_id", "expert_snapshot", "git_state") if key in task}


def snapshot(*, project_id="", include_archived=False):
    from . import automation, project_memory, store

    tasks = store.list_tasks(archived=bool(include_archived))
    groups = {}
    for task in tasks:
        workdir = str(task.get("workdir") or "").strip()
        key = _key(workdir)
        if not key:
            continue
        groups.setdefault(key, {"id": key, "workdir": workdir, "tasks": [],
                                "runs": [], "automations": [], "memory": []})["tasks"].append(task)
    if project_id:
        groups = {project_id: groups.get(project_id)} if project_id in groups else {}
    run_by_task = {}
    for run in store.list_runs(limit=100000):
        tid = str(run.get("task_id") or "")
        if tid:
            run_by_task.setdefault(tid, []).append(run)
    for group in groups.values():
        if not group:
            continue
        task_ids = {str(t.get("id")) for t in group["tasks"]}
        runs = []
        for task in group["tasks"]:
            runs.extend(run_by_task.get(str(task.get("id")), []))
        runs.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        group["tasks"] = [_task_summary(t) for t in group["tasks"]]
        group["runs"] = [{key: run.get(key) for key in (
            "id", "task_id", "title", "status", "created_at", "started_at", "ended_at",
            "summary", "error", "paused", "pause_phase", "verdict", "cost_usd", "tokens")
            if key in run} for run in runs[:100]]
        try:
            group["memory"] = project_memory.list_entries(group["workdir"])
        except (OSError, ValueError):
            group["memory"] = []
        try:
            group["automations"] = [a for a in automation.list_tasks()
                                    if str(a.get("workdir") or "") == group["workdir"]
                                    or str(a.get("task_id") or "") in task_ids]
        except Exception:
            group["automations"] = []
        latest = group["runs"][0] if group["runs"] else {}
        group["name"] = _path_label(group["workdir"])
        group["counts"] = {
            "tasks": len(group["tasks"]),
            "runs": len(group["runs"]),
            "running": sum(1 for r in group["runs"] if r.get("status") == "running"),
            "queued": sum(1 for r in group["runs"] if r.get("status") == "queued"),
            "attention": sum(1 for r in group["runs"] if r.get("paused") or
                               r.get("status") in ("failed", "timeout") or
                               (r.get("status") == "done" and
                                isinstance(r.get("verdict"), dict) and
                                r["verdict"].get("pass") is False)),
            "memory": len(group["memory"]),
            "automations": len(group["automations"]),
        }
        group["latest_run"] = latest
    return sorted((g for g in groups.values() if g),
                  key=lambda row: (str(row.get("name") or "").casefold(), row["id"]))


def get(project_id, **kwargs):
    rows = snapshot(project_id=project_id, **kwargs)
    return rows[0] if rows else None
