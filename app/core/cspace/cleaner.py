# -*- coding: utf-8 -*-
"""安全清理：先出计划 → 用户确认 → 复核 → 回收站/归档 → 逐条审计。

三条硬规矩：

* ``plan()`` 只认规则库解析出来的白名单路径，前端硬塞一个路径会被记进
  ``refused``，绝不进执行清单；
* ``run(confirm=False)`` 只回显计划，一个字节都不动；
* 执行时优先送系统回收站（可还原），回收站失败就同盘 ``shutil.move``
  归档到数据目录的 trash 批次里。**整个模块没有 rmtree / unlink 用户文件**。

规则里的 ``cleanTarget: "contents"`` 表示「清这个目录的内容、保留目录本身」，
直接把整个目录搬走会破坏软件预期的目录结构，所以执行前会展开成子项。
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from . import ledger, risk, rules, scanner, winapi


_CACHE = {"ts": 0.0, "rows": None}
_CACHE_TTL = 60.0


def candidates(env=None, refresh=False) -> list[dict]:
    """规则库里可清且真实存在的目标，补体积与风险判定；红线目标直接不进清单。

    全量盘点点会在缓存机器上走几十秒（实测 40s），所以进程内缓存 60 秒：
    前端「清理」页拉完候选再点「生成计划」不该重扫一遍。用户主动点
    「重新盘点」时传 refresh=True 强制重扫；env 注入（测试）不进缓存。
    """
    now = time.monotonic()
    if (not refresh and env is None and _CACHE["rows"] is not None
            and now - _CACHE["ts"] < _CACHE_TTL):
        return [dict(x) for x in _CACHE["rows"]]
    out = []
    for row in rules.resolved_targets(kind="clean", env=env):
        path = row.get("path")
        if not path:
            continue
        try:
            risk.assert_action_allowed(path, "clean")
        except risk.UnsafePath:
            continue
        try:
            size, files = scanner.calc_size(path)
        except OSError:
            continue
        judged = risk.classify(row)
        item = dict(row)
        item.update({"size": int(size), "files": int(files),
                     "size_human": scanner.human(size),
                     "level": judged.get("level"),
                     "label": judged.get("label"),
                     "reason": judged.get("reason", "")})
        out.append(item)
    out.sort(key=lambda x: -x["size"])
    if env is None:
        _CACHE["rows"] = [dict(x) for x in out]
        _CACHE["ts"] = time.monotonic()
    return out


def plan(paths, recycle=True, base=None, allowed=None) -> dict:
    """生成两阶段计划。只认白名单；此函数绝不写任何用户目录。"""
    rows = candidates() if allowed is None else allowed
    whitelist = {os.path.normcase(os.path.normpath(x["path"])): x
                 for x in rows if x.get("path")}
    items, refused = [], []
    for raw in paths or []:
        key = os.path.normcase(os.path.normpath(str(raw)))
        item = whitelist.get(key)
        if not item:
            refused.append({"path": str(raw), "reason": "不在规则白名单或已被安全红线排除"})
            continue
        items.append({"path": item["path"], "rule_id": item.get("rule_id"),
                      "name": item.get("name"), "risk": item.get("risk"),
                      "size": item["size"], "size_human": item["size_human"],
                      "files": item["files"],
                      "clean_target": item.get("clean_target"),
                      "action": "recycle" if recycle else "archive"})
    plan_id = ledger.new_id("clean")
    total = sum(int(x["size"]) for x in items)
    result = {"plan_id": plan_id, "recycle": bool(recycle),
              "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "items": items, "refused": refused,
              "total_bytes": total, "total_human": scanner.human(total),
              "risk_summary": {
                  level: sum(1 for x in items if x.get("risk") == level)
                  for level in ("safe", "caution", "danger")}}
    ledger.put_plan(plan_id, result, base=base)
    return result


def _children(path):
    try:
        return [entry.path for entry in os.scandir(path)]
    except OSError:
        return []


def _archive(path, batch, base, index):
    """回收站不可用时的兜底：同盘改名进数据目录的 trash 批次，不删除。"""
    root = ledger.store_dir(base) / "trash" / batch
    root.mkdir(parents=True, exist_ok=True)
    name = "%03d-%s" % (index, os.path.basename(str(path)) or "item")
    dst = root / name
    try:
        shutil.move(str(path), str(dst))
        return {"ok": True, "action": "archive", "to": str(dst)}
    except OSError as exc:
        return {"ok": False, "action": "archive",
                "error": getattr(exc, "strerror", None) or type(exc).__name__}


def run(plan_id, confirm=False, on_progress=None, base=None) -> dict:
    """执行计划。confirm 不是真布尔时只回显；确认后逐条复核、跳过占用、记审计。"""
    saved = ledger.get_plan(plan_id, base=base)
    if not isinstance(saved, dict):
        return {"ok": False, "error": "计划不存在或已过期，请重新生成"}
    if confirm is not True:
        return {"dry_run": True, "plan": saved}

    items = saved.get("items") or []
    batch = time.strftime("%Y%m%d-%H%M%S")
    moved, skipped, refused, errors = [], [], [], []
    freed = 0
    use_recycle = saved.get("recycle", True) is not False

    for index, item in enumerate(items, 1):
        path = item.get("path") or ""
        try:
            risk.assert_action_allowed(path, "clean")
        except (risk.UnsafePath, ValueError) as exc:
            refused.append({"path": path, "reason": str(exc)})
            continue
        targets = (_children(path) if item.get("clean_target") == "contents"
                   else [path])
        if not targets:
            skipped.append({"path": path, "reason": "目录为空，无需清理"})
        for target in targets:
            lock = winapi.probe_lock(target)
            if lock.get("locked"):
                skipped.append({"path": target, "reason": lock.get("reason") or "被占用"})
                continue
            try:
                size, _files = scanner.calc_size(target)
            except OSError:
                size = 0
            done = None
            if use_recycle and winapi.recycle(target):
                done = {"ok": True, "action": "recycle"}
            else:
                done = _archive(target, batch, base, index)
            if done.get("ok"):
                moved.append({"path": target, "action": done["action"],
                              "to": done.get("to", "")})
                freed += size
                ledger.append_audit({"action": "clean", "path": target,
                                     "mode": done["action"], "bytes": size,
                                     "batch": batch}, base=base)
            else:
                errors.append({"path": target, "reason": done.get("error") or "未能移走"})
        if on_progress:
            try:
                on_progress({"phase": "clean", "current": path,
                             "done": index, "total": len(items),
                             "freed": freed})
            except Exception:
                pass

    ledger.drop_plan(plan_id, base=base)
    return {"ok": not errors, "moved": moved, "skipped": skipped,
            "refused": refused, "errors": errors, "freed": freed,
            "freed_human": scanner.human(freed), "batch": batch,
            "recycle": use_recycle,
            "audit": "clean_history.jsonl"}
