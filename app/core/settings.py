# -*- coding: utf-8 -*-
"""运行设置（data/settings.json）：目前只有并发 worker 数。"""
from __future__ import annotations

import json
import threading

from . import paths

_LOCK = threading.RLock()
_FILE = paths.DATA_DIR / "settings.json"

DEFAULTS = {"max_concurrent_jobs": 3}
MIN_WORKERS, MAX_WORKERS = 1, 6


def load():
    with _LOCK:
        try:
            data = json.loads(_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    out = dict(DEFAULTS)
    if isinstance(data, dict):
        out.update({k: v for k, v in data.items() if k in DEFAULTS})
    try:
        out["max_concurrent_jobs"] = max(MIN_WORKERS, min(MAX_WORKERS, int(out["max_concurrent_jobs"])))
    except Exception:
        out["max_concurrent_jobs"] = DEFAULTS["max_concurrent_jobs"]
    return out


def save(patch):
    """合并保存。返回 (view, 错误)。"""
    if not isinstance(patch, dict):
        return load(), "payload 必须是对象"
    with _LOCK:
        cur = load()
        if "max_concurrent_jobs" in patch:
            try:
                cur["max_concurrent_jobs"] = int(patch["max_concurrent_jobs"])
            except Exception:
                return cur, "max_concurrent_jobs 必须是整数"
            if not MIN_WORKERS <= cur["max_concurrent_jobs"] <= MAX_WORKERS:
                return cur, "max_concurrent_jobs 取值 %d-%d" % (MIN_WORKERS, MAX_WORKERS)
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_FILE)
    return cur, None
