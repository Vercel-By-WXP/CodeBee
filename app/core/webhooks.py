# -*- coding: utf-8 -*-
"""Webhook signing and delivery-id replay protection."""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
import time
from pathlib import Path

from . import paths

_FILE = paths.DATA_DIR / "webhook_deliveries.json"
MAX_SKEW_SECONDS = 300
MAX_DELIVERIES = 2000
_LOCK = threading.RLock()


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


def verify_raw(secret, body, signature):
    """Verify providers such as GitHub that sign the raw body only."""
    if not secret or not signature:
        return False
    expected = "sha256=" + hmac.new(str(secret).encode("utf-8"), body or b"",
                                    hashlib.sha256).hexdigest()
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
    db_path = _FILE.with_suffix(_FILE.suffix + ".sqlite3")
    with _LOCK:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(db_path), timeout=5) as db:
            db.execute("CREATE TABLE IF NOT EXISTS deliveries (delivery_id TEXT PRIMARY KEY, claimed_at REAL NOT NULL)")
            cutoff = stamp - max(60, int(ttl_seconds))
            db.execute("DELETE FROM deliveries WHERE claimed_at < ?", (cutoff,))
            try:
                db.execute("INSERT INTO deliveries(delivery_id, claimed_at) VALUES (?, ?)",
                           (delivery_id, stamp))
            except sqlite3.IntegrityError:
                return False, {"deduplicated": True, "delivery_id": delivery_id}
            db.execute("DELETE FROM deliveries WHERE delivery_id NOT IN (SELECT delivery_id FROM deliveries ORDER BY claimed_at DESC LIMIT ?)",
                       (MAX_DELIVERIES,))
    return True, {"deduplicated": False, "delivery_id": delivery_id}


def release_delivery(delivery_id):
    delivery_id = str(delivery_id or "").strip()[:160]
    if not delivery_id:
        return
    db_path = _FILE.with_suffix(_FILE.suffix + ".sqlite3")
    with _LOCK:
        try:
            with sqlite3.connect(str(db_path), timeout=5) as db:
                db.execute("DELETE FROM deliveries WHERE delivery_id = ?", (delivery_id,))
        except (OSError, sqlite3.Error):
            pass
