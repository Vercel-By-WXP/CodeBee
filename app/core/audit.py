# -*- coding: utf-8 -*-
"""轻量审计日志：不可逆操作留痕（当前仅任务删除）。

删除任务连运行记录一起物理清除且无回收站，出争议时无据可查
（2026-09-28 连载根任务被删案：谁删的、何时删的全无线索）。
这里按月追加 jsonl 到 data/audit/，一行一对象、正序追加，
与用量台账同款纪律。审计失败绝不拖垮主流程。
"""
from __future__ import annotations

import json
import hashlib
import threading
import time
from pathlib import Path

from . import paths

_LOCK = threading.RLock()


def record_decision(action, decision, zone, reason, *, status="returned",
                    run_id="", args=None, result=None):
    """Record one model-tool adjudication without persisting raw arguments.

    The action result is represented only by a digest and size; callers should
    use the normal run log for human-readable output.  This event is the
    compact, hash-chained provenance record used to answer whether a tool was
    allowed.
    """
    raw = json.dumps(args if isinstance(args, dict) else {}, ensure_ascii=False,
                     sort_keys=True, separators=(",", ":"), default=str)
    # Tool output can contain file contents, command output, tokens, or other
    # user data.  Do not copy even a short prefix into the durable audit log;
    # retain only a digest and size so the receipt can still be correlated
    # without turning the audit trail into a secret/PII sink.
    result_text = str(result or "")
    record("tool_decision", {
        "run_id": str(run_id or "")[:120],
        "action": str(action or "")[:120],
        "decision": str(decision or "")[:32],
        "zone": str(zone or "")[:16],
        "reason": str(reason or "")[:240],
        "status": str(status or "returned")[:32],
        "args_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "result_sha256": hashlib.sha256(result_text.encode("utf-8")).hexdigest(),
        "result_chars": len(result_text),
    })


def _canonical(row: dict) -> str:
    """Return the stable bytes covered by an audit hash.

    The hash intentionally excludes ``hash`` itself so an entry can be
    verified without reproducing dictionary insertion order.
    """
    payload = {k: v for k, v in row.items() if k not in {"hash"}}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def _hash(row: dict) -> str:
    return hashlib.sha256(_canonical(row).encode("utf-8")).hexdigest()


def _tail(path: Path) -> tuple[int, str]:
    """Read the last valid sequence/hash, tolerating legacy unchained rows."""
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, UnicodeDecodeError, ValueError):
        return 0, "genesis"
    valid = [row for row in rows if isinstance(row, dict)]
    if not valid:
        return 0, "genesis"
    last = valid[-1]
    try:
        seq = int(last.get("seq") or len(valid))
    except (TypeError, ValueError):
        seq = len(valid)
    return seq, str(last.get("hash") or "genesis")


def record(event: str, detail: dict) -> None:
    """追加一条带前序哈希的审计记录（event 即台账名，如 task_delete）。"""
    try:
        day = time.strftime("%Y-%m")
        d = Path(paths.DATA_DIR) / "audit"
        d.mkdir(parents=True, exist_ok=True)
        path = d / ("%s-%s.jsonl" % (event, day))
        with _LOCK:
            seq, prev_hash = _tail(path)
            row = dict(detail) if isinstance(detail, dict) else {}
            # Reserved chain fields are always written by the logger, never by
            # a caller supplied detail object.
            row.update({"seq": seq + 1, "prev_hash": prev_hash,
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "event": str(event or "")[:80]})
            row["hash"] = _hash(row)
            line = json.dumps(row, ensure_ascii=False, sort_keys=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


def verify(event: str, month: str | None = None) -> dict:
    """Verify the hash chain for one event log without mutating it."""
    month = month or time.strftime("%Y-%m")
    path = Path(paths.DATA_DIR) / "audit" / ("%s-%s.jsonl" % (event, month))
    with _LOCK:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {"ok": True, "event": event, "entries": 0}
    previous = "genesis"
    chain_started = False
    expected_seq = 0
    for index, line in enumerate(lines, 1):
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            return {"ok": False, "event": event, "entries": len(lines), "broken_at": index}
        if not isinstance(row, dict) or row.get("prev_hash", "genesis") != previous:
            return {"ok": False, "event": event, "entries": len(lines), "broken_at": index}
        is_chained = "seq" in row or "prev_hash" in row
        if is_chained:
            chain_started = True
            raw_sequence = row.get("seq")
            if raw_sequence is None:
                return {"ok": False, "event": event, "entries": len(lines), "broken_at": index}
            try:
                sequence = int(raw_sequence)
            except (TypeError, ValueError):
                return {"ok": False, "event": event, "entries": len(lines), "broken_at": index}
            if expected_seq and sequence != expected_seq + 1:
                return {"ok": False, "event": event, "entries": len(lines), "broken_at": index}
            expected_seq = sequence
        supplied = row.get("hash")
        if chain_started and not supplied:
            return {"ok": False, "event": event, "entries": len(lines), "broken_at": index}
        if supplied and supplied != _hash(row):
            return {"ok": False, "event": event, "entries": len(lines), "broken_at": index}
        previous = str(supplied or previous)
    return {"ok": True, "event": event, "entries": len(lines)}
