# -*- coding: utf-8 -*-
"""Execution policy primitives shared by tools and runners."""
from __future__ import annotations

import os
from pathlib import Path


def normalize_sandbox(spec=None, workdir=None):
    spec = spec if isinstance(spec, dict) else {}
    root = Path(workdir or os.getcwd()).expanduser().resolve()
    roots = []
    requested_roots = spec.get("allowed_roots")
    if requested_roots is not None and not isinstance(requested_roots, (list, tuple)):
        raise ValueError("allowed_roots 必须是路径列表")
    explicit_roots = bool(requested_roots)
    for value in requested_roots or [str(root)]:
        try:
            candidate = Path(value).expanduser().resolve()
        except (TypeError, OSError, ValueError):
            continue
        # The task payload is not an authority to grant host filesystem access.
        # Sandbox roots may narrow the selected workspace, never widen it.
        if candidate != root and root not in candidate.parents:
            continue
        if candidate not in roots:
            roots.append(candidate)
    if not roots:
        if explicit_roots:
            raise ValueError("allowed_roots 没有工作目录内的有效路径")
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
    network_value = spec.get("network", True)
    if isinstance(network_value, bool):
        network = network_value
    elif isinstance(network_value, str) and network_value.strip().lower() in ("true", "false"):
        network = network_value.strip().lower() == "true"
    else:
        # Invalid policy input must never silently widen network access.
        network = False
    backend = str(spec.get("backend") or "native").strip().lower()
    if backend not in ("native", "docker"):
        raise ValueError("沙箱 backend 必须是 native 或 docker")
    image = str(spec.get("docker_image") or "").strip()[:240]
    if backend == "docker":
        import re
        if not image or not re.match(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]*$", image):
            raise ValueError("Docker 镜像名无效")
    format_locks = []
    raw_locks = spec.get("format_locks")
    if isinstance(raw_locks, (list, tuple)):
        for raw_lock in raw_locks[:32]:
            if not isinstance(raw_lock, dict):
                continue
            extensions = [str(x).strip().lower()[:16]
                          for x in (raw_lock.get("extensions") or [])
                          if str(x).strip()][:32]
            channel = str(raw_lock.get("channel") or "").strip()[:80]
            if extensions and channel:
                format_locks.append({
                    "extensions": extensions,
                    "channel": channel,
                    "reason": str(raw_lock.get("reason") or "").strip()[:240],
                })
    internal_dir = str(spec.get("internal_dir") or ".codebee").strip()[:120]
    allowed_tools = None
    if "allowed_tools" in spec:
        raw_tools = spec.get("allowed_tools")
        if not isinstance(raw_tools, (list, tuple, set)):
            raise ValueError("allowed_tools 必须是工具名列表")
        allowed_tools = []
        for value in raw_tools:
            name = str(value or "").strip()[:120]
            if name and name not in allowed_tools:
                allowed_tools.append(name)
            if len(allowed_tools) >= 200:
                break
    approved_tools = []
    raw_approved = spec.get("approved_tools")
    if isinstance(raw_approved, (list, tuple, set)):
        for value in raw_approved:
            name = str(value or "").strip()[:120]
            if name and name not in approved_tools:
                approved_tools.append(name)
            if len(approved_tools) >= 200:
                break
    return {"allowed_roots": [str(x) for x in roots], "env_allowlist": env,
            "network": network, "timeout_s": timeout,
            "max_output_bytes": output,
            "backend": backend, "docker_image": image,
            "format_locks": format_locks, "internal_dir": internal_dir,
            "disabled_tools": normalize_disabled_tools(spec.get("disabled_tools")),
            "allowed_tools": allowed_tools,
            "approved_tools": approved_tools,
            "require_approval": bool(spec.get("require_approval", False))}


def normalize_disabled_tools(items):
    if not isinstance(items, (list, tuple, set)):
        return []
    out = []
    for value in items:
        name = str(value or "").strip()[:120]
        if name and name not in out:
            out.append(name)
        if len(out) >= 200:
            break
    return out


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
