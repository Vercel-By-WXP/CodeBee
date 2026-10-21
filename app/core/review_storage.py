"""Dependency-free, redacted persistence for structured review results.

The orchestration pipeline still owns when a review is requested and how its
verdict affects a run.  This module provides a small durable store that those
callers can opt into without persisting prompts, raw CLI output, or secrets.
Each operation opens its own SQLite connection, so the API is safe for the
threaded HTTP server and background workers without sharing connections across
threads.
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import atomic, paths, review_findings
from .redact import scrub_text


_LOCK = threading.RLock()
_DB_PATH = None
_SCHEMA_VERSION = 2
_MAX_LIMIT = 500
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS review_records (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  run_id TEXT,
  author_cli TEXT NOT NULL,
  author_model_family TEXT,
  reviewer_cli TEXT NOT NULL,
  reviewer_model_family TEXT,
  scores_json TEXT NOT NULL,
  line_comments_json TEXT NOT NULL,
  findings_json TEXT NOT NULL DEFAULT '[]',
  coverage_json TEXT NOT NULL DEFAULT '{}',
  review_status TEXT NOT NULL DEFAULT 'ok',
  passed INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  duration_ms INTEGER,
  schema_version INTEGER NOT NULL DEFAULT 2
);
CREATE INDEX IF NOT EXISTS idx_review_task
  ON review_records(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_review_author_cli
  ON review_records(author_cli);
CREATE INDEX IF NOT EXISTS idx_review_reviewer_cli
  ON review_records(reviewer_cli);
CREATE TABLE IF NOT EXISTS review_findings (
  task_id TEXT NOT NULL,
  finding_id TEXT NOT NULL,
  status TEXT NOT NULL,
  verification TEXT NOT NULL DEFAULT 'unverified',
  verification_reason TEXT NOT NULL DEFAULT '',
  path TEXT NOT NULL DEFAULT '',
  line_start INTEGER,
  line_end INTEGER,
  severity TEXT NOT NULL DEFAULT 'minor',
  header TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  evidence TEXT NOT NULL DEFAULT '',
  suggestion TEXT NOT NULL DEFAULT '',
  first_seen_run TEXT,
  last_seen_run TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  resolved_run TEXT,
  resolved_at TEXT,
  PRIMARY KEY (task_id, finding_id)
);
CREATE INDEX IF NOT EXISTS idx_review_finding_status
  ON review_findings(task_id, status, last_seen_at);
"""


def init(data_dir=None):
    """Select the data directory and create the review schema.

    ``data_dir`` is injectable for tests and isolated installations.  Calling
    ``init`` is optional; without it the current dynamic ``paths.DATA_DIR`` is
    used on every operation.
    """
    global _DB_PATH
    with _LOCK:
        base = Path(data_dir) if data_dir is not None else Path(paths.DATA_DIR)
        base.mkdir(parents=True, exist_ok=True)
        _DB_PATH = base / "reviews.sqlite3"
        _ensure_schema(_DB_PATH)


def _path():
    with _LOCK:
        if _DB_PATH is not None:
            return Path(_DB_PATH)
    return Path(paths.DATA_DIR) / "reviews.sqlite3"


def _restrict_sqlite_files(database):
    """Keep the database and SQLite WAL sidecars private on POSIX systems.

    ``chmod`` follows symlinks by default, so inspect each path first and skip
    anything that is not a regular file. Windows ACLs are inherited from the
    data directory; mode bits there do not provide an equivalent restriction.
    """
    if os.name == "nt":
        return
    target = Path(database)
    for path in (target, Path(str(target) + "-wal"),
                 Path(str(target) + "-shm")):
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                continue
            os.chmod(str(path), 0o600)
        except OSError:
            continue


def _connect(path=None):
    target = Path(path or _path())
    target.parent.mkdir(parents=True, exist_ok=True)
    # sqlite3 creates a missing database with the process umask (often 022),
    # which briefly exposes review snippets and may also leave an overly broad
    # mode on disk. Publish an empty private inode first, then open it.
    if not target.exists():
        try:
            atomic.create_bytes(target, b"", mode=0o600)
        except FileExistsError:
            pass  # another worker won the first-create race
    try:
        os.chmod(str(target), 0o600)
    except OSError:
        pass
    conn = sqlite3.connect(str(target), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    # WAL keeps readers responsive while a background worker records a review.
    # SQLite remains the only runtime dependency and falls back to its default
    # journal mode on filesystems that do not support WAL.
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:
        pass
    # Keep init() optional as documented: a first read or write must be able
    # to create the schema in a fresh data directory as well.
    try:
        _create_schema(conn)
    except Exception:
        conn.close()
        raise
    _restrict_sqlite_files(target)
    return conn


def _create_schema(conn):
    conn.executescript(_SCHEMA_SQL)
    _migrate(conn)
    conn.commit()


def _migrate(conn):
    """旧库加列：CREATE IF NOT EXISTS 不会给既有表补字段。"""
    columns = {row["name"] for row in
               conn.execute("PRAGMA table_info(review_records)").fetchall()}
    additions = (
        ("findings_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("coverage_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("review_status", "TEXT NOT NULL DEFAULT 'ok'"),
    )
    for name, ddl in additions:
        if name not in columns:
            conn.execute("ALTER TABLE review_records ADD COLUMN %s %s" % (name, ddl))


def _ensure_schema(path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        try:
            atomic.create_bytes(target, b"", mode=0o600)
        except FileExistsError:
            pass
    try:
        os.chmod(str(target), 0o600)
    except OSError:
        pass
    conn = sqlite3.connect(str(target), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        _create_schema(conn)
        _restrict_sqlite_files(target)
    finally:
        conn.close()


def _text(value, limit=160):
    return scrub_text(str(value or "").strip(), limit=limit)


def _json(value, fallback):
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return json.dumps(fallback, ensure_ascii=False, separators=(",", ":"))


def _scores(value):
    """Keep only finite numeric 0..10 scores and bounded dimension names."""
    if not isinstance(value, dict):
        return {}
    out = {}
    for key, raw in list(value.items())[:64]:
        name = _text(key, 80)
        if not name:
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError, OverflowError):
            continue
        if number != number or number in (float("inf"), float("-inf")):
            continue
        if 0 <= number <= 10:
            out[name] = round(number, 4)
    return out


def _comments(value):
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value[:200]:
        if not isinstance(item, dict):
            continue
        raw_line = item.get("line")
        try:
            line = (int(raw_line) if isinstance(raw_line, (int, float, str))
                    else None)
        except (TypeError, ValueError, OverflowError):
            line = None
        out.append({
            "path": _text(item.get("path"), 240),
            "line": max(1, line) if line is not None else None,
            "severity": _text(item.get("severity"), 32),
            "message": _text(item.get("message"), 800),
        })
    return out


def _findings(value):
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value[:200]:
        finding = review_findings.normalize(item)
        if finding is None:
            continue
        out.append(finding)
    return out


def _coverage(value):
    if not isinstance(value, dict):
        return {}
    out = {}
    for key in ("repo", "checked", "complete", "truncated"):
        out[key] = bool(value.get(key))
    for key in ("changed_files", "diff_chars", "diff_limit", "chunks",
                "failed_chunks"):
        try:
            number = int(value.get(key) or 0)
        except (TypeError, ValueError, OverflowError):
            number = 0
        out[key] = max(0, number)
    for key in ("reviewed_files", "omitted_files", "ignored_files",
                "partial_files", "chunk_omitted_files"):
        items = value.get(key)
        if not isinstance(items, (list, tuple)):
            items = []
        out[key] = [_text(item, 240) for item in items[:200] if _text(item, 240)]
    return out


def _review_status(value):
    text = str(value or "").strip().lower()
    return text if text in ("ok", "degraded", "partial") else "ok"


def _normalise(row):
    if not isinstance(row, dict):
        raise ValueError("评审记录必须是对象")
    task_id = _text(row.get("task_id"), 96)
    author_cli = _text(row.get("author_cli"), 64)
    reviewer_cli = _text(row.get("reviewer_cli"), 64)
    if not task_id or not author_cli or not reviewer_cli:
        raise ValueError("评审记录缺少 task_id、author_cli 或 reviewer_cli")
    ident = _text(row.get("id"), 96) or uuid.uuid4().hex
    created = _text(row.get("created_at"), 64)
    if not created:
        created = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    try:
        duration = (int(row["duration_ms"])
                    if row.get("duration_ms") is not None else None)
        if duration is not None:
            duration = max(0, min(duration, 7 * 24 * 60 * 60 * 1000))
    except (TypeError, ValueError, OverflowError):
        duration = None
    raw_passed = row.get("passed", False)
    if isinstance(raw_passed, bool):
        passed = raw_passed
    elif isinstance(raw_passed, int) and raw_passed in (0, 1):
        passed = bool(raw_passed)
    elif isinstance(raw_passed, str):
        value = raw_passed.strip().lower()
        passed = value in ("true", "1")
    else:
        passed = False
    return {
        "id": ident,
        "task_id": task_id,
        "run_id": _text(row.get("run_id"), 96),
        "author_cli": author_cli,
        "author_model_family": _text(row.get("author_model_family"), 80),
        "reviewer_cli": reviewer_cli,
        "reviewer_model_family": _text(row.get("reviewer_model_family"), 80),
        "scores": _scores(row.get("scores")),
        "line_comments": _comments(row.get("line_comments")),
        "findings": _findings(row.get("findings")),
        "coverage": _coverage(row.get("coverage")),
        "review_status": _review_status(row.get("review_status")),
        "passed": passed,
        "created_at": created,
        "duration_ms": duration,
        "schema_version": _SCHEMA_VERSION,
    }


def record_review(row):
    """Store a redacted review and return its stable id.

    Repeating the same ``id`` is idempotent.  Unknown input keys are ignored,
    which prevents a caller from accidentally persisting a prompt or raw log.
    """
    item = _normalise(row)
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT OR IGNORE INTO review_records
               (id, task_id, run_id, author_cli, author_model_family,
                reviewer_cli, reviewer_model_family, scores_json,
                line_comments_json, findings_json, coverage_json,
                review_status, passed, created_at, duration_ms,
                schema_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (item["id"], item["task_id"], item["run_id"], item["author_cli"],
             item["author_model_family"], item["reviewer_cli"],
             item["reviewer_model_family"], _json(item["scores"], {}),
             _json(item["line_comments"], []), _json(item["findings"], []),
             _json(item["coverage"], {}), item["review_status"],
             int(item["passed"]), item["created_at"], item["duration_ms"],
             item["schema_version"]),
        )
        conn.commit()
    finally:
        conn.close()
    return item["id"]


def _decode(raw, fallback):
    try:
        value = json.loads(raw)
        return value if isinstance(value, type(fallback)) else fallback
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _row(row):
    item = dict(row)
    item["scores"] = _decode(item.pop("scores_json", "{}"), {})
    item["line_comments"] = _decode(item.pop("line_comments_json", "[]"), [])
    item["findings"] = _decode(item.pop("findings_json", "[]"), [])
    item["coverage"] = _decode(item.pop("coverage_json", "{}"), {})
    item["review_status"] = _review_status(item.get("review_status"))
    item["passed"] = bool(item.get("passed"))
    return item


def list_reviews(task_id, *, limit=100):
    """Return the oldest first reviews for a task, tolerating bad JSON fields."""
    try:
        limit = max(1, min(_MAX_LIMIT, int(limit)))
    except (TypeError, ValueError, OverflowError):
        limit = 100
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT id, task_id, run_id, author_cli, author_model_family,
                      reviewer_cli, reviewer_model_family, scores_json,
                      line_comments_json, findings_json, coverage_json,
                      review_status, passed, created_at, duration_ms,
                      schema_version
                 FROM review_records WHERE task_id = ?
                 ORDER BY created_at ASC, id ASC LIMIT ?""",
            (_text(task_id, 96), limit),
        ).fetchall()
    finally:
        conn.close()
    return [_row(row) for row in rows]


def _finding_row(row):
    item = dict(row)
    for key in ("line_start", "line_end"):
        try:
            item[key] = int(item[key]) if item.get(key) is not None else None
        except (TypeError, ValueError, OverflowError):
            item[key] = None
    return item


def _compact_finding(row):
    return {
        "finding_id": row.get("finding_id") or "",
        "path": row.get("path") or "",
        "line_start": row.get("line_start"),
        "line_end": row.get("line_end"),
        "severity": row.get("severity") or "",
        "header": row.get("header") or "",
        "status": row.get("status") or "",
        "verification": row.get("verification") or "unverified",
        "verification_reason": row.get("verification_reason") or "",
    }


def sync_findings(task_id, run_id, findings, *, allow_resolution=False,
                  coverage_complete=False, reviewed_files=()):
    """把本轮 findings 并入 task 级状态机。

    RESOLVED 条件刻意保守：本轮评审必须完整覆盖该 finding 所在文件，且本轮
    输出通过调用方的前置校验；否则上轮 ACTIVE 原样保留。返回的 ``status``
    已回写到传入/返回的 finding 字典，供报告与修复轮直接使用。
    """
    task_id = _text(task_id, 96)
    run_id = _text(run_id, 96)
    current = _findings(findings)
    reviewed = set()
    for path in reviewed_files or ():
        normalized = review_findings.normalize_path(path)
        if normalized:
            reviewed.add(normalized)
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    summary = {"new": [], "reopened": [], "resolved": [], "dismissed": [],
               "active": [], "counts": {}}
    if not task_id:
        return summary
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = {row["finding_id"]: _finding_row(row) for row in conn.execute(
            "SELECT * FROM review_findings WHERE task_id = ?", (task_id,))}
        current_ids = set()
        for finding in current:
            ident = finding["finding_id"]
            current_ids.add(ident)
            existing = rows.get(ident)
            verification = finding.get("verification") or "unverified"
            if verification == "rejected":
                status = "DISMISSED"
            elif existing and existing.get("status") in ("RESOLVED", "DISMISSED"):
                status = "REOPENED"
            else:
                status = "ACTIVE"
            finding["status"] = status
            values = (status, verification, finding.get("verification_reason") or "",
                      finding.get("path") or "", finding.get("line_start"),
                      finding.get("line_end"), finding.get("severity") or "minor",
                      finding.get("header") or "", finding.get("detail") or "",
                      finding.get("evidence") or "", finding.get("suggestion") or "",
                      run_id, now)
            if existing is None:
                conn.execute(
                    """INSERT INTO review_findings
                       (task_id, finding_id, status, verification,
                        verification_reason, path, line_start, line_end,
                        severity, header, detail, evidence, suggestion,
                        first_seen_run, last_seen_run, first_seen_at,
                        last_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (task_id, ident) + values + (now, now))
                if status == "DISMISSED":
                    summary["dismissed"].append(_compact_finding(dict(
                        finding, status=status, task_id=task_id)))
                else:
                    summary["new"].append(_compact_finding(dict(
                        finding, status=status, task_id=task_id)))
            else:
                conn.execute(
                    """UPDATE review_findings SET
                       status = ?, verification = ?, verification_reason = ?,
                       path = ?, line_start = ?, line_end = ?, severity = ?,
                       header = ?, detail = ?, evidence = ?, suggestion = ?,
                       last_seen_run = ?, last_seen_at = ?,
                       resolved_run = NULL, resolved_at = NULL
                       WHERE task_id = ? AND finding_id = ?""",
                    values + (task_id, ident))
                if status == "REOPENED" and existing.get("status") != "REOPENED":
                    summary["reopened"].append(_compact_finding(dict(
                        finding, status=status, task_id=task_id)))
                elif status == "DISMISSED":
                    summary["dismissed"].append(_compact_finding(dict(
                        finding, status=status, task_id=task_id)))
            if status in ("ACTIVE", "REOPENED"):
                summary["active"].append(_compact_finding(dict(
                    finding, status=status, task_id=task_id)))
        if allow_resolution and coverage_complete and reviewed:
            for ident, row in rows.items():
                if ident in current_ids:
                    continue
                if row.get("status") not in ("ACTIVE", "REOPENED"):
                    continue
                path = review_findings.normalize_path(row.get("path"))
                if not path or path not in reviewed:
                    continue
                conn.execute(
                    """UPDATE review_findings SET
                       status = 'RESOLVED', resolved_run = ?, resolved_at = ?
                       WHERE task_id = ? AND finding_id = ?""",
                    (run_id, now, task_id, ident))
                summary["resolved"].append(_compact_finding(dict(
                    row, status="RESOLVED")))
        conn.commit()
    finally:
        conn.close()
    # 把状态回写到调用方的 findings，报告/修复轮无需再查库。
    by_id = {item["finding_id"]: item for item in current}
    for raw in (findings if isinstance(findings, list) else []):
        if not isinstance(raw, dict):
            continue
        ident = raw.get("finding_id")
        if not ident:
            normalized = review_findings.normalize(raw)
            ident = normalized.get("finding_id") if normalized else ""
        item = by_id.get(ident)
        if item:
            raw["status"] = item.get("status") or ""
            raw["verification"] = item.get("verification") or "unverified"
    summary["counts"] = {
        "active": len(summary["active"]), "new": len(summary["new"]),
        "reopened": len(summary["reopened"]), "resolved": len(summary["resolved"]),
        "dismissed": len(summary["dismissed"]),
    }
    return summary


def list_findings(task_id, statuses=None, *, limit=100):
    try:
        limit = max(1, min(_MAX_LIMIT, int(limit)))
    except (TypeError, ValueError, OverflowError):
        limit = 100
    sql = "SELECT * FROM review_findings WHERE task_id = ?"
    params: list = [_text(task_id, 96)]
    statuses = [str(status).upper() for status in (statuses or ()) if status]
    if statuses:
        sql += " AND status IN (%s)" % ",".join("?" for _ in statuses)
        params.extend(statuses)
    sql += " ORDER BY last_seen_at DESC, finding_id ASC LIMIT ?"
    params.append(limit)
    conn = _connect()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [_finding_row(row) for row in rows]


def delete_task_reviews(task_id):
    """Delete structured records when their owning task is deleted.

    Task archive keeps the records for replay; destructive task deletion can
    call this helper in its existing cleanup transaction boundary.
    """
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute("DELETE FROM review_records WHERE task_id = ?",
                              (_text(task_id, 96),))
        deleted = max(0, int(cursor.rowcount or 0))
        conn.execute("DELETE FROM review_findings WHERE task_id = ?",
                     (_text(task_id, 96),))
        conn.commit()
        return deleted
    finally:
        conn.close()


def cli_statistics(*, author_cli=None, reviewer_cli=None):
    """Aggregate pass rate and average dimensions in Python, without JSON1."""
    conn = _connect()
    try:
        clauses, params = [], []
        if author_cli:
            clauses.append("author_cli = ?")
            params.append(_text(author_cli, 64))
        if reviewer_cli:
            clauses.append("reviewer_cli = ?")
            params.append(_text(reviewer_cli, 64))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            "SELECT author_cli, reviewer_cli, scores_json, passed "
            "FROM review_records" + where, params).fetchall()
    finally:
        conn.close()
    grouped = {}
    for row in rows:
        key = (row["author_cli"], row["reviewer_cli"])
        stat = grouped.setdefault(key, {"author_cli": key[0],
                                        "reviewer_cli": key[1],
                                        "reviews": 0, "passed": 0,
                                        "scores": {}, "score_counts": {}})
        stat["reviews"] += 1
        stat["passed"] += int(bool(row["passed"]))
        for name, score in _decode(row["scores_json"], {}).items():
            try:
                stat["scores"][name] = stat["scores"].get(name, 0.0) + float(score)
                stat["score_counts"][name] = stat["score_counts"].get(name, 0) + 1
            except (TypeError, ValueError):
                continue
    result = []
    for stat in grouped.values():
        counts = stat.pop("score_counts")
        stat["pass_rate"] = round(stat["passed"] / stat["reviews"], 4)
        stat["scores"] = {name: round(total / counts[name], 4)
                           for name, total in stat["scores"].items()
                           if counts.get(name)}
        result.append(stat)
    return sorted(result, key=lambda item: (item["author_cli"], item["reviewer_cli"]))


__all__ = ["init", "record_review", "list_reviews", "list_findings",
           "sync_findings", "delete_task_reviews", "cli_statistics"]
