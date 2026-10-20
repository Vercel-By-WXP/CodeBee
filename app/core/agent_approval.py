# -*- coding: utf-8 -*-
"""Ephemeral approvals for model-proposed tool calls.

Task contracts and command authorization are durable user policy.  This store
is intentionally different: approvals live only in the current process, are
bound to one run and task, and expire automatically.
"""
from __future__ import annotations

import threading
import time


DEFAULT_TTL_SECONDS = 10 * 60
MAX_TASK_CHARS = 200
MAX_ITEMS = 200


def normalize_task(value):
    return str(value or "").strip()[:MAX_TASK_CHARS]


class RuntimeApprovalStore:
    def __init__(self, ttl_s=DEFAULT_TTL_SECONDS, now=None):
        try:
            ttl = float(ttl_s)
        except (TypeError, ValueError):
            ttl = DEFAULT_TTL_SECONDS
        self._ttl_s = max(1.0, ttl)
        self._now = now or time.time
        self._rows = {}
        self._lock = threading.RLock()

    @staticmethod
    def _key(task, run_id):
        task_key = normalize_task(task)
        run_key = str(run_id or "").strip()[:120]
        return task_key, run_key

    @staticmethod
    def _bounded(values):
        if values is None:
            return set()
        if isinstance(values, str):
            values = [values]
        result = set()
        for value in values:
            text = str(value or "").strip()[:120]
            if text:
                result.add(text)
            if len(result) >= MAX_ITEMS:
                break
        return result

    def approve(self, task, run_id="", tools=None, groups=None, now=None):
        key = self._key(task, run_id)
        if not key[0] or not key[1]:
            raise ValueError("运行态审批必须绑定 task 和 run_id")
        stamp = float(self._now() if now is None else now)
        with self._lock:
            previous = self._rows.get(key) or {}
            self._rows[key] = {
                "task": key[0],
                "run_id": key[1],
                "tools": set(previous.get("tools") or ()) | self._bounded(tools),
                "groups": set(previous.get("groups") or ()) | self._bounded(groups),
                "expires_at": stamp + self._ttl_s,
            }
        return self.describe(task, run_id, now=stamp)

    def _active(self, key, now):
        row = self._rows.get(key)
        if not row:
            return None
        if float(row.get("expires_at") or 0) <= float(now):
            self._rows.pop(key, None)
            return None
        return row

    def is_approved(self, task, run_id="", action="", group="", now=None):
        key = self._key(task, run_id)
        with self._lock:
            row = self._active(key, self._now() if now is None else now)
            if not row:
                return False
            return (str(action or "").strip() in row["tools"] or
                    str(group or "").strip() in row["groups"])

    def describe(self, task, run_id="", now=None):
        key = self._key(task, run_id)
        with self._lock:
            row = self._active(key, self._now() if now is None else now)
            if not row:
                return None
            return {
                "task": row["task"],
                "run_id": row["run_id"],
                "tools": sorted(row["tools"]),
                "groups": sorted(row["groups"]),
                "expires_at": row["expires_at"],
            }

    def clear_expired(self, now=None):
        stamp = self._now() if now is None else now
        with self._lock:
            before = len(self._rows)
            for key in list(self._rows):
                self._active(key, stamp)
            return before - len(self._rows)

    def clear(self):
        with self._lock:
            self._rows.clear()

    def size(self):
        with self._lock:
            self.clear_expired()
            return len(self._rows)

    def snapshot(self):
        with self._lock:
            self.clear_expired()
            return [self.describe(task, run_id) for task, run_id in self._rows]


_DEFAULT = RuntimeApprovalStore()


def default_store():
    return _DEFAULT


def approve(task, run_id="", tools=None, groups=None, now=None):
    return _DEFAULT.approve(task, run_id, tools=tools, groups=groups, now=now)


def is_approved(task, run_id="", action="", group="", now=None):
    return _DEFAULT.is_approved(task, run_id, action=action, group=group, now=now)


def view(task="", run_id=""):
    if task and run_id:
        return _DEFAULT.describe(task, run_id)
    return _DEFAULT.snapshot()
