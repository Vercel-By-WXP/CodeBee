# -*- coding: utf-8 -*-
"""账本与两阶段计划：链接、迁移、审计、设置，全部落在数据目录里。

账本文件是**数据不是代码**：坏 JSON 一律当空结构处理，绝不因为一处手写坏了
就让整个设置页打不开；但写回去时用「临时文件 + os.replace」保证不会写坏。

两阶段计划（plan_id → 执行）是本包最重要的安全装置：计划里有创建时间，
超过 30 分钟自动失效；执行完立刻删掉，前端就算重放同一个 id 也动不了文件。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from .. import paths

LEDGER_NAME = "ledger.json"
SETTINGS_NAME = "settings.json"
AUDIT_NAME = "clean_history.jsonl"
PLANS_NAME = "plans.json"
_PLAN_TTL = 30 * 60
_PLAN_KEEP = 50

DEFAULT_SETTINGS = {"readonly": True, "recycle": True, "default_target": ""}


def store_dir(base=None) -> Path:
    """数据落点；base 供测试注入临时目录，避免碰到真实用户数据。"""
    if base:
        return Path(base)
    return Path(paths.DATA_DIR) / "cspace"


def _read(name, default, base=None):
    path = store_dir(base) / name
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default() if callable(default) else default
    return data if isinstance(data, type(default() if callable(default) else default)) else (
        default() if callable(default) else default)


def _write(name, data, base=None):
    root = store_dir(base)
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / (name + ".tmp")
    final = root / name
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(str(tmp), str(final))


def load(base=None) -> dict:
    data = _read(LEDGER_NAME, lambda: {"links": [], "migrations": []}, base)
    if not isinstance(data, dict):
        return {"links": [], "migrations": []}
    data.setdefault("links", [])
    data.setdefault("migrations", [])
    if not isinstance(data["links"], list):
        data["links"] = []
    if not isinstance(data["migrations"], list):
        data["migrations"] = []
    return data


def save(data, base=None) -> None:
    _write(LEDGER_NAME, data or {"links": [], "migrations": []}, base)


def settings(base=None) -> dict:
    data = _read(SETTINGS_NAME, lambda: dict(DEFAULT_SETTINGS), base)
    out = dict(DEFAULT_SETTINGS)
    if isinstance(data, dict):
        out.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    # 只读开关必须是真布尔，脏值一律回落到最保守的 True
    if not isinstance(out.get("readonly"), bool):
        out["readonly"] = True
    return out


def save_settings(cfg, base=None) -> dict:
    cur = settings(base)
    for key in DEFAULT_SETTINGS:
        if key in (cfg or {}):
            cur[key] = cfg[key]
    _write(SETTINGS_NAME, cur, base)
    return cur


def list_links(base=None) -> list[dict]:
    return [x for x in load(base)["links"] if isinstance(x, dict)]


def list_migrations(base=None) -> list[dict]:
    return [x for x in load(base)["migrations"] if isinstance(x, dict)]


def add_link(entry, base=None) -> None:
    data = load(base)
    source = (entry or {}).get("source")
    data["links"] = [x for x in data["links"] if x.get("source") != source]
    data["links"].append(dict(entry))
    save(data, base)


def remove_link(source, base=None) -> None:
    data = load(base)
    data["links"] = [x for x in data["links"] if x.get("source") != source]
    save(data, base)


def add_migration(entry, base=None) -> None:
    data = load(base)
    source = (entry or {}).get("source")
    data["migrations"] = [x for x in data["migrations"]
                          if x.get("source") != source]
    data["migrations"].append(dict(entry))
    save(data, base)


def update_migration(source, patch, base=None) -> None:
    data = load(base)
    for row in data["migrations"]:
        if row.get("source") == source:
            row.update(patch or {})
            row["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save(data, base)


def drop_migration(source, base=None) -> None:
    data = load(base)
    data["migrations"] = [x for x in data["migrations"]
                          if x.get("source") != source]
    save(data, base)


def append_audit(row, base=None) -> None:
    root = store_dir(base)
    root.mkdir(parents=True, exist_ok=True)
    payload = dict(row or {})
    payload.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
    with (root / AUDIT_NAME).open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _plans(base=None):
    data = _read(PLANS_NAME, dict, base)
    return data if isinstance(data, dict) else {}


def _save_plans(data, base=None):
    _write(PLANS_NAME, data, base)


def put_plan(plan_id, plan, base=None) -> None:
    plans = _plans(base)
    row = dict(plan or {})
    row["created_at"] = time.time()
    plans[str(plan_id)] = row
    if len(plans) > _PLAN_KEEP:
        for key in sorted(plans, key=lambda k: plans[k].get("created_at", 0))[
                :len(plans) - _PLAN_KEEP]:
            plans.pop(key, None)
    _save_plans(plans, base)


def get_plan(plan_id, base=None):
    row = _plans(base).get(str(plan_id or ""))
    if not isinstance(row, dict):
        return None
    if time.time() - float(row.get("created_at") or 0) > _PLAN_TTL:
        drop_plan(plan_id, base)
        return None
    return row


def drop_plan(plan_id, base=None) -> None:
    plans = _plans(base)
    if plans.pop(str(plan_id or ""), None) is not None:
        _save_plans(plans, base)


def new_id(prefix="cs"):
    return "%s-%s" % (prefix, uuid.uuid4().hex[:12])
