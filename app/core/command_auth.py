# -*- coding: utf-8 -*-
"""Command execution policy with live approval requests and redacted audit."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from .redact import scrub_text

_LOCK = threading.RLock()
_FILE = None
_PENDING = {}
_WAIT_SECONDS = 300
_MAX_AUDIT = 500
_MODES = {"legacy", "restricted"}
_SCOPES = {"once", "run", "project", "global"}
_MATCHES = {"exact", "prefix", "any"}
_SHELL_META = re.compile(r"[;&|<>`$\r\n]")


def init(data_dir=None):
    global _FILE, _PENDING
    with _LOCK:
        if data_dir is None:
            from . import paths
            base = paths.DATA_DIR
        else:
            base = Path(data_dir)
        base.mkdir(parents=True, exist_ok=True)
        _FILE = base / "command-permissions.json"
        _PENDING = {}


def _file():
    global _FILE
    if _FILE is None:
        init()
    return _FILE


def _default_state():
    return {"mode": "legacy", "rules": [], "audit": []}


def _load():
    path = _file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _default_state()
    except (OSError, ValueError):
        return {"mode": "restricted", "rules": [], "audit": []}
    if not isinstance(data, dict) or data.get("mode") not in _MODES:
        return {"mode": "restricted", "rules": [], "audit": []}
    return {"mode": data["mode"],
            "rules": data.get("rules") if isinstance(data.get("rules"), list) else [],
            "audit": data.get("audit") if isinstance(data.get("audit"), list) else []}


def _persist(state):
    path = _file()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(str(tmp), 0o600)
    except OSError:
        pass
    os.replace(str(tmp), str(path))


def _hash(value):
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _path_key(workdir):
    if not workdir:
        return ""
    return _hash(os.path.normcase(os.path.realpath(os.path.expanduser(str(workdir)))))


def _scope_key(scope, run_id, workdir):
    if scope == "global":
        return "*"
    if scope == "run":
        return str(run_id or "")
    if scope == "project":
        return _path_key(workdir)
    return ""


def _prefix_tokens(value):
    return tuple(str(value or "").strip().split())


def _rule_matches(rule, command, run_id, workdir):
    scope = rule.get("scope")
    if scope not in ("run", "project", "global"):
        return False
    if _scope_key(scope, run_id, workdir) != rule.get("scope_key"):
        return False
    match = rule.get("match")
    if match == "any":
        return True
    if match == "exact":
        return _hash(command) == rule.get("command_hash")
    if match == "prefix":
        if _SHELL_META.search(command):
            return False
        tokens = _prefix_tokens(command)
        prefix_hashes = rule.get("prefix_hashes") or []
        return (bool(prefix_hashes) and len(tokens) >= len(prefix_hashes) and
                [_hash(x) for x in tokens[:len(prefix_hashes)]] == prefix_hashes)
    return False


def _audit(state, action, command, request_id="", scope="", match=""):
    entry = {"id": request_id or uuid.uuid4().hex[:16], "action": action,
             "command": scrub_text(command, limit=240), "command_sha256": _hash(command),
             "scope": scope or "", "match": match or "", "at": time.time()}
    state["audit"] = (state.get("audit") or [])[-(_MAX_AUDIT - 1):] + [entry]


def _bump_state():
    try:
        from . import store
        store.bump_state()
    except Exception:
        pass


def mode():
    with _LOCK:
        return _load()["mode"]


def set_mode(value):
    if value not in _MODES:
        raise ValueError("mode 必须是 legacy/restricted")
    with _LOCK:
        state = _load()
        changed = state["mode"] != value
        state["mode"] = value
        _persist(state)
        if changed:
            for request in _PENDING.values():
                request["decision"] = {"allowed": False, "reason": "policy_changed"}
                request["event"].set()
    if changed:
        _bump_state()
    return value


def _make_rule(command, scope, match, run_id, workdir, prefix=None):
    if scope not in ("run", "project", "global"):
        raise ValueError("持久规则 scope 必须是 run/project/global")
    if match not in _MATCHES:
        raise ValueError("match 必须是 exact/prefix/any")
    if scope == "run" and not run_id:
        raise ValueError("当前运行授权缺少 run_id")
    if scope == "project" and not workdir:
        raise ValueError("项目授权缺少工作目录")
    rule = {"id": uuid.uuid4().hex[:16], "scope": scope,
            "scope_key": _scope_key(scope, run_id, workdir), "match": match,
            "command_preview": scrub_text(command if match != "any" else "*", 180),
            "created_at": time.time()}
    if match == "exact":
        rule["command_hash"] = _hash(command)
    elif match == "prefix":
        raw_prefix = str(prefix or command or "").strip()
        if not raw_prefix or _SHELL_META.search(raw_prefix) or _SHELL_META.search(command):
            raise ValueError("前缀授权只支持不含 shell 控制符的单条命令")
        tokens = _prefix_tokens(raw_prefix)
        if not tokens or len(tokens) > len(_prefix_tokens(command)):
            raise ValueError("授权前缀必须是本次请求命令的前缀")
        if _prefix_tokens(command)[:len(tokens)] != tokens:
            raise ValueError("授权前缀必须与本次请求命令开头一致")
        rule["prefix_hashes"] = [_hash(token) for token in tokens]
        rule["command_preview"] = scrub_text(raw_prefix, 180)
    return rule


def authorize(command, *, run_id="", workdir="", timeout_s=None,
              deadline=None, cancel_event=None, source="run_command"):
    command = str(command or "").strip()
    if not command:
        return {"allowed": False, "reason": "empty_command"}
    with _LOCK:
        state = _load()
        if state["mode"] == "legacy":
            return {"allowed": True, "reason": "legacy"}
        matched = next((rule for rule in reversed(state["rules"])
                       if _rule_matches(rule, command, run_id, workdir)), None)
        if matched:
            _audit(state, "allowed_by_rule", command,
                   scope=matched.get("scope"), match=matched.get("match"))
            _persist(state)
            return {"allowed": True, "reason": "rule", "rule_id": matched.get("id")}
        wait_s = _WAIT_SECONDS if timeout_s is None else max(0.0, float(timeout_s))
        if deadline is not None:
            wait_s = min(wait_s, max(0.0, float(deadline) - time.monotonic()))
        if wait_s <= 0 or (cancel_event is not None and cancel_event.is_set()):
            reason = "cancelled" if cancel_event is not None and cancel_event.is_set() else "timeout"
            _audit(state, reason, command)
            _persist(state)
            return {"allowed": False, "reason": reason}
        request_id = uuid.uuid4().hex[:16]
        request = {"id": request_id, "command": command, "run_id": str(run_id or ""),
                   "workdir": str(workdir or ""), "source": str(source or "")[:40],
                   "created_at": time.time(), "event": threading.Event(), "decision": None}
        _PENDING[request_id] = request
        _audit(state, "requested", command, request_id=request_id)
        _persist(state)
    _bump_state()
    expires = time.monotonic() + wait_s
    decision = None
    while time.monotonic() < expires:
        if cancel_event is not None and cancel_event.is_set():
            decision = {"allowed": False, "reason": "cancelled"}
            break
        if request["event"].wait(min(0.2, max(0.0, expires - time.monotonic()))):
            decision = request.get("decision")
            break
    if decision is None:
        decision = {"allowed": False, "reason": "timeout"}
    with _LOCK:
        _PENDING.pop(request_id, None)
        state = _load()
        _audit(state, decision.get("reason") or "denied", command,
               request_id=request_id, scope=decision.get("scope", ""),
               match=decision.get("match", ""))
        _persist(state)
    _bump_state()
    return decision


def pending_requests():
    with _LOCK:
        rows = []
        for request in _PENDING.values():
            rows.append({"id": request["id"], "command": scrub_text(request["command"], 500),
                         "run_id": request["run_id"], "source": request["source"],
                         "created_at": request["created_at"]})
        return sorted(rows, key=lambda row: row["created_at"])


def decide(request_id, *, approve, scope="once", match="exact", prefix=None):
    if not isinstance(approve, bool):
        raise ValueError("approve 必須是布尔值")
    with _LOCK:
        request = _PENDING.get(str(request_id or ""))
        if request is None:
            raise KeyError("命令授权请求不存在或已结束")
        state = _load()
        if approve:
            if scope not in _SCOPES:
                raise ValueError("scope 必须是 once/run/project/global")
            if scope == "once":
                rule = None
            else:
                rule = _make_rule(request["command"], scope, match,
                                  request["run_id"], request["workdir"], prefix=prefix)
                state["rules"].append(rule)
            request["decision"] = {"allowed": True, "reason": "approved",
                                   "rule_id": rule.get("id") if rule else "",
                                   "scope": scope, "match": match}
            _audit(state, "approved", request["command"], request_id=request["id"],
                   scope=scope, match=match)
        else:
            request["decision"] = {"allowed": False, "reason": "denied"}
            _audit(state, "denied_by_user", request["command"],
                   request_id=request["id"])
        _persist(state)
        request["event"].set()
    _bump_state()
    return {"ok": True}


def revoke(rule_id):
    with _LOCK:
        state = _load()
        remaining = [rule for rule in state["rules"] if rule.get("id") != str(rule_id or "")]
        if len(remaining) == len(state["rules"]):
            raise KeyError("授权规则不存在")
        state["rules"] = remaining
        _audit(state, "revoked", "", request_id=str(rule_id or ""))
        _persist(state)
    _bump_state()
    return True


def view():
    with _LOCK:
        state = _load()
        return {"mode": state["mode"], "rules": [dict(rule) for rule in state["rules"]],
                "pending": pending_requests(),
                "audit": [dict(row) for row in state["audit"][-100:]]}
