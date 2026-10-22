# -*- coding: utf-8 -*-
"""Versioned project memory stored inside each workspace.

Candidates must pass a deterministic self-judgment gate before entering:
runs that failed their own gates and runs that produced no reusable facts
stay out, and identical facts from reruns collapse into the first entry.
Qualified candidates are approved automatically after integrity checks.
The journal still keeps explicit review transitions for legacy or manually
held entries.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import stat
import threading
import time
from pathlib import Path

MAX_ENTRY_CHARS = 4000
DEFAULT_TTL_DAYS = 180
_LOCK = threading.RLock()
_LOG = logging.getLogger(__name__)

# 自判闸：这些行只说明「跑过」，对后续会话没有可复用的价值。
_ENTRY_BOILERPLATE_PREFIXES = ("- 任务目标：", "- 任务流程：", "- 完成时间：")
_ENTRY_BOILERPLATE_LINES = (
    "- 直连任务未执行独立验证或评审，执行完成不代表质量验收通过",)
_FAILED_GATE_RE = re.compile(
    r"^- (?:质量门禁|发布门禁|发布结果|评审|验证)：未通过\s*$")


def _file(workdir):
    if not str(workdir or "").strip():
        raise ValueError("工作目录不可用")
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
        # Review decisions can be older than any display window. Stream the
        # whole journal so backfill cannot resurrect an old rejected entry.
        with path.open("rb") as handle:
            for line in handle:
                try:
                    row = json.loads(line.decode("utf-8"))
                    if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"]:
                        latest[row["id"]] = row
                except (ValueError, TypeError, UnicodeError):
                    continue
    except FileNotFoundError:
        return latest
    return latest


def _selfjudge(content):
    """Deterministic value gate for candidate/entry content.

    A failed gate line means the run recorded a transient failure — the
    passing rerun lands later, so the failure itself is not knowledge.
    Content that reduces to goal/engine/time boilerplate carries nothing a
    future session could reuse.  Unknown line shapes count as substantive:
    when unsure, keep.
    """
    text = str(content or "")
    substantive = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _FAILED_GATE_RE.match(line):
            return False, "门禁未通过"
        if line.startswith(_ENTRY_BOILERPLATE_PREFIXES) or line in _ENTRY_BOILERPLATE_LINES:
            continue
        substantive += 1
    if not substantive:
        return False, "无可复用事实"
    return True, ""


def _facts_digest(title, content):
    """Stable digest of facts ignoring the completion timestamp.

    Reruns of the same goal with the same outcome differ only in 完成时间;
    this digest lets capture collapse them into the first entry.
    """
    lines = [ln for ln in str(content or "").splitlines()
             if not ln.strip().startswith("- 完成时间：")]
    payload = str(title or "").strip() + "\0" + "\n".join(lines)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _candidate(*, source_run="", title="", facts=None, now=None):
    facts = [str(x).strip()[:500] for x in (facts or []) if str(x).strip()]
    content = "\n".join("- " + x for x in facts)[:MAX_ENTRY_CHARS]
    if not content:
        return None
    stamp = float(now if now is not None else time.time())
    digest = hashlib.sha256((str(source_run) + "\0" + content).encode("utf-8")).hexdigest()
    return {"id": digest[:24], "source_run": str(source_run or "")[:100],
           "title": str(title or "")[:200], "content": content,
           "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
           "facts_sha256": _facts_digest(title, content),
           "created_at": stamp, "expires_at": stamp + DEFAULT_TTL_DAYS * 86400,
           "status": "approved", "version": 1, "updated_at": stamp,
           "reviewed_at": stamp, "approval_mode": "automatic"}


def propose(workdir, *, source_run="", title="", facts=None, now=None):
    """Persist a verified candidate and make it active by default."""
    row = _candidate(source_run=source_run, title=title, facts=facts, now=now)
    if row is None:
        return None
    with _LOCK:
        path = _file(workdir)
        old = _read(path).get(row["id"])
        if old:
            return old
        _append(path, row)
        return row


def _number(value, default=0, *, integer=False):
    try:
        number = float(value)
        if math.isfinite(number):
            return int(number) if integer else number
    except (TypeError, ValueError, OverflowError):
        pass
    return default


def list_entries(workdir, *, now=None):
    stamp = float(now if now is not None else time.time())
    rows = list(_read(_file(workdir)).values())
    for row in rows:
        raw = row.get("content")
        digest = _content_digest(raw)
        row["verified"] = (isinstance(raw, str) and _number(row.get("version")) >= 1
                           and digest is not None and digest == row.get("content_sha256"))
        row["content"] = raw if digest is not None else "（记忆内容格式无效）"
        row["title"] = str(row.get("title") or "")
        row["created_at"] = _number(row.get("created_at"))
        row["expires_at"] = _number(row.get("expires_at"))
        row["version"] = _number(row.get("version"), integer=True)
        row["expired"] = row["expires_at"] <= stamp
        row["feedback_score"] = _number(row.get("feedback_score"), integer=True)
        row["feedback_count"] = _number(row.get("feedback_count"), integer=True)
    return sorted(rows, key=lambda x: (x.get("created_at", 0), x.get("id", "")), reverse=True)


def _content_digest(content):
    if not isinstance(content, str):
        return None
    try:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
    except UnicodeError:
        return None


def migrate_legacy(workdir):
    """Import the old Markdown store as automatically approved entries."""
    path = _file(workdir)
    legacy = path.with_name("project-memory.md")
    if legacy.is_symlink() or (hasattr(legacy, "is_junction") and legacy.is_junction()):
        raise ValueError("拒绝符号链接项目记忆文件")
    if not legacy.is_file():
        return 0
    with legacy.open(encoding="utf-8-sig") as handle:
        text = handle.read(2 * 1024 * 1024 + 1)
    if len(text) > 2 * 1024 * 1024:
        raise ValueError("旧版项目记忆文件过大")
    added = 0
    with _LOCK:
        latest = _read(path)
        sources = {row["source_run"] for row in latest.values()
                   if isinstance(row.get("source_run"), str)}
        for section in re.split(r"(?m)^### ", text)[1:]:
            heading, _, body = section.partition("\n")
            facts = [line[2:].strip() for line in body.splitlines() if line.startswith("- ")]
            source = "legacy:" + hashlib.sha256(section.strip().encode("utf-8")).hexdigest()[:32]
            if not facts or source in sources:
                continue
            title, _, stamp = heading.rpartition(" · ")
            try:
                created_at = time.mktime(time.strptime(stamp.strip(), "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                created_at = legacy.stat().st_mtime
            row = _candidate(source_run=source, title=title or heading.strip(),
                             facts=facts, now=created_at)
            if row and row["id"] not in latest:
                _append(path, row)
                latest[row["id"]] = row
                sources.add(source)
                added += 1
    return added


def _run_candidate(task, run):
    """Build execution facts from the run's pinned inputs, not later task edits."""
    if (run.get("status") != "done" or not run.get("id")
            or run.get("kind") != "orchestration"
            or not task.get("workdir") or run.get("task_id") != task.get("id")):
        return None
    verdict = run.get("verdict") if isinstance(run.get("verdict"), dict) else {}
    if verdict.get("partial"):
        return None
    execution_snapshot = run.get("execution_snapshot") if isinstance(run.get("execution_snapshot"), dict) else {}
    original_workdir = execution_snapshot.get("workdir")
    if original_workdir and Path(original_workdir).expanduser().resolve() != Path(task["workdir"]).expanduser().resolve():
        return None  # A task moved to another project must not import the old project's facts.
    snapshot = run.get("task_spec") if isinstance(run.get("task_spec"), dict) else {}
    title = snapshot.get("title", run.get("title") or task.get("title") or "")
    goal = snapshot.get("goal", task.get("goal") or title)
    manuscript = snapshot.get("deliverable", task.get("manuscript") or "")
    engine = verdict.get("engine") or snapshot.get("engine") or task.get("engine") or task.get("type") or ""
    facts = ["任务目标：%s" % str(goal)[:500],
             "任务流程：%s；执行已完成" % engine]
    if engine == "direct":
        facts.append("直连任务未执行独立验证或评审，执行完成不代表质量验收通过")
    else:
        for field, label in (("pass", "质量门禁"), ("publishable", "发布门禁"),
                             ("verify_pass", "验证"), ("review_pass", "评审")):
            if field == "verify_pass" and verdict.get("verify_ran") is False:
                facts.append("验证：未执行独立验证")
                continue
            if isinstance(verdict.get(field), bool):
                facts.append("%s：%s" % (label, "通过" if verdict[field] else "未通过"))
    if verdict.get("start_chapter") is not None and verdict.get("end_chapter") is not None:
        facts.append("本次章节范围：%s–%s" % (verdict["start_chapter"], verdict["end_chapter"]))
    for step in run.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if step.get("role") == "verify":
            command = (step.get("verify_command") or
                       execution_snapshot.get("verify_command") or
                       "（历史命令未记录）")
            facts.append("验证命令 `%s` → %s" % (command, step.get("status") or "未知"))
    if manuscript:
        facts.append("稿件文件：%s" % manuscript)
    if run.get("ended_at"):
        facts.append("完成时间：%s" % run["ended_at"])
    try:
        created_at = time.mktime(time.strptime(run.get("ended_at") or "", "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        created_at = time.time()
    ok, _reason = _selfjudge("\n".join("- " + x for x in facts))
    if not ok:
        return None  # 自判闸：失败的尝试或只有套话的运行不产生项目记忆。
    return _candidate(source_run=run["id"], title=title,
                      facts=facts, now=created_at)


def capture_run(task, run):
    """Capture bounded execution facts from every completed task engine."""
    candidate = _run_candidate(task, run)
    if candidate is None:
        return None
    with _LOCK:
        path = _file(task["workdir"])
        for row in _read(path).values():
            if row.get("source_run") == run["id"] or row["id"] == candidate["id"]:
                return row  # Never replace an earlier approval/rejection on backfill.
            known = row.get("facts_sha256") or _facts_digest(
                row.get("title"), row.get("content"))
            if known == candidate["facts_sha256"]:
                return row  # 同一事实的重复 run 不再入账，也不推翻已有裁决。
        _append(path, candidate)
        return candidate


def backfill(tasks, runs):
    """Recover missing candidates without model calls.

    Startup also retracts earlier automatic approvals that fail the current
    self-judgment gate; entries a human decided stay untouched.
    """
    # Startup runs this before accepting requests. Share the lock with live
    # capture, and read each workspace journal only once for the run batch.
    with _LOCK:
        return _backfill(tasks, runs)


def _demote_unqualified_locked(path, latest, *, now=None):
    """Archive automatic approvals that the self-judgment gate would reject.

    Deliberately narrow: only rows no human ever decided (approval_mode is
    automatic, digest still verified, not expired) are demoted, and demotion
    lands as a reversible archived transition instead of a deletion.
    """
    stamp = float(now if now is not None else time.time())
    demoted = 0
    for entry_id, row in latest.items():
        if (row.get("status") != "approved"
                or row.get("approval_mode") != "automatic"
                or _content_digest(row.get("content")) != row.get("content_sha256")
                or 0 < _number(row.get("expires_at")) <= stamp):
            continue
        ok, reason = _selfjudge(row.get("content"))
        if ok:
            continue
        updated = dict(row, status="archived",
                       version=int(row.get("version") or 1) + 1,
                       updated_at=stamp, reviewed_at=stamp,
                       demoted_reason="自动入账自判未通过：%s" % reason)
        _append(path, updated)
        latest[entry_id] = updated
        demoted += 1
    return demoted


def _backfill(tasks, runs):
    by_id = {task["id"]: task for task in tasks if task.get("id")}
    result = {"legacy": 0, "runs": 0, "errors": 0, "demoted": 0}
    sources = {}
    existing_ids = {}
    known_facts = {}
    for task in by_id.values():
        workdir = task.get("workdir")
        if not workdir:
            continue
        try:
            if not Path(str(workdir)).expanduser().is_dir():
                # Historical tasks can outlive a deleted checkout.  This is
                # expected cleanup, not a migration error and should not
                # flood startup logs with a traceback-shaped warning.
                result["skipped"] = int(result.get("skipped") or 0) + 1
                continue
        except OSError:
            result["skipped"] = int(result.get("skipped") or 0) + 1
            continue
        try:
            key = str(_file(workdir))
            if key not in sources:
                result["legacy"] += migrate_legacy(workdir)
                # Older releases left valid candidates pending.  The current
                # default is automatic approval, so migrate them on startup;
                # expired or tampered entries remain blocked by the helper.
                path = _file(workdir)
                latest = _read(path)
                _approve_pending_locked(path, latest)
                result["demoted"] += _demote_unqualified_locked(path, latest)
                sources[key] = {row["source_run"] for row in latest.values()
                                if isinstance(row.get("source_run"), str)}
                existing_ids[key] = set(latest)
                known_facts[key] = {
                    (row.get("facts_sha256") or _facts_digest(
                        row.get("title"), row.get("content")))
                    for row in latest.values()}
        except (OSError, ValueError) as exc:
            result["errors"] += 1
            _LOG.warning("项目记忆迁移失败（任务 %s）：%s", task["id"], exc)
    for run in runs:
        task = by_id.get(run.get("task_id"))
        if (not task or not task.get("workdir") or run.get("status") != "done"
                or run.get("kind") != "orchestration"):
            continue
        try:
            key = str(_file(task.get("workdir")))
            if run.get("id") in sources.get(key, set()):
                continue
            row = _run_candidate(task, run)
            if (row and row["id"] not in existing_ids.get(key, set())
                    and row["facts_sha256"] not in known_facts.get(key, set())):
                _append(_file(task["workdir"]), row)
                sources.setdefault(key, set()).add(run["id"])
                existing_ids.setdefault(key, set()).add(row["id"])
                known_facts.setdefault(key, set()).add(row["facts_sha256"])
                result["runs"] += 1
        except (OSError, ValueError, TypeError) as exc:
            result["errors"] += 1
            _LOG.warning("项目记忆回填失败（运行 %s）：%s", run.get("id"), exc)
    return result


def project_index(tasks, *, now=None):
    """Return a path-free memory index grouped by workspace.

    The UI needs one project-level view, while review actions still target an
    existing task endpoint.  Keep filesystem paths server-side and expose only
    stable task ids, a display name, and the existing governed entries.
    """
    groups = {}
    entries_by_workspace = {}
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        workdir = str(task.get("workdir") or "").strip()
        task_id = str(task.get("id") or "").strip()
        if not workdir or not task_id:
            continue
        try:
            path = _file(workdir)
            key = str(path.parent.resolve())
            if key not in entries_by_workspace:
                entries_by_workspace[key] = list_entries(workdir, now=now)
            entries = entries_by_workspace[key]
        except (OSError, ValueError):
            continue
        if not entries:
            continue
        group = groups.setdefault(key, {"task_ids": [], "name": "", "entries": entries})
        if task_id not in group["task_ids"]:
            group["task_ids"].append(task_id)
        if not group["name"]:
            group["name"] = str(task.get("name") or task.get("title") or
                                  task.get("goal") or "项目记忆").strip()[:160]
    result = []
    for group in groups.values():
        entries = group["entries"]
        result.append({
            "name": group["name"] or "项目记忆",
            "task_ids": group["task_ids"],
            "entries": entries,
            "counts": {
                "total": len(entries),
                "pending": sum(1 for x in entries if x.get("status") == "pending"),
                "approved": sum(1 for x in entries if x.get("status") == "approved"),
                "archived": sum(1 for x in entries if x.get("status") == "archived"),
            },
        })
    return sorted(result, key=lambda x: (x["name"].lower(), x["task_ids"][0]))


def decide(workdir, entry_id, action, *, expected_version=None, now=None):
    with _LOCK:
        return _decide(workdir, entry_id, action, expected_version=expected_version, now=now)


def approve_pending(workdir, *, now=None):
    """Approve every pending candidate that passes integrity and expiry checks.

    Bulk approval is intentionally narrower than a blind status update: tampered
    or expired candidates remain pending so they cannot be enabled accidentally.
    The append-only journal and process lock keep the batch consistent with the
    single-entry review path.
    """
    with _LOCK:
        path = _file(workdir)
        return _approve_pending_locked(path, _read(path), now=now)


def _approve_pending_locked(path, latest, *, now=None):
    stamp = float(now if now is not None else time.time())
    approved_count = skipped_count = invalid_count = 0
    for entry_id, row in latest.items():
        if row.get("status") != "pending":
            continue
        raw = row.get("content")
        digest = _content_digest(raw)
        version = _number(row.get("version"), integer=True)
        if (digest is None or digest != row.get("content_sha256")
                or version < 1):
            invalid_count += 1
            continue
        if _number(row.get("expires_at")) <= stamp:
            skipped_count += 1
            continue
        updated = dict(
            row,
            status="approved",
            version=version + 1,
            updated_at=stamp,
            reviewed_at=stamp,
        )
        _append(path, updated)
        latest[entry_id] = updated
        approved_count += 1
    return {
        "approved_count": approved_count,
        "skipped_count": skipped_count,
        "invalid_count": invalid_count,
    }


def _decide(workdir, entry_id, action, *, expected_version=None, now=None):
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
    raw = row.get("content")
    digest = _content_digest(raw)
    if digest is None or digest != row.get("content_sha256"):
        raise ValueError("记忆内容指纹校验失败")
    transitions = {"approve": "pending", "reject": "pending",
                   "archive": "approved", "restore": "archived"}
    if row.get("status") != transitions[action]:
        raise ValueError("当前记忆状态不允许此操作")
    if action in ("approve", "restore") and _number(row.get("expires_at")) <= stamp:
        raise ValueError("记忆已过期，不能启用；请重新生成候选")
    row = dict(row, status={"approve": "approved", "reject": "rejected",
                            "archive": "archived", "restore": "approved"}[action],
               version=int(row.get("version") or 1) + 1, updated_at=stamp,
               reviewed_at=stamp)
    _append(path, row)
    return row


def feedback(workdir, entry_id, *, useful, expected_version=None, now=None):
    """Record explicit retrieval feedback as a versioned append-only update."""
    with _LOCK:
        return _feedback(workdir, entry_id, useful=useful, expected_version=expected_version, now=now)


def _feedback(workdir, entry_id, *, useful, expected_version=None, now=None):
    if not isinstance(useful, bool):
        raise ValueError("useful 必须是布尔值")
    path = _file(workdir)
    row = _read(path).get(str(entry_id or ""))
    if not row:
        raise KeyError("记忆条目不存在")
    if expected_version is not None and int(expected_version) != int(row.get("version") or 1):
        raise ValueError("记忆条目版本已变化，请刷新后重试")
    raw = row.get("content")
    digest = _content_digest(raw)
    if digest is None or digest != row.get("content_sha256"):
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
        cap = min(20000, max(0, int(cap)))
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


def injection_preview(workdir, *, cap=4000, query="", now=None):
    """Explain exactly which approved memory entries would be injected.

    The returned text is the same bounded content as ``active_text``; metadata
    is deliberately separate so callers can render provenance without putting
    extra bookkeeping into the model context.
    """
    stamp = float(now if now is not None else time.time())
    try:
        cap = min(20000, max(0, int(cap)))
    except (TypeError, ValueError):
        cap = 4000
    heading = "## 已批准项目记忆（内容已校验，过期条目不注入）\n\n"
    body_cap = max(0, cap - len(heading) - 2)
    terms = _query_terms(query)
    candidates = []
    for row in list_entries(workdir, now=stamp):
        if row.get("status") != "approved" or row.get("expired") or not row.get("verified"):
            continue
        block = "### %s\n%s" % (row.get("title") or "项目记忆", row.get("content") or "")
        candidates.append((row, block))
    candidates.sort(key=lambda item: (_relevance(item[0], terms),
                                      item[0].get("feedback_score", 0),
                                      item[0].get("created_at", 0)), reverse=True)
    selected, used = [], 0
    for row, block in candidates:
        cost = len(block) + (2 if selected else 0)
        if used + cost > body_cap:
            continue
        selected.append((row, block))
        used += cost
    return {
        "text": (heading + "\n\n".join(block for _row, block in selected) + "\n\n"
                 if selected else ""),
        "entries": [{"id": row.get("id"), "title": row.get("title"),
                     "source_run": row.get("source_run", ""),
                     "version": int(row.get("version") or 1),
                     "content_sha256": row.get("content_sha256", ""),
                     "feedback_score": int(row.get("feedback_score") or 0)}
                    for row, _block in selected],
        "budget": {"cap": cap, "used": len((heading + "\n\n".join(
            block for _row, block in selected) + "\n\n") if selected else "")},
        "query": str(query or "")[:500],
    }
