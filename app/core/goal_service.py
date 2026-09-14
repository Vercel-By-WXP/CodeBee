# -*- coding: utf-8 -*-
"""Goal 状态外置：跨轮目标（title/描述/phase/轮次）独立于 LLM 调用持久化。

设计稿：docs/migration/03-state-externalization.md §2A。
参考 dsh packages/goal/goal/：状态 100% 来自事件溯源（goal/change 事件），
唯一当前 goal，revision 走 CAS 拒绝陈旧引用。

与 planner.py 的关系：planner 继续负责「LLM 拆解」；GoalService 只管
「目标状态」——谁都可以读（UI/续行驱动器/流水线），变更必须过 CAS。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

_LOCK = threading.RLock()
# goal_id -> dict（内存缓存；磁盘为唯一事实来源）
_GOALS = {}
_FILE = None  # 由 init() 注入


def init(data_dir=None):
    """注入存储路径并从磁盘加载。测试可传临时目录。"""
    global _FILE, _GOALS
    with _LOCK:
        base = Path(data_dir) if data_dir else Path(_default_dir())
        base.mkdir(parents=True, exist_ok=True)
        _FILE = base / "goals.json"
        _GOALS = {}
        try:
            data = json.loads(_FILE.read_text(encoding="utf-8"))
            for g in data.get("goals") or []:
                if isinstance(g, dict) and g.get("goal_id"):
                    _GOALS[g["goal_id"]] = g
        except (OSError, json.JSONDecodeError):
            pass


def _default_dir():
    from . import paths
    return str(paths.DATA_DIR)


def _persist():
    """原子写盘（tmp + replace）。调用方必须已持有 _LOCK。"""
    if _FILE is None:
        return
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"goals": list(_GOALS.values())},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_FILE)


class StaleRevisionError(Exception):
    """CAS 失败：调用方持有的是陈旧 revision。"""


class GoalExistsError(Exception):
    """已有未完结的当前 goal，需先 complete/abandon。"""


class GoalNotFoundError(Exception):
    pass


def _valid_phase(p):
    return p in ("active", "paused", "complete", "abandoned")


def current():
    """返回唯一「当前」goal（未 complete/abandoned 的最新一个）；无则 None。"""
    with _LOCK:
        open_goals = [g for g in _GOALS.values()
                      if g.get("phase") in ("active", "paused")]
        if not open_goals:
            return None
        return max(open_goals, key=lambda g: g.get("created_at", 0))


def get(goal_id: str):
    with _LOCK:
        g = _GOALS.get(goal_id)
        return dict(g) if g else None


def create(title: str, description: str = "", *, rounds_max: int = 5,
           metadata: dict | None = None) -> dict:
    """创建新 goal。已有未完结 goal 时拒绝（单一当前目标，仿 dsh）。"""
    with _LOCK:
        cur = current()
        if cur is not None:
            raise GoalExistsError(
                "已有进行中的 goal %s（%s），请先 complete/abandon"
                % (cur["goal_id"], cur["title"]))
        goal = {
            "goal_id": "g" + uuid.uuid4().hex[:12],
            "title": (title or "").strip(),
            "description": (description or "").strip(),
            "phase": "active",
            "revision": 1,
            "rounds_started": 0,
            "rounds_max": max(1, int(rounds_max)),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "metadata": dict(metadata or {}),
        }
        _GOALS[goal["goal_id"]] = goal
        _persist()
        return dict(goal)


def update(goal_id: str, expected_revision: int, **changes) -> dict:
    """CAS 更新：expected_revision 不匹配即抛 StaleRevisionError。

    允许变更的字段：title/description/phase/rounds_max/metadata。
    phase 只允许合法值；complete/abandoned 为终态不可再改回。
    """
    with _LOCK:
        g = _GOALS.get(goal_id)
        if not g:
            raise GoalNotFoundError(goal_id)
        if g["revision"] != expected_revision:
            raise StaleRevisionError(
                "%s 期望 rev %d，实际 %d" % (goal_id, expected_revision, g["revision"]))
        if g["phase"] in ("complete", "abandoned"):
            raise ValueError("goal 已终态（%s），不可再变更" % g["phase"])
        allowed = {"title", "description", "phase", "rounds_max",
                   "rounds_started", "metadata"}
        for k, v in changes.items():
            if k not in allowed:
                raise ValueError("不可变更字段：%s" % k)
            if k == "phase" and not _valid_phase(v):
                raise ValueError("非法 phase：%s" % v)
            if k == "rounds_max":
                v = max(1, int(v))
            g[k] = v
        g["revision"] += 1
        g["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _persist()
        return dict(g)


def increment_rounds(goal_id: str, expected_revision: int) -> dict:
    """轮次 +1（Goal Round Driver 专用 CAS 入口）。"""
    with _LOCK:
        g = _GOALS.get(goal_id)
        if not g:
            raise GoalNotFoundError(goal_id)
        return update(goal_id, expected_revision, rounds_started=g["rounds_started"] + 1)


def list_goals(limit: int = 50) -> list:
    with _LOCK:
        goals = sorted(_GOALS.values(),
                       key=lambda g: g.get("created_at", ""), reverse=True)
        return [dict(g) for g in goals[:limit]]