# -*- coding: utf-8 -*-
"""调度事件台账：记录脱敏的候选、决策和结果，支持按运行回放。"""
from __future__ import annotations

import json
import threading
import time

from . import paths
from .redact import scrub_text

LOCK = threading.RLock()


def _dispatch_dir():
    # 读取动态 DATA_DIR，测试和多实例运行可在启动后重定向数据目录。
    return paths.DATA_DIR / "dispatch"


def _month_file(day):
    return _dispatch_dir() / ("dispatch-%s.jsonl" % day[:7].replace("-", ""))


def _text(value, limit=240):
    value = str(value or "").replace("\r", " ").replace("\n", " ")
    return scrub_text(value, limit=limit)


def _candidate(row):
    if not isinstance(row, dict):
        return {}
    return {"agent_id": _text(row.get("agent_id"), 64),
            "label": _text(row.get("label"), 80),
            "kind": _text(row.get("kind"), 32),
            "score": row.get("score", 0),
            "reason": _text(row.get("reason"), 240),
            "order": row.get("order", 0)}


def record_event(run_id="", task_id="", task_type="", difficulty="", role="",
                 phase="selected", selected="", participants=(), candidates=(),
                 fallback=(), selection_reason="", result="", verify_pass=None,
                 review_pass=None):
    """追加一条事件；仅保存路由元数据，不保存正文、提示词、密钥或文件内容。"""
    try:
        day = time.strftime("%Y-%m-%d")
        event = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "run_id": _text(run_id, 64), "task_id": _text(task_id, 64),
            "task_type": _text(task_type, 32), "difficulty": _text(difficulty, 16),
            "role": _text(role, 40), "phase": _text(phase, 16),
            "selected": _text(selected, 64),
            "participants": [_text(x.get("id") if isinstance(x, dict) else x, 64)
                             for x in (participants or ())],
            "candidates": [_candidate(x) for x in (candidates or ()) if isinstance(x, dict)],
            "fallback": [_text(x, 64) for x in (fallback or ())],
            "selection_reason": _text(selection_reason, 400),
            "result": _text(result, 32),
        }
        if verify_pass is not None:
            event["verify_pass"] = bool(verify_pass)
        if review_pass is not None:
            event["review_pass"] = bool(review_pass)
        with LOCK:
            _dispatch_dir().mkdir(parents=True, exist_ok=True)
            with open(_month_file(day), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event
    except Exception:
        return None


def _iter_events(run_id="", task_type="", limit=100):
    """从新到旧读取，满足 limit 即停，避免回放请求解析全部历史。"""
    try:
        directory = _dispatch_dir()
        files = sorted(directory.glob("dispatch-*.jsonl"), reverse=True) \
            if directory.is_dir() else []
    except Exception:
        return []
    events = []
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in reversed(lines):
                if not line.strip().startswith("{"):
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if not isinstance(item, dict):
                    continue
                if run_id and item.get("run_id") != run_id:
                    continue
                if task_type and item.get("task_type") != task_type:
                    continue
                events.append(item)
                if len(events) >= limit:
                    return events
        except Exception:
            continue
    return events


def replay(run_id="", task_type="", limit=100):
    """返回最新的脱敏调度事件，支持按 run_id/task_type 筛选。"""
    try:
        limit = max(1, min(1000, int(limit)))
    except (TypeError, ValueError):
        limit = 100
    rid, ttype = _text(run_id, 64), _text(task_type, 32)
    return _iter_events(rid, ttype, limit)
