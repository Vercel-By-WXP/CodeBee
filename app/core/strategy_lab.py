# -*- coding: utf-8 -*-
"""机制闸对账（Raven Evolver 借鉴）：影子台账 → 对账报告 → 人工采用。

链路：router._shadow_note 在每次冠军选将后追加一条记录（挑战者会选谁）；
report() 把影子记录与 run.json 步骤结局对账，产出分歧率/潜在挽回等指标；
adopt() 把挑战者权重并为主权重（仅人工触发，绝不自动采用——与漂移闸纪律
一致：机制层改动必须带证据上岗）。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import atomic, paths, strategy_config

_LOCK = threading.RLock()
_FILE = paths.DATA_DIR / "strategy_shadow.jsonl"
_REPORT_FILE = paths.DATA_DIR / "strategy_lab_report.json"
_MAX_LINES = 20000
_ROTATE_BYTES = 8 * 1024 * 1024
_FAILED_STATUSES = ("failed", "timeout")


def append_shadow(record: Dict[str, Any]) -> None:
    """追加一条影子记录；超限轮转（保留最近 _MAX_LINES 行，原子重写）。"""
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with _LOCK:
        try:
            paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
            rotate = False
            try:
                rotate = _FILE.stat().st_size > _ROTATE_BYTES
            except OSError:
                pass
            if rotate:
                _rotate_locked()
            with _FILE.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            # 影子台账写不进（磁盘满/占用）绝不连累选将调用方。
            pass


def _rotate_locked() -> None:
    try:
        lines = _FILE.read_text(encoding="utf-8").splitlines()
    except Exception:
        return
    kept = [ln for ln in lines[-_MAX_LINES:] if ln.strip()]
    atomic.write_text(_FILE, "\n".join(kept) + ("\n" if kept else ""))


def read_shadow(limit: int = 50000) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    try:
        text = _FILE.read_text(encoding="utf-8")
    except Exception:
        return records
    for line in text.splitlines()[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def _within_days(ts: str, days: float) -> bool:
    if days <= 0:
        return True
    try:
        stamp = time.mktime(time.strptime(str(ts or ""), "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return False
    return (time.time() - stamp) <= days * 86400.0


def _step_outcomes(run_id: str) -> Dict[str, str]:
    """{agent_id: 步骤结局}——该 run 内该 agent 的最差步骤状态。"""
    out: Dict[str, str] = {}
    if not run_id:
        return out
    path = Path(paths.RUNS_DIR) / str(run_id) / "run.json"
    try:
        run = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return out
    for step in run.get("steps") or []:
        if not isinstance(step, dict):
            continue
        agent = str(step.get("agent") or "")
        status = str(step.get("status") or "")
        if not agent or not status:
            continue
        prev = out.get(agent)
        if prev in _FAILED_STATUSES:
            continue
        out[agent] = status
    return out


def report(days: float = 7.0) -> Dict[str, Any]:
    """影子对账报告：分歧率、潜在挽回（分歧且冠军步骤失败）、分数差分布。

    结局关联按 run 内 agent 步骤状态聚合（最差优先）——pick 的 role 与
    步骤 role 命名有 implement/draft 之差，按 agent 关联比按 role 稳。"""
    records = [r for r in read_shadow() if _within_days(r.get("ts") or "", days)]
    outcome_cache: Dict[str, Dict[str, str]] = {}
    total = len(records)
    diverged = 0
    champion_failed = 0
    champion_ok = 0
    unresolved = 0
    deltas: List[float] = []
    by_role: Dict[str, Dict[str, int]] = {}
    for rec in records:
        role = str(rec.get("role") or "?")
        slot = by_role.setdefault(role, {"total": 0, "diverged": 0,
                                         "champion_failed": 0})
        slot["total"] += 1
        if not rec.get("diverged"):
            continue
        diverged += 1
        slot["diverged"] += 1
        try:
            deltas.append(round(float(rec.get("challenger_score") or 0.0)
                                - float(rec.get("champion_score") or 0.0), 1))
        except (TypeError, ValueError):
            pass
        run_id = str(rec.get("run_id") or "")
        agent = str(rec.get("champion_agent") or "")
        if not run_id or not agent:
            unresolved += 1
            continue
        if run_id not in outcome_cache:
            outcome_cache[run_id] = _step_outcomes(run_id)
        status = outcome_cache[run_id].get(agent)
        if status in _FAILED_STATUSES:
            champion_failed += 1
            slot["champion_failed"] += 1
        elif status:
            champion_ok += 1
        else:
            unresolved += 1
    result: Dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "window_days": days,
        "total": total,
        "diverged": diverged,
        "divergence_rate": round(diverged / total, 4) if total else 0.0,
        "potential_saves": champion_failed,
        "diverged_champion_ok": champion_ok,
        "unresolved": unresolved,
        "score_delta": {
            "avg": round(sum(deltas) / len(deltas), 2) if deltas else 0.0,
            "min": min(deltas) if deltas else 0.0,
            "max": max(deltas) if deltas else 0.0,
        } if deltas else {},
        "by_role": by_role,
        "challenger": strategy_config.challenger(),
        "adopt_history": strategy_config.adopt_history()[-5:],
    }
    try:
        atomic.write_json(_REPORT_FILE, result)
    except Exception:
        pass
    return result


def latest_report() -> Dict[str, Any]:
    try:
        data = json.loads(_REPORT_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def adopt() -> Dict[str, Any]:
    """采用挑战者：先算当前对账快照留档，再并权重（人工触发）。"""
    snapshot = report()
    summary = {"total": snapshot.get("total"),
               "diverged": snapshot.get("diverged"),
               "potential_saves": snapshot.get("potential_saves")}
    ok, err = strategy_config.adopt_challenger(summary=summary)
    return {"ok": bool(ok), "error": err, "adopted_with": summary}


__all__ = ["append_shadow", "read_shadow", "report", "latest_report", "adopt"]
