# -*- coding: utf-8 -*-
"""Keyless session/workspace snapshots for deterministic replay tests."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


def _digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def capture(workspace, session):
    root = Path(workspace).resolve()
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".git" not in path.parts and ".codebee" not in path.parts:
            files[str(path.relative_to(root)).replace("\\", "/")] = _digest(path)
    return {"schema": 1, "workspace": str(root), "files": files,
            "session": copy.deepcopy(session)}


def compare_workspace(snapshot, workspace):
    current = capture(workspace, {}).get("files", {})
    before = snapshot.get("files") or {}
    return {"added": sorted(set(current) - set(before)),
            "removed": sorted(set(before) - set(current)),
            "modified": sorted(k for k in set(before) & set(current) if before[k] != current[k])}


def restore(snapshot):
    return copy.deepcopy(snapshot.get("session") or {})


def replay(snapshot, *, upto_seq=None):
    """Replay the keyless session projection deterministically.

    The snapshot never contains credentials or executable instructions.  It
    contains the append-only event projection captured at run finalization;
    replay therefore reconstructs the exact model-visible messages without
    re-running tools or touching the workspace.
    """
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    events = []
    for raw in snapshot.get("events") or ():
        if not isinstance(raw, dict):
            continue
        try:
            seq = int(raw.get("seq") or 0)
        except (TypeError, ValueError):
            continue
        if upto_seq is not None and seq > int(upto_seq):
            continue
        events.append(copy.deepcopy(raw))
    events.sort(key=lambda x: int(x.get("seq") or 0))
    messages = copy.deepcopy((snapshot.get("session") or {}).get("messages") or [])
    if events:
        surface = []
        for event in events:
            op = event.get("surface_op")
            if op == "shadow":
                continue
            if isinstance(op, dict) and op.get("op") == "replace":
                start = int(op.get("start_seq") or op.get("start") or 0)
                end = int(op.get("end_seq") or op.get("end") or 0)
                surface = [x for x in surface
                           if not (start <= int(x.get("seq") or 0) <= end)]
            if event.get("type") in ("system_message", "user_message", "assistant_message"):
                data = event.get("data") if isinstance(event.get("data"), dict) else {}
                role = ("system" if event.get("type") == "system_message" else
                        "assistant" if event.get("type") == "assistant_message" else "user")
                surface.append({"role": role, "content": str(data.get("content") or ""),
                                "seq": int(event.get("seq") or 0)})
        if surface:
            messages = surface
    return {"schema": int(snapshot.get("schema") or 1),
            "workspace": str(snapshot.get("workspace") or ""),
            "messages": messages,
            "events": events,
            "event_count": len(events),
            "replace_generation": int(snapshot.get("replace_generation") or 0)}


def write(snapshot, path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def from_session(session, workspace):
    messages = session.derive_messages() if hasattr(session, "derive_messages") else {}
    events = []
    if hasattr(session, "events"):
        for event in session.events():
            try:
                events.append(event.to_dict())
            except AttributeError:
                continue
    return dict(capture(workspace, {"messages": messages}),
                events=events,
                replace_generation=(session.replace_generation()
                                     if hasattr(session, "replace_generation") else 0))
