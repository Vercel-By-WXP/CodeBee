# -*- coding: utf-8 -*-
"""Human notification for repeated exhaustion of a model candidate chain.

This module is intentionally separate from ``health``.  A provider can be
down while another candidate still completes the task; the alarm here means a
whole candidate set has failed repeatedly and a person should intervene.

The default Windows channel uses the native PowerShell toast API when running
on Windows.  SMTP is available through environment configuration for hosts
where mail is the preferred on-call channel.  Sending is best effort: a
notification failure is recorded but never changes the task result.
"""
from __future__ import annotations

import base64
from email.message import EmailMessage
import json
import logging
import os
from pathlib import Path
import re
import smtplib
import subprocess
import threading
import time
import uuid

log = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 3
DEFAULT_COOLDOWN_SECONDS = 3600
MAX_SEEN_RUNS = 64
_LOCK = threading.RLock()

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_ -]?key|access[_ -]?token|authorization|bearer|"
    r"password|secret|token)\b\s*[:=]\s*[^\s,;]+")
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_SECRET_NAME = re.compile(
    r"(?i)\b(?:[A-Za-z0-9_-]*(?:api[_-]?key|secret|password)"
    r"[A-Za-z0-9_-]*|[A-Za-z0-9_-]+[_-]token[A-Za-z0-9_-]*)\b")
_TOKEN_PREFIX = re.compile(
    r"\b(?:sk|pk|rk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{8,}\b")


def _state_path() -> Path:
    from . import paths
    return Path(paths.DATA_DIR) / "failure_notifications.json"


def _threshold() -> int:
    try:
        return max(1, min(20, int(os.environ.get(
            "TUTTI_FAILURE_NOTIFY_THRESHOLD", DEFAULT_THRESHOLD))))
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD


def _cooldown() -> float:
    try:
        return max(0.0, float(os.environ.get(
            "TUTTI_FAILURE_NOTIFY_COOLDOWN_S", DEFAULT_COOLDOWN_SECONDS)))
    except (TypeError, ValueError):
        return float(DEFAULT_COOLDOWN_SECONDS)


def _read() -> dict:
    try:
        value = json.loads(_state_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(value: dict) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.warning("[failure-notify] 无法保存通知状态：%s", exc)


def _scope(role: str = "") -> str:
    role = " ".join(str(role or "").split())[:80]
    return "all-candidates:%s" % (role or "default")


def _safe_text(value, limit: int = 120) -> str:
    # Metadata is normally safe, but provider/model labels can originate from
    # local configuration.  Redact common credential forms before placing them
    # in a toast, e-mail, or durable state; control characters are removed too.
    text = " ".join(str(value or "").replace("\x00", "").split())
    text = _SECRET_ASSIGNMENT.sub("[REDACTED]", text)
    text = _BEARER_VALUE.sub("Bearer [REDACTED]", text)
    text = _SECRET_NAME.sub("[REDACTED]", text)
    text = _TOKEN_PREFIX.sub("[REDACTED]", text)
    return text[:limit]


def _event_from_attempts(*, run_id: str, task_id: str, role: str,
                         attempts, error_code: str = "", provider: str = "",
                         model: str = "") -> dict:
    attempts = [a for a in (attempts or ()) if isinstance(a, dict)]
    first = next((a for a in reversed(attempts) if a), {})
    return {
        "event_id": uuid.uuid4().hex,
        "kind": "all_candidates_failed",
        "scope": _scope(role),
        "run_id": _safe_text(run_id, 100),
        "task_id": _safe_text(task_id, 100),
        "role": _safe_text(role, 80),
        "consecutive_failures": 0,
        "candidate_count": len(attempts),
        "error_code": _safe_text(error_code, 48),
        "provider": _safe_text(provider or first.get("provider_id") or
                                first.get("provider"), 100),
        "model": _safe_text(model or first.get("model"), 120),
        "at": time.time(),
    }


def _toast_script(title: str, message: str) -> str:
    # Base64 keeps user-controlled metadata out of PowerShell source code.
    def enc(value: str) -> str:
        return base64.b64encode(value.encode("utf-8", "replace")).decode("ascii")
    t64, m64 = enc(title), enc(message)
    return ("$ErrorActionPreference='Stop';"
            "Add-Type -AssemblyName System.Runtime.WindowsRuntime;"
            "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]>$null;"
            "$e=[Text.Encoding]::UTF8;"
            "$t=$e.GetString([Convert]::FromBase64String('%s'));"
            "$m=$e.GetString([Convert]::FromBase64String('%s'));"
            "$x=New-Object Windows.Data.Xml.Dom.XmlDocument;"
            "$et=[System.Security.SecurityElement]::Escape($t);"
            "$em=[System.Security.SecurityElement]::Escape($m);"
            "$x.LoadXml(\"<toast><visual><binding template='ToastGeneric'>"
            "<text>$et</text><text>$em</text></binding></visual></toast>\");"
            "$n=[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('CodeBee');"
            "$n.Show((New-Object Windows.UI.Notifications.ToastNotification $x));" %
            (t64, m64))


def _send_windows_toast(event: dict) -> tuple[bool, str]:
    if os.name != "nt":
        return False, "windows_unavailable"
    if os.environ.get("TUTTI_NOTIFY_WINDOWS_TOAST", "1").strip().lower() in (
            "0", "false", "off", "no"):
        return False, "windows_disabled"
    title = "CodeBee：候选链连续失败"
    message = ("连续 %d 轮全候选失败（%s），请检查余额、异上游和配置。"
               % (event.get("consecutive_failures") or 0,
                  event.get("error_code") or "未分类"))
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             _toast_script(title, message)],
            check=False, capture_output=True, text=True, timeout=15)
        if result.returncode:
            return False, "powershell_exit_%s" % result.returncode
        return True, "windows_toast"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "windows_error:%s" % type(exc).__name__


def _send_email(event: dict) -> tuple[bool, str]:
    try:
        host = os.environ.get("TUTTI_NOTIFY_SMTP_HOST", "").strip()
        recipient = os.environ.get("TUTTI_NOTIFY_EMAIL_TO", "").strip()
        if not host or not recipient:
            return False, "email_unconfigured"
        sender = os.environ.get("TUTTI_NOTIFY_EMAIL_FROM", "codebee@localhost").strip()
        port = int(os.environ.get("TUTTI_NOTIFY_SMTP_PORT", "587"))
    except ValueError:
        port = 587
    try:
        msg = EmailMessage()
        msg["Subject"] = "CodeBee 候选链连续失败告警"
        msg["From"] = sender
        msg["To"] = recipient
        msg.set_content(
            "连续 %d 轮全候选失败。\nrun=%s task=%s role=%s code=%s provider=%s model=%s\n"
            % (event.get("consecutive_failures") or 0, event.get("run_id") or "",
               event.get("task_id") or "", event.get("role") or "",
               event.get("error_code") or "", event.get("provider") or "",
               event.get("model") or ""))
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            if os.environ.get("TUTTI_NOTIFY_SMTP_TLS", "1").lower() not in (
                    "0", "false", "off", "no"):
                smtp.starttls()
            user = os.environ.get("TUTTI_NOTIFY_SMTP_USER", "")
            password = os.environ.get("TUTTI_NOTIFY_SMTP_PASSWORD", "")
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
        return True, "email"
    except (OSError, smtplib.SMTPException, ValueError, TypeError) as exc:
        return False, "email_error:%s" % type(exc).__name__


def send(event: dict) -> dict:
    """Send configured channels and return per-channel outcomes."""
    outcomes = {}
    if os.name == "nt":
        outcomes["windows"] = _send_windows_toast(event)
    if os.environ.get("TUTTI_NOTIFY_SMTP_HOST") or os.environ.get("TUTTI_NOTIFY_EMAIL_TO"):
        outcomes["email"] = _send_email(event)
    if not outcomes:
        outcomes["none"] = (False, "no_channel_configured")
    return {name: {"sent": bool(result[0]), "detail": result[1]}
            for name, result in outcomes.items()}


def record_success(*, role: str = "") -> None:
    """Reset the consecutive-failure episode after a successful candidate call."""
    key = _scope(role)
    with _LOCK:
        state = _read()
        item = state.get(key) or {}
        if item.get("consecutive_failures") or item.get("episode_notified"):
            item.update({"consecutive_failures": 0, "episode_notified": False,
                         "seen_runs": []})
            state[key] = item
            _write(state)


def record_all_candidates_failed(*, run_id: str, task_id: str = "", role: str = "",
                                 attempts=(), error_code: str = "", provider: str = "",
                                 model: str = "") -> dict:
    """Record one exhausted chain and notify after the configured threshold.

    Duplicate observations for the same run are ignored.  The return value is
    useful to tests and diagnostics and contains no prompt or secret material.
    """
    attempts = [a for a in (attempts or ()) if isinstance(a, dict)]
    if not attempts or any(a.get("ok") for a in attempts):
        return {"recorded": False, "reason": "chain_not_exhausted"}
    key = _scope(role)
    now = time.time()
    event = _event_from_attempts(run_id=run_id, task_id=task_id, role=role,
                                 attempts=attempts, error_code=error_code,
                                 provider=provider, model=model)
    with _LOCK:
        state = _read()
        item = state.get(key) or {"consecutive_failures": 0, "seen_runs": []}
        seen = list(item.get("seen_runs") or [])
        if run_id and run_id in seen:
            return {"recorded": False, "reason": "duplicate_run",
                    "consecutive_failures": int(item.get("consecutive_failures") or 0)}
        item["consecutive_failures"] = int(item.get("consecutive_failures") or 0) + 1
        if run_id:
            seen.append(run_id)
        item["seen_runs"] = seen[-MAX_SEEN_RUNS:]
        event["consecutive_failures"] = item["consecutive_failures"]
        should_notify = (item["consecutive_failures"] >= _threshold()
                         and not item.get("episode_notified")
                         and now - float(item.get("last_notified_at") or 0) >= _cooldown())
        if should_notify:
            item["episode_notified"] = True
            item["last_notified_at"] = now
        state[key] = item
        _write(state)
    outcomes = send(event) if should_notify else {}
    if should_notify:
        with _LOCK:
            state = _read()
            item = state.get(key) or {}
            item["last_outcomes"] = outcomes
            # A configured channel can fail transiently.  Only close the
            # episode after at least one channel confirms delivery; otherwise
            # the cooldown permits a later exhausted run to retry the alert.
            item["episode_notified"] = any(
                bool(value.get("sent")) for value in outcomes.values()
                if isinstance(value, dict))
            state[key] = item
            _write(state)
    return {"recorded": True, "notified": should_notify,
            "consecutive_failures": event["consecutive_failures"],
            "outcomes": outcomes}


def reset_for_tests() -> None:
    with _LOCK:
        try:
            _state_path().unlink()
        except OSError:
            pass
