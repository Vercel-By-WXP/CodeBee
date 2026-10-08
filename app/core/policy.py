# -*- coding: utf-8 -*-
"""Descriptive sandbox policy primitives shared by API and runners."""
from __future__ import annotations

import os
from pathlib import Path


def normalize_sandbox(spec=None, workdir=None):
    spec = spec if isinstance(spec, dict) else {}
    root = Path(workdir or os.getcwd()).expanduser().resolve()
    roots = []
    for value in spec.get("allowed_roots") or [str(root)]:
        try:
            candidate = Path(value).expanduser().resolve()
        except (TypeError, OSError, ValueError):
            continue
        if candidate not in roots:
            roots.append(candidate)
    if not roots:
        roots = [root]
    env = []
    for value in spec.get("env_allowlist") or []:
        name = str(value or "").strip()
        if name and name.replace("_", "a").isalnum() and name not in env:
            env.append(name)
    try:
        timeout = max(1, min(7 * 24 * 3600, int(spec.get("timeout_s") or 3600)))
    except (TypeError, ValueError):
        timeout = 3600
    try:
        output = max(1024, min(100 * 1024 * 1024, int(spec.get("max_output_bytes") or 10 * 1024 * 1024)))
    except (TypeError, ValueError):
        output = 10 * 1024 * 1024
    return {"allowed_roots": [str(x) for x in roots], "env_allowlist": env,
            "network": bool(spec.get("network", True)), "timeout_s": timeout,
            "max_output_bytes": output}


def path_allowed(path, sandbox):
    try:
        candidate = Path(path).expanduser().resolve()
        return any(candidate == Path(root) or Path(root) in candidate.parents
                   for root in (sandbox or {}).get("allowed_roots") or [])
    except (TypeError, OSError, ValueError):
        return False


def filter_env(env, sandbox):
    values = env if isinstance(env, dict) else {}
    allow = (sandbox or {}).get("env_allowlist") or []
    return {str(k): str(v) for k, v in values.items() if str(k) in allow}
