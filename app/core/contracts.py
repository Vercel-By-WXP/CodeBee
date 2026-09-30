"""Durable task contracts, evidence, approvals and completion receipts."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path

from . import paths

_DIR = paths.DATA_DIR / "contracts"
_LOCK = threading.RLock()
_ID_RE = re.compile(r"^[A-Za-z][0-9A-Za-z_-]{0,99}$")
_CRITERIA_MAX = 40
_EVIDENCE_MAX = 300
_MISSING = object()
_CASE_KEYWORDS = {
    "correctness": ("正确", "验证", "测试", "质量", "correct", "test", "verify"),
    "performance": ("性能", "耗时", "成本", "token", "performance", "latency", "cost"),
    "boundary": ("边界", "极端", "boundary", "edge"),
    "exception": ("异常", "失败处理", "容错", "exception", "error", "failure"),
    "readability": ("可读", "文档", "表达", "readability", "documentation"),
    "security": ("安全", "权限", "注入", "security", "auth", "injection"),
}


def _valid_id(task_id):
    return bool(_ID_RE.fullmatch(str(task_id or "")))


def _path(task_id):
    if not _valid_id(task_id):
        raise ValueError("任务 ID 无效")
    return _DIR / (str(task_id) + ".json")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _save(item):
    p = _path(item["task_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _load(task_id):
    try:
        value = json.loads(_path(task_id).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _actor(value):
    return str(value or "local").strip()[:120] or "local"


def create(task_id, payload=None):
    """Create or update a task's contract, preserving evidence and audit history."""
    payload = payload if isinstance(payload, dict) else {}
    raw_criteria = payload.get("acceptance_criteria", _MISSING)
    if raw_criteria is not _MISSING and not isinstance(raw_criteria, list):
        raise ValueError("acceptance_criteria 必须是列表")
    clean = []
    for value in (raw_criteria if raw_criteria is not _MISSING else [])[:_CRITERIA_MAX]:
        text = str(value or "").strip()[:500]
        if text and text not in clean:
            clean.append(text)
    with _LOCK:
        item = _load(task_id) or {
            "version": 1, "task_id": str(task_id), "created_at": _now(),
            "acceptance_criteria": [], "evidence": [], "approval_required": False,
            "approval": None, "blocked_reason": "", "completion_receipt": None,
            "audit": [], "status": "open",
        }
        # Partial updates must not erase an existing contract field merely
        # because the caller omitted it (MCP/UI frequently send sparse patches).
        if raw_criteria is not _MISSING:
            item["acceptance_criteria"] = clean
        if "approval_required" in payload:
            approval_required = payload.get("approval_required")
            if not isinstance(approval_required, bool):
                raise ValueError("approval_required 必须是布尔值")
            item["approval_required"] = approval_required
        item["updated_at"] = _now()
        item["audit"].append({"at": item["updated_at"], "actor": _actor(payload.get("actor")),
                              "action": "contract_updated",
                              "criteria_count": len(item.get("acceptance_criteria") or [])})
        _save(item)
        return item


def get(task_id):
    with _LOCK:
        return _load(task_id)


def add_evidence(task_id, evidence, actor="local"):
    if not isinstance(evidence, dict):
        raise ValueError("证据必须是对象")
    status = str(evidence.get("status") or "unknown").lower()
    if status not in ("passed", "failed", "unknown", "not_evaluated"):
        raise ValueError("证据状态无效")
    with _LOCK:
        item = _load(task_id)
        if not item:
            item = create(task_id, {})
        row = {"id": "ev-%s" % uuid.uuid4().hex[:20],
               "criterion": str(evidence.get("criterion") or "")[:500], "status": status,
               "summary": str(evidence.get("summary") or "")[:2000],
               "source": str(evidence.get("source") or "manual")[:80], "at": _now(),
               "actor": _actor(actor)}
        item["evidence"].append(row)
        item["evidence"] = item["evidence"][-_EVIDENCE_MAX:]
        item["audit"].append({"at": row["at"], "actor": row["actor"],
                              "action": "evidence_added", "evidence_id": row["id"]})
        item["updated_at"] = row["at"]
        _save(item)
        return item


def record_acceptance_cases(task_id, cases, actor="system"):
    """Map acceptance-matrix cases onto matching user criteria.

    Matching is deliberately conservative: only criteria containing a known
    case keyword (or the explicit ``acceptance:<case>`` form) are linked. Any
    unmatched criterion remains unverified rather than being guessed green.
    """
    if not isinstance(cases, list):
        return get(task_id)
    item = get(task_id)
    if not item:
        return None
    criteria = item.get("acceptance_criteria") or []
    for case in cases:
        if not isinstance(case, dict):
            continue
        name = str(case.get("case") or "").strip().lower()
        status = str(case.get("status") or "unknown").lower()
        if name not in _CASE_KEYWORDS or status not in ("passed", "failed", "unknown", "not_evaluated"):
            continue
        for criterion in criteria:
            text = str(criterion or "").strip()
            low = text.lower()
            explicit = low == "acceptance:" + name
            matched = explicit or any(keyword.lower() in low for keyword in _CASE_KEYWORDS[name])
            if not matched:
                continue
            add_evidence(task_id, {"criterion": text, "status": status,
                                   "summary": case.get("reason") or
                                             "acceptance matrix: " + name,
                                   "source": "acceptance"}, actor=actor)
    return get(task_id)


def _decision(task_id, status, actor, note):
    with _LOCK:
        item = _load(task_id)
        if not item:
            return None, "任务契约不存在"
        decision = {"status": status, "actor": _actor(actor),
                    "note": str(note or "").strip()[:1000], "at": _now()}
        item["approval"] = decision
        item["status"] = "approved" if status == "approved" else "open"
        item["audit"].append(dict(decision, action=status))
        item["updated_at"] = decision["at"]
        _save(item)
        return item, None


def approve(task_id, actor="local", note=""):
    return _decision(task_id, "approved", actor, note)


def reject(task_id, actor="local", note=""):
    if not str(note or "").strip():
        return None, "拒绝审批必须说明原因"
    return _decision(task_id, "rejected", actor, note)


def set_blocked(task_id, reason, actor="local"):
    reason = str(reason or "").strip()[:1000]
    if not reason:
        return None, "Blocked 状态必须填写原因"
    with _LOCK:
        item = _load(task_id)
        if not item:
            return None, "任务契约不存在"
        item["status"] = "blocked"
        item["blocked_reason"] = reason
        item["updated_at"] = _now()
        item["audit"].append({"at": item["updated_at"], "actor": _actor(actor),
                              "action": "blocked", "reason": reason})
        _save(item)
        return item, None


def release_allowed(task_id):
    item = get(task_id)
    # Task creation persists the approval bit redundantly for backwards
    # compatibility.  Combine both sources with OR so a damaged/tampered
    # sidecar cannot downgrade an approval-protected task to releasable.
    try:
        from . import store
        task = store.get_task(task_id) or {}
    except Exception:
        task = {}
    requires = bool((item or {}).get("approval_required")) or bool(task.get("approval_required"))
    if not requires:
        return True
    if item is None:
        return False
    approval = item.get("approval") or {}
    return approval.get("status") == "approved"


def complete(task_id, run):
    if not isinstance(run, dict) or run.get("status") not in ("done", "failed", "cancelled", "timeout"):
        return None, "完成回执只能关联终态运行"
    with _LOCK:
        item = _load(task_id)
        if not item:
            item = create(task_id, {})
        criteria = item.get("acceptance_criteria") or []
        evidence = item.get("evidence") or []
        latest = {}
        for row in evidence:
            if row.get("criterion"):
                latest[row["criterion"]] = row
        checked = [latest.get(c) for c in criteria]
        receipt = {"version": 1, "task_id": str(task_id), "run_id": run.get("id"),
                   "run_status": run.get("status"), "criteria_total": len(criteria),
                   "criteria_passed": sum(1 for row in checked if row and row.get("status") == "passed"),
                   "criteria_failed": sum(1 for row in checked if row and row.get("status") == "failed"),
                   "criteria_unverified": sum(1 for row in checked if not row or row.get("status") not in ("passed", "failed")),
                   "evidence_ids": [row.get("id") for row in checked if row],
                   "cost_usd": float(run.get("cost_usd") or 0), "tokens": int(run.get("tokens") or 0),
                   "completed_at": _now()}
        # Completion is idempotent for the same run.  A late duplicate status
        # write must not manufacture a second audit event or mutate the
        # original cost/token receipt.
        previous = item.get("completion_receipt") or {}
        if previous.get("run_id") == receipt["run_id"]:
            return previous, None
        item["completion_receipt"] = receipt
        item["audit"].append({"at": receipt["completed_at"], "actor": "system",
                              "action": "completion_receipt_created", "run_id": run.get("id")})
        item["updated_at"] = receipt["completed_at"]
        _save(item)
        return receipt, None
