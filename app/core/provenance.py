# -*- coding: utf-8 -*-
"""Portable, redacted execution provenance records."""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

from . import atomic


def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _files(paths):
    out = []
    for raw in paths or ():
        p = Path(raw)
        item = {"path": str(p), "exists": p.is_file()}
        if p.is_file():
            try:
                item.update({"size": p.stat().st_size, "sha256": file_digest(p)})
            except OSError:
                item["exists"] = False
        out.append(item)
    return out


def build(*, run_id, workdir, command=(), inputs=(), outputs=(), verification=(), source_revision=""):
    wd = Path(workdir).expanduser().resolve()
    revision = str(source_revision or "")
    if not revision:
        try:
            revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wd,
                                      capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            revision = ""
    return {"schema": 1, "run_id": str(run_id), "workdir": str(wd),
            "source_revision": revision, "command": [str(x) for x in command or ()],
            "inputs": _files(inputs), "outputs": _files(outputs),
            "verification": [str(x)[:500] for x in verification or ()],
            "created_at": time.time()}


def dump(record, path):
    atomic.write_json(Path(path), record)


def from_run(run, task=None, *, workdir=None):
    """Build provenance from persisted run/task rows and their artifact refs."""
    run = run if isinstance(run, dict) else {}
    task = task if isinstance(task, dict) else {}
    root = Path(workdir or task.get("workdir") or ".").expanduser().resolve()
    command = []
    verification = []
    for step in run.get("steps") or ():
        if not isinstance(step, dict):
            continue
        if step.get("verify_command"):
            value = str(step["verify_command"])
            if value not in verification:
                verification.append(value)
        if step.get("command"):
            value = step.get("command") if isinstance(step.get("command"), list) else [step.get("command")]
            if not command:
                command = value
    outputs = []
    for item in run.get("assets") or run.get("artifact_refs") or ():
        if isinstance(item, dict) and item.get("path"):
            outputs.append(root / str(item["path"]))
    return build(run_id=run.get("id") or "", workdir=root, command=command,
                 inputs=[], outputs=outputs, verification=verification,
                 source_revision=(run.get("source_revision") or ""))
