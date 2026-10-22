# -*- coding: utf-8 -*-
"""Append-only provider attempt ledger used by routing, billing and replay."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from .error_codes import attempt_verdict, classify_error_text, error_code_value

_SECRET = re.compile(r"(?i)(sk-[A-Za-z0-9_-]+|Bearer\s+[A-Za-z0-9._-]+|api[_ -]?key\s*[:=]\s*[^\s;]+)")

ATTEMPT_VERDICTS = ("passed", "failed", "blocked")
# 回放缓存状态词汇（tester-army/e2e）：写盘、保留、未验证、驱逐、无变化、未写入。
CACHE_STATUSES = ("saved", "kept", "unconfirmed", "evicted", "no-change", "not-written")


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

    def finish(self, run_id, role, provider, model, *, ok, error="", error_code="",
               usage=None, duration_s=0.0, retry_class="", ttft_ms=0.0,
               tokens_per_sec=0.0, cache_status="", **extra):
        usage = usage if isinstance(usage, dict) else {}
        code = error_code_value(error_code)[:64]
        if not code and error:
            code = error_code_value(classify_error_text(error))[:64]
        status = str(cache_status or "").strip().lower()
        if status not in CACHE_STATUSES:
            status = ""
        row = {"event": "finish", "ts": time.time(), "run_id": str(run_id),
               "role": str(role), "provider": str(provider), "model": str(model),
               "ok": bool(ok), "duration_s": float(duration_s or 0),
               "usage": {k: int(v or 0) for k, v in usage.items()
                         if k in ("input", "output", "cached", "reasoning", "total")},
               "retry_class": str(retry_class or ""), "error_code": code,
               "verdict": attempt_verdict(ok, code),
               "error_fingerprint": error_fingerprint(error) if error else "",
               "error": str(error or "")[:500], "ttft_ms": float(ttft_ms or 0),
               "tokens_per_sec": float(tokens_per_sec or 0), **extra}
        if status:
            row["cache_status"] = status
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
        verdicts = {name: 0 for name in ATTEMPT_VERDICTS}
        cache_statuses = {}
        total = cached = 0
        for row in rows:
            if row.get("retry_class"):
                retry[row["retry_class"]] = retry.get(row["retry_class"], 0) + 1
            verdict = row.get("verdict")
            if verdict not in ATTEMPT_VERDICTS:
                verdict = attempt_verdict(row.get("ok"), row.get("error_code") or "")
            verdicts[verdict] += 1
            status = row.get("cache_status")
            if status in CACHE_STATUSES:
                cache_statuses[status] = cache_statuses.get(status, 0) + 1
            usage = row.get("usage") or {}
            total += int(usage.get("total") or usage.get("input") or 0) + int(usage.get("output") or 0)
            cached += int(usage.get("cached") or 0)
        return {"run_id": str(run_id), "attempts": len(rows),
                "successful": verdicts["passed"], "failed": verdicts["failed"],
                "verdicts": verdicts, "cache_statuses": cache_statuses,
                "tokens": total, "cached": cached, "retry_classes": retry}


def default_ledger():
    try:
        from . import paths
        return AttemptLedger(paths.DATA_DIR / "attempts" / "attempts.jsonl")
    except Exception:
        return None
