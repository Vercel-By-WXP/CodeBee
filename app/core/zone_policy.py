# -*- coding: utf-8 -*-
"""Trusted dynamic zone resolvers for context-sensitive actions."""
from __future__ import annotations

import threading


_ZONES = {"green", "yellow", "red"}


class ZoneRegistry:
    def __init__(self):
        self._resolvers = {}
        self._lock = threading.RLock()

    def register(self, action, resolver):
        if not callable(resolver):
            raise TypeError("zone resolver must be callable")
        with self._lock:
            self._resolvers[str(action or "").strip()] = resolver

    def unregister(self, action):
        with self._lock:
            self._resolvers.pop(str(action or "").strip(), None)

    def resolve(self, action, base_zone, args=None, sandbox=None):
        with self._lock:
            resolver = self._resolvers.get(str(action or "").strip())
        if resolver is None:
            return base_zone if base_zone in _ZONES else "red"
        try:
            value = resolver({"action": action, "base_zone": base_zone,
                              "args": dict(args or {}), "sandbox": dict(sandbox or {})})
        except Exception:
            return "red"
        return value if value in _ZONES else "red"


_DEFAULT = ZoneRegistry()


def register(action, resolver):
    _DEFAULT.register(action, resolver)


def unregister(action):
    _DEFAULT.unregister(action)


def resolve(action, base_zone, args=None, sandbox=None):
    return _DEFAULT.resolve(action, base_zone, args=args, sandbox=sandbox)
