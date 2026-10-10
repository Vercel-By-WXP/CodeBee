# -*- coding: utf-8 -*-
"""Versioned, approval-gated project memory stored inside each workspace."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path

MAX_ENTRY_CHARS = 4000
DEFAULT_TTL_DAYS = 180


def _file(workdir):
    root = Path(workdir or "").expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("工作目录不可用")
    directory = root / ".codebee"
    if directory.is_symlink() or (hasattr(directory, "is_junction") and directory.is_junction()):
        raise ValueError("拒绝使用工作目录外的项目记忆路径")
    if directory.exists() and not directory.is_dir():
        raise ValueError("项目记忆目录路径不是目录")
    resolved_dir = directory.resolve(strict=False)
    if resolved_dir != root and root not in resolved_dir.parents:
        raise ValueError("项目记忆目录超出工作目录")
    target = directory / "project-memory.jsonl"
    if target.is_symlink() or (hasattr(target, "is_junction") and target.is_junction()):
        raise ValueError("拒绝符号链接项目记忆文件")
    resolved_target = target.resolve(strict=False)
    if resolved_target.parent != resolved_dir:
        raise ValueError("项目记忆文件路径越界")
    return target


def _append(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("项目记忆路径不是普通文件")
        payload = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        with os.fdopen(fd, "ab", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
    finally:
        os.close(fd)


def _read(path):
    latest = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-5000:]
    except OSError:
        return latest
    for line in lines:
        try:
            row = json.loads(line)
            if isinstance(row, dict) and row.get("id"):
                latest[row["id"]] = row
        except (ValueError, TypeError):
            continue
    return latest


def propose(workdir, *, source_run="", title="", facts=None, now=None):
    """Persist a candidate, never active until a human explicitly approves it."""
    facts = [str(x).strip()[:500] for x in (facts or []) if str(x).strip()]
    content = "\n".join("- " + x for x in facts)[:MAX_ENTRY_CHARS]
    if not content:
        return None
    stamp = float(now if now is not None else time.time())
    digest = hashlib.sha256((str(source_run) + "\0" + content).encode("utf-8")).hexdigest()
    row = {"id": digest[:24], "source_run": str(source_run or "")[:100],
           "title": str(title or "")[:200], "content": content,
           "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
           "created_at": stamp, "expires_at": stamp + DEFAULT_TTL_DAYS * 86400,
           "status": "pending", "version": 1, "updated_at": stamp}
    path = _file(workdir)
    latest = _read(path)
    old = latest.get(row["id"])
    if old:
        return old
    _append(path, row)
    return row


def list_entries(workdir, *, now=None):
    stamp = float(now if now is not None else time.time())
    rows = list(_read(_file(workdir)).values())
    for row in rows:
        raw = row.get("content") or ""
        row["verified"] = hashlib.sha256(raw.encode("utf-8")).hexdigest() == row.get("content_sha256")
        row["expired"] = float(row.get("expires_at") or 0) <= stamp
        row["feedback_score"] = int(row.get("feedback_score") or 0)
        row["feedback_count"] = int(row.get("feedback_count") or 0)
    return sorted(rows, key=lambda x: (x.get("created_at", 0), x.get("id", "")), reverse=True)


def decide(workdir, entry_id, action, *, expected_version=None, now=None):
    if action not in ("approve", "reject", "archive", "restore"):
        raise ValueError("action 必须是 approve/reject/archive/restore")
    path = _file(workdir)
    latest = _read(path)
    row = latest.get(str(entry_id or ""))
    if not row:
        raise KeyError("记忆条目不存在")
    if expected_version is not None and int(expected_version) != int(row.get("version") or 1):
        raise ValueError("记忆条目版本已变化，请刷新后重试")
    stamp = float(now if now is not None else time.time())
    raw = row.get("content") or ""
    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != row.get("content_sha256"):
        raise ValueError("记忆内容指纹校验失败")
    transitions = {"approve": "pending", "reject": "pending",
                   "archive": "approved", "restore": "archived"}
    if row.get("status") != transitions[action]:
        raise ValueError("当前记忆状态不允许此操作")
    if action in ("approve", "restore") and float(row.get("expires_at") or 0) <= stamp:
        raise ValueError("记忆已过期，不能启用；请重新生成候选")
    row = dict(row, status={"approve": "approved", "reject": "rejected",
                            "archive": "archived", "restore": "approved"}[action],
               version=int(row.get("version") or 1) + 1, updated_at=stamp,
               reviewed_at=stamp)
    _append(path, row)
    return row


def feedback(workdir, entry_id, *, useful, expected_version=None, now=None):
    """Record explicit retrieval feedback as a versioned append-only update."""
    if not isinstance(useful, bool):
        raise ValueError("useful 必须是布尔值")
    path = _file(workdir)
    row = _read(path).get(str(entry_id or ""))
    if not row:
        raise KeyError("记忆条目不存在")
    if expected_version is not None and int(expected_version) != int(row.get("version") or 1):
        raise ValueError("记忆条目版本已变化，请刷新后重试")
    raw = row.get("content") or ""
    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != row.get("content_sha256"):
        raise ValueError("记忆内容指纹校验失败")
    if row.get("status") != "approved":
        raise ValueError("只有已批准的记忆条目可以反馈")
    stamp = float(now if now is not None else time.time())
    row = dict(row, feedback_score=int(row.get("feedback_score") or 0) +
               (1 if useful else -1),
               feedback_count=int(row.get("feedback_count") or 0) + 1,
               feedback_at=stamp, version=int(row.get("version") or 1) + 1,
               updated_at=stamp)
    _append(path, row)
    return row


def _query_terms(query):
    query = str(query or "").strip().lower()
    words = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", query)
    terms = set()
    for word in words:
        if len(word) > 2 and re.fullmatch(r"[\u3400-\u9fff]+", word):
            terms.update(word[i:i + 2] for i in range(len(word) - 1))
        else:
            terms.add(word)
    return terms


def _relevance(row, terms):
    if not terms:
        return 0
    title = str(row.get("title") or "").lower()
    content = str(row.get("content") or "").lower()
    title_terms = _query_terms(title)
    content_terms = _query_terms(content)
    return 4 * len(terms & title_terms) + len(terms & content_terms)


def active_text(workdir, *, cap=4000, query="", now=None):
    stamp = float(now if now is not None else time.time())
    try:
        cap = max(0, int(cap))
    except (TypeError, ValueError):
        cap = 4000
    heading = "## 已批准项目记忆（内容已校验，过期条目不注入）\n\n"
    cap = max(0, cap - len(heading) - 2)
    terms = _query_terms(query)
    items = []
    for row in list_entries(workdir, now=stamp):
        if row.get("status") != "approved" or row.get("expired") or not row.get("verified"):
            continue
        block = "### %s\n%s" % (row.get("title") or "项目记忆", row.get("content") or "")
        items.append((row, block))
    items.sort(key=lambda item: (_relevance(item[0], terms),
                                 item[0].get("feedback_score", 0),
                                 item[0].get("created_at", 0)), reverse=True)
    selected = []
    used = 0
    for _row, block in items:
        cost = len(block) + (2 if selected else 0)
        if used + cost > cap:
            continue
        selected.append(block)
        used += cost
    text = "\n\n".join(selected)
    return (heading + text + "\n\n") if text else ""
