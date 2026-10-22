"""Small, standard-library routing strategy registry.

The default strategy delegates to the existing ``router._pick_scored`` and is
therefore behavior-compatible. Integrations can register an in-process
strategy by name at startup; task data is never used as an import path.  This
keeps extension explicit and avoids executing arbitrary user supplied modules.
"""
from __future__ import annotations

import os
import re
import threading
from typing import Any, Dict, cast


class RoutingStrategy:
    """Runtime contract for a selectable routing policy."""

    def select_cli(self, task, available_clis):
        raise NotImplementedError

    def should_fallback(self, error):
        raise NotImplementedError

    def get_fallback_chain(self, cli):
        raise NotImplementedError


class BuiltinStrategy(RoutingStrategy):
    """Adapter around the production router and shared error taxonomy."""

    def select_cli(self, task, available_clis):
        from . import router
        task = dict(task) if isinstance(task, dict) else {}
        # Keep the established picker, including its exact score inputs and
        # tie-breaking, as the default runtime policy.
        return router._pick_scored(
            list(available_clis or []),
            str(task.get("role") or "implement"),
            task.get("task_type") or task.get("type") or "direct",
            task.get("stats"),
            exclude=task.get("exclude") or (),
        )

    def should_fallback(self, error):
        from .error_codes import ErrorCode, classify_error_text, error_code_value
        fallback_codes = {
            ErrorCode.NETWORK.value, ErrorCode.UPSTREAM_SERVER.value,
            ErrorCode.TIMEOUT.value, ErrorCode.QUOTA.value,
            ErrorCode.RATE_LIMIT.value, ErrorCode.LOCAL_STATE.value,
            ErrorCode.FORBIDDEN.value, ErrorCode.MODEL_UNAVAILABLE.value,
            ErrorCode.VENDOR_REFUSAL.value,
            # 凭据类账号死错同样要换 CLI（2026-10-22 cline-cli 案：账号
            # Unauthorized 判 AUTH 不在白名单 → 评审步原地重试同一死号 3 次
            # ×2 轮全撞 401。绑定链内的 Key 走查在 runner 已先耗尽，走到
            # 这里的 AUTH 是整链凭据死，换 CLI 是唯一活路）。
            ErrorCode.AUTH.value, ErrorCode.MISSING_CREDENTIAL.value,
        }
        if isinstance(error, dict):
            raw = error.get("raw") or {}
            if (error.get("cancelled") or
                    (isinstance(raw, dict) and raw.get("cancelled"))):
                return False
            code = error_code_value(error.get("error_code"))
            if code:
                return code in fallback_codes
            error = error.get("error") or error.get("message") or ""
        code = classify_error_text(error)
        return error_code_value(code) in fallback_codes

    def get_fallback_chain(self, cli):
        if not isinstance(cli, dict):
            return []
        values = cli.get("fallbacks")
        if values is None:
            values = cli.get("fallback_chain")
        if not isinstance(values, (list, tuple)):
            return []
        return [dict(item) if isinstance(item, dict) else item
                for item in values]


_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_LOCK = threading.RLock()
_FACTORIES: Dict[str, Any] = {"builtin": BuiltinStrategy}
_DEFAULT_NAME = "builtin"


def register(name, factory, *, replace=False):
    """Register a strategy factory explicitly at process startup.

    ``factory`` may be a strategy instance, a class, or a zero-argument
    callable.  Registration rejects malformed names and accidental overwrite
    unless ``replace=True`` is deliberate.
    """
    key = str(name or "").strip().lower()
    if not _NAME.fullmatch(key):
        raise ValueError("策略名称必须是 1-32 位小写标识符")
    if not isinstance(factory, type) and _valid(factory):
        pass
    elif not callable(factory):
        raise TypeError("策略必须实现 select_cli/should_fallback/get_fallback_chain")
    with _LOCK:
        if key in _FACTORIES and not replace:
            raise ValueError("策略已注册：%s" % key)
        _FACTORIES[key] = factory


def set_default(name):
    """Select a registered policy for future routing calls in this process."""
    key = str(name or "").strip().lower()
    if not _NAME.fullmatch(key):
        raise ValueError("策略名称必须是 1-32 位小写标识符")
    global _DEFAULT_NAME
    with _LOCK:
        if key not in _FACTORIES:
            raise ValueError("策略未注册：%s" % key)
        _DEFAULT_NAME = key
    return key


def _valid(value):
    return all(callable(getattr(value, method, None)) for method in (
        "select_cli", "should_fallback", "get_fallback_chain"))


def get(name=None) -> RoutingStrategy:
    """Resolve a policy by name, or the process default when name is omitted.

    CODEBEE_ROUTING_STRATEGY is an optional startup override. It can select a
    policy registered by an embedding application without importing modules
    named by user data.
    """
    configured = os.environ.get("CODEBEE_ROUTING_STRATEGY", "").strip()
    key = str(name or configured or _DEFAULT_NAME).strip().lower()
    with _LOCK:
        factory = _FACTORIES.get(key) or _FACTORIES["builtin"]
    if not isinstance(factory, type) and _valid(factory):
        return cast(RoutingStrategy, factory)
    try:
        strategy = factory() if callable(factory) else factory
    except Exception as exc:
        raise RuntimeError("routing strategy factory failed: %s" % key) from exc
    if not _valid(strategy):
        return BuiltinStrategy()
    return cast(RoutingStrategy, strategy)


def names():
    with _LOCK:
        return tuple(sorted(_FACTORIES))


__all__ = ["RoutingStrategy", "BuiltinStrategy", "register", "set_default",
           "get", "names"]
