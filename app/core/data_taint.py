# -*- coding: utf-8 -*-
"""Run-scoped credential taint for tool data flow.

Tool output is masked before it reaches the model (redact.mask_secrets), but
the model can still obtain a credential indirectly (a command that prints it
in an unrecognised shape, a task output, a file read before masking existed).
This module fingerprints every credential-shaped value seen in tool output
for one run and blocks sending that exact value to outbound tools.

The boundary is deliberately narrow: only literals copied into outbound tool
arguments are caught.  Commands that re-read a local file themselves, and
network effects already covered by command authorization/MCP sandboxing,
remain the responsibility of those layers.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets as _secrets
import threading
import time

from . import redact

DEFAULT_TTL_SECONDS = 6 * 3600
MAX_RUNS = 200
MAX_SECRETS_PER_RUN = 32
_OUTBOUND_TOOLS = {"run_command", "web_search"}
_OUTBOUND_PREFIXES = ("mcp__",)


class TaintStore:
    def __init__(self, ttl_s=DEFAULT_TTL_SECONDS, now=None):
        try:
            ttl = float(ttl_s)
        except (TypeError, ValueError):
            ttl = DEFAULT_TTL_SECONDS
        self._ttl_s = max(1.0, ttl)
        self._now = now or time.time
        self._rows = {}
        self._lock = threading.RLock()
        # Process-local HMAC key: persisted hashes would be offline guessable
        # for low-entropy credentials, and this state must not survive restarts.
        self._key = _secrets.token_bytes(32)

    def _fingerprint(self, value):
        return hmac.new(self._key, str(value).encode("utf-8", "replace"),
                        hashlib.sha256).hexdigest()

    def _purge(self, now):
        for run_id in [k for k, row in self._rows.items()
                       if float(row["updated"]) + self._ttl_s <= now]:
            self._rows.pop(run_id, None)
        while len(self._rows) > MAX_RUNS:
            oldest = min(self._rows, key=lambda k: self._rows[k]["updated"])
            self._rows.pop(oldest, None)

    @staticmethod
    def _run_key(run_id):
        return str(run_id or "").strip()[:120]

    def observe(self, run_id, text):
        """Remember credentials found in one tool output; return new count."""
        run_key = self._run_key(run_id)
        values = redact.find_secrets(text) if run_key else []
        if not values:
            return 0
        now = float(self._now())
        with self._lock:
            self._purge(now)
            row = self._rows.setdefault(run_key, {"secrets": set(), "updated": now})
            before = len(row["secrets"])
            for value in values:
                row["secrets"].add(self._fingerprint(value))
                if len(row["secrets"]) >= MAX_SECRETS_PER_RUN:
                    break
            row["updated"] = now
            return len(row["secrets"]) - before

    def is_tainted(self, run_id):
        run_key = self._run_key(run_id)
        if not run_key:
            return False
        now = float(self._now())
        with self._lock:
            self._purge(now)
            return bool((self._rows.get(run_key) or {}).get("secrets"))

    def check_outbound(self, tool_name, args, run_id):
        """Return a denial message when copied credentials reach an outbound tool.

        Empty string means the call may proceed.  Credentials the user supplied
        directly (never observed in tool output) are intentionally allowed.
        """
        name = str(tool_name or "").strip()
        if not (name in _OUTBOUND_TOOLS or name.startswith(_OUTBOUND_PREFIXES)):
            return ""
        run_key = self._run_key(run_id)
        if not run_key:
            return ""
        with self._lock:
            self._purge(float(self._now()))
            known = set((self._rows.get(run_key) or {}).get("secrets") or ())
        if not known:
            return ""
        try:
            blob = json.dumps(args if isinstance(args, dict) else {},
                              ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            blob = str(args or "")
        for value in redact.find_secrets(blob):
            if self._fingerprint(value) in known:
                return ("（数据流拦截：%s 的参数包含本次运行从工具输出读到的凭据，"
                        "已阻止外发；如需使用，请让用户在任务中显式提供，"
                        "或改为从环境变量引用。）" % name[:120])
        return ""

    def clear(self, run_id=""):
        run_key = self._run_key(run_id)
        with self._lock:
            if run_key:
                self._rows.pop(run_key, None)
            else:
                self._rows.clear()

    def size(self):
        with self._lock:
            self._purge(float(self._now()))
            return len(self._rows)


_DEFAULT = TaintStore()


def observe(run_id, text):
    return _DEFAULT.observe(run_id, text)


def is_tainted(run_id):
    return _DEFAULT.is_tainted(run_id)


def check_outbound(tool_name, args, run_id):
    return _DEFAULT.check_outbound(tool_name, args, run_id)


def clear(run_id=""):
    return _DEFAULT.clear(run_id)


def default_store():
    return _DEFAULT
