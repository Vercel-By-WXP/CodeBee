# -*- coding: utf-8 -*-
"""Append-only provider attempt ledger used by routing, billing and replay."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

_SECRET = re.compile(r"(?i)(sk-[A-Za-z0-9_-]+|Bearer\s+[A-Za-z0-9._-]+|api[_ -]?key\s*[:=]\s*[^\s;]+)")


def error_fingerprint(value):
    text = _SECRET.sub("<redacted>", str(value or "")).lower()
    text = re.sub(r"\d+", "#", text)
    text = re.sub(r"\s+", " ", text).strip()[:500]
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class AttemptLedger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _append(self, row):
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def start(self, run_id, role, provider, model, **extra):
        row = {"event": "start", "ts": time.time(), "run_id": str(run_id),
               "role": str(role), "provider": str(provider), "model": str(model), **extra}
        self._append(row)
        return row

    def finish(self, run_id, role, provider, model, *, ok, error="", usage=None,
               duration_s=0.0, retry_class="", ttft_ms=0.0, tokens_per_sec=0.0, **extra):
        usage = usage if isinstance(usage, dict) else {}
        row = {"event": "finish", "ts": time.time(), "run_id": str(run_id),
               "role": str(role), "provider": str(provider), "model": str(model),
               "ok": bool(ok), "duration_s": float(duration_s or 0),
               "usage": {k: int(v or 0) for k, v in usage.items()
                         if k in ("input", "output", "cached", "reasoning", "total")},
               "retry_class": str(retry_class or ""),
               "error_fingerprint": error_fingerprint(error) if error else "",
               "error": str(error or "")[:500], "ttft_ms": float(ttft_ms or 0),
               "tokens_per_sec": float(tokens_per_sec or 0), **extra}
        self._append(row)
        return row

    def for_run(self, run_id):
        rows = []
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if row.get("event") == "finish" and row.get("run_id") == str(run_id):
                    rows.append(row)
        except (OSError, ValueError, TypeError):
            pass
        return rows

    def summary(self, run_id):
        rows = self.for_run(run_id)
        retry = {}
        total = cached = 0
        for row in rows:
            if row.get("retry_class"):
                retry[row["retry_class"]] = retry.get(row["retry_class"], 0) + 1
            usage = row.get("usage") or {}
            total += int(usage.get("total") or usage.get("input") or 0) + int(usage.get("output") or 0)
            cached += int(usage.get("cached") or 0)
        return {"run_id": str(run_id), "attempts": len(rows),
                "successful": sum(1 for x in rows if x.get("ok")),
                "failed": sum(1 for x in rows if not x.get("ok")),
                "tokens": total, "cached": cached, "retry_classes": retry}


def default_ledger():
    try:
        from . import paths
        return AttemptLedger(paths.DATA_DIR / "attempts" / "attempts.jsonl")
    except Exception:
        return None
