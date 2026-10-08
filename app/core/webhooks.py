# -*- coding: utf-8 -*-
"""Webhook signing and delivery-id replay protection."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

from . import paths

_FILE = paths.DATA_DIR / "webhook_deliveries.json"
MAX_SKEW_SECONDS = 300
MAX_DELIVERIES = 2000


def sign(secret, body, timestamp):
    payload = (str(timestamp) + ".").encode("utf-8") + (body or b"")
    return "sha256=" + hmac.new(str(secret or "").encode("utf-8"), payload,
                                hashlib.sha256).hexdigest()


def verify(secret, body, signature, timestamp, *, now=None, max_skew=MAX_SKEW_SECONDS):
    if not secret or not signature:
        return False
    try:
        ts = int(timestamp)
        if abs((time.time() if now is None else float(now)) - ts) > max(1, int(max_skew)):
            return False
    except (TypeError, ValueError):
        return False
    expected = sign(secret, body, ts)
    return hmac.compare_digest(expected, str(signature).strip())


def _load():
    try:
        value = json.loads(_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, TypeError, ValueError):
        return {}


def claim_delivery(delivery_id, *, now=None, ttl_seconds=86400):
    delivery_id = str(delivery_id or "").strip()[:160]
    if not delivery_id:
        return True, {"deduplicated": False, "delivery_id": ""}
    stamp = float(time.time() if now is None else now)
    data = _load()
    fresh = {}
    for key, value in data.items():
        try:
            if stamp - float(value) <= max(60, int(ttl_seconds)):
                fresh[key] = value
        except (TypeError, ValueError):
            continue
    if delivery_id in fresh:
        return False, {"deduplicated": True, "delivery_id": delivery_id}
    fresh[delivery_id] = stamp
    fresh = dict(sorted(fresh.items(), key=lambda item: item[1], reverse=True)[:MAX_DELIVERIES])
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(fresh, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_FILE)
    return True, {"deduplicated": False, "delivery_id": delivery_id}
