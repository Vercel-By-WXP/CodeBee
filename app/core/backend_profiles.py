# -*- coding: utf-8 -*-
"""User-managed ACP agent launch profiles with secret-redacted views."""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path

from .redact import scrub_text

_LOCK = threading.RLock()
_FILE = None
_SECRET_NAME = re.compile(r"(?i)(token|secret|password|passwd|api[_-]?key|credential)")
_PROFILE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def init(data_dir=None):
    global _FILE
    with _LOCK:
        if data_dir is None:
            from . import paths
            base = paths.DATA_DIR
        else:
            base = Path(data_dir)
        base.mkdir(parents=True, exist_ok=True)
        _FILE = base / "backend-profiles.json"


def _file():
    global _FILE
    if _FILE is None:
        init()
    return _FILE


def _load():
    path = _file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise ValueError("ACP 后端配置不可读取，已阻止覆盖") from exc
    profiles = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(profiles, list):
        raise ValueError("ACP 后端配置格式无效")
    return [row for row in profiles if isinstance(row, dict)][:50]


def _persist(profiles):
    path = _file()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"profiles": profiles}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    try:
        os.chmod(str(tmp), 0o600)
    except OSError:
        pass
    os.replace(str(tmp), str(path))


def _valid_workspace_path(value):
    value = str(value or "").strip()
    if not value:
        return ""
    if "\x00" in value or len(value) > 1024:
        raise ValueError("workspace_path 无效")
    if not (value.startswith("/") or value.startswith("\\") or
            re.match(r"^[A-Za-z]:[\\/]", value)):
        raise ValueError("workspace_path 必须是绝对路径")
    return value


def _normalize(payload, existing=None):
    if not isinstance(payload, dict):
        raise ValueError("profile 必须是 JSON 对象")
    old = existing or {}
    profile_id = str(payload.get("id") or old.get("id") or
                     ("backend-" + uuid.uuid4().hex[:16])).strip()
    if not _PROFILE_ID.fullmatch(profile_id):
        raise ValueError("profile id 格式无效")
    label = str(payload.get("label") or "").strip()
    command = str(payload.get("command") or "").strip()
    if not label or len(label) > 80:
        raise ValueError("label 必填且不能超过 80 个字符")
    if not command or len(command) > 500 or "\x00" in command:
        raise ValueError("command 必填且格式无效")
    raw_args = payload.get("args", old.get("args", []))
    if not isinstance(raw_args, list) or len(raw_args) > 80:
        raise ValueError("args 必须是最多 80 项的列表")
    args = []
    for value in raw_args:
        if not isinstance(value, str) or len(value) > 2048 or "\x00" in value:
            raise ValueError("args 只能包含不超过 2048 字符的文本")
        args.append(value)
    raw_env = payload.get("env", old.get("env", {}))
    if not isinstance(raw_env, dict) or len(raw_env) > 100:
        raise ValueError("env 必须是最多 100 项的对象")
    env = {}
    old_env = old.get("env") or {}
    for name, value in raw_env.items():
        name = str(name)
        if not _ENV_NAME.fullmatch(name) or not isinstance(value, str) \
                or len(value) > 8192 or "\x00" in value:
            raise ValueError("env 变量名或值无效")
        if value == "__REDACTED__" and name in old_env:
            value = old_env[name]
        env[name] = value
    enabled = payload.get("enabled", old.get("enabled", True))
    if not isinstance(enabled, bool):
        raise ValueError("enabled 必须是布尔值")
    return {"id": profile_id, "agent_id": "acp-" + profile_id,
            "label": label, "command": command, "args": args,
            "env": env, "workspace_path": _valid_workspace_path(
                payload.get("workspace_path", old.get("workspace_path", ""))),
            "enabled": enabled}


def _view(profile):
    row = dict(profile)
    row["command"] = scrub_text(profile.get("command") or "", 500)
    row["args"] = [scrub_text(value, 2048) for value in profile.get("args") or []]
    env = {}
    for name, value in (profile.get("env") or {}).items():
        env[name] = "__REDACTED__" if _SECRET_NAME.search(name) and value else value
    row["env"] = env
    return row


def list_profiles():
    with _LOCK:
        return [_view(row) for row in _load()]


def runtime_profiles():
    with _LOCK:
        return [dict(row, env=dict(row.get("env") or {})) for row in _load()
                if row.get("enabled", True)]


def get_runtime(profile_id):
    with _LOCK:
        return next((dict(row, env=dict(row.get("env") or {}))
                     for row in _load() if row.get("id") == str(profile_id or "")
                     and row.get("enabled", True)), None)


def runtime_agents():
    return [{"id": row["agent_id"], "profile_id": row["id"],
             "label": row["label"], "kind": "acp", "mode": "real",
             "command": row["command"], "args": list(row["args"]),
             "env": {}, "workspace_path": row["workspace_path"],
             "profile_managed": True}
            for row in runtime_profiles()]


def save(payload):
    with _LOCK:
        profiles = _load()
        requested_id = str((payload or {}).get("id") or "")
        old = next((row for row in profiles if row.get("id") == requested_id), None)
        if requested_id and old is None:
            raise KeyError("ACP 后端配置不存在")
        profile = _normalize(payload, old)
        if any(row.get("id") == profile["id"] and row is not old for row in profiles):
            raise ValueError("profile id 已存在")
        if old is None and len(profiles) >= 50:
            raise ValueError("ACP 后端最多配置 50 个")
        if old is None:
            profiles.append(profile)
        else:
            profiles[profiles.index(old)] = profile
        _persist(profiles)
        return _view(profile)


def remove(profile_id):
    with _LOCK:
        profiles = _load()
        remaining = [row for row in profiles if row.get("id") != str(profile_id or "")]
        if len(remaining) == len(profiles):
            raise KeyError("ACP 后端配置不存在")
        _persist(remaining)
        return True
