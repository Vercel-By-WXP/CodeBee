# -*- coding: utf-8 -*-
"""空间管家的 HTTP 契约层：GET 全只读，POST 两阶段确认，长操作后台跑。

这一层不碰文件系统本身，只做四件事：参数校验、调用底盘、把异常翻译成人话、
把耗时动作放进后台线程并记进度。安全判定一律下沉到 risk / cleaner / migrator，
**绝不因为前端传了某个字段就跳过复核**。

只读开关（``ledger.settings()["readonly"]``，默认 True）是最后一道保险：没在
界面上主动解锁，任何 confirm 动作都直接 403，连计划都不会被执行。

URL 前缀统一 ``/api/cspace/``，由 main.py 的 GET/POST 分发各插一处转发。
返回 ``(http_code, dict)``，dict 必须可直接 JSON 序列化。
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path

from . import analyzer, cleaner, ledger, links, migrator, risk, rules, scanner, winapi

_OPS = {}
_OPS_LOCK = threading.RLock()
_OPS_KEEP = 20
_PROGRESS_KEEP = 200

_BUSY = {
    "clean": "正在清理",
    "migrate": "正在搬家",
}


# ---------------------------------------------------------------- 工具

def _one(qs, key, default=""):
    v = qs.get(key) or []
    return str(v[0]) if v else default


def _paths_arg(raw):
    """接受 list 或逗号分隔字符串，逐项做绝对路径校验。空返 []。"""
    if isinstance(raw, str):
        raw = [x for x in raw.split(",") if x.strip()]
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        p = _safe_abs(item)
        if p:
            out.append(p)
    return out


def _safe_abs(value):
    """把外部传进来的路径规整成绝对路径；可疑输入一律返回空串。

    拒绝相对路径、``..`` 穿越、空白/NUL、以及非字符串。这里只做形状校验，
    真正的「能不能动」由 risk.assert_action_allowed 在执行前再判一次。
    """
    if not isinstance(value, str):
        try:
            value = str(value)
        except Exception:
            return ""
    p = value.strip().strip('"')
    if not p or "\x00" in p or ".." in Path(p).parts:
        return ""
    if not os.path.isabs(p):
        return ""
    return os.path.normpath(p)


def _positive_int(value, default, low, high):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def _readonly():
    try:
        return bool(ledger.settings().get("readonly", True))
    except Exception:
        return True


def _guard(fn):
    """统一异常边界：业务文案固定，技术原因只进 detail，不塞进 error。"""
    try:
        return 200, fn()
    except risk.UnsafePath as exc:
        return 400, {"error": "该路径被安全规则拒绝，没有执行任何操作",
                     "detail": str(exc)}
    except ValueError as exc:
        return 400, {"error": "请求参数不合法，没有执行任何操作",
                     "detail": str(exc)}
    except OSError as exc:
        return 409, {"error": "文件系统没能完成这个动作，已保留原状",
                     "detail": getattr(exc, "strerror", None) or type(exc).__name__}
    except Exception as exc:
        return 500, {"error": "操作失败，未改动文件；若已开始则已回滚",
                     "detail": "%s: %s" % (type(exc).__name__, exc)}


def _describe(exc):
    """后台线程里出错时给人话，不把堆栈字符串当业务结论。"""
    if isinstance(exc, risk.UnsafePath):
        return "该路径被安全规则拒绝，已停止", str(exc)
    if isinstance(exc, OSError):
        return "文件系统没能完成这个动作，已回滚", (getattr(exc, "strerror", None)
                                              or type(exc).__name__)
    return "操作失败，已回滚", "%s: %s" % (type(exc).__name__, exc)


# ---------------------------------------------------------------- 后台操作

def _start_op(kind, plan_key, fn, kwargs):
    """把长动作丢进守护线程，立即返回 op_id 供前端轮询。

    同一个 plan_key 有在跑的操作时拒绝重复提交——用户在界面上连点两下不该
    触发两次搬家。进度事件记在内存里，服务重启即丢（不落盘，避免产生第二份真相）。
    """
    key = str(plan_key or "")
    with _OPS_LOCK:
        for op in _OPS.values():
            if op["key"] == key and op["status"] == "running":
                return None, {"error": "这个计划正在执行，不要重复提交",
                              "op_id": op["op_id"]}
        op_id = uuid.uuid4().hex[:12]
        _OPS[op_id] = {"op_id": op_id, "kind": kind, "key": key,
                       "status": "running", "events": [], "result": None,
                       "error": "", "detail": "", "started_at": time.time()}
        if len(_OPS) > _OPS_KEEP:
            for old in sorted(_OPS.values(), key=lambda x: x["started_at"])[:-_OPS_KEEP]:
                if old["status"] != "running":
                    _OPS.pop(old["op_id"], None)

    def on_progress(event):
        with _OPS_LOCK:
            op = _OPS.get(op_id)
            if op and len(op["events"]) < _PROGRESS_KEEP:
                op["events"].append(dict(event or {}))

    def worker():
        try:
            result = fn(on_progress=on_progress, **kwargs)
            with _OPS_LOCK:
                op = _OPS.get(op_id)
                if op:
                    op["result"] = result
                    op["status"] = "done" if result.get("ok", True) else "failed"
                    if op["status"] == "failed":
                        op["error"] = str(result.get("error") or "操作未完成")
        except Exception as exc:
            msg, detail = _describe(exc)
            with _OPS_LOCK:
                op = _OPS.get(op_id)
                if op:
                    op["status"] = "failed"
                    op["error"] = msg
                    op["detail"] = detail
        finally:
            with _OPS_LOCK:
                op = _OPS.get(op_id)
                if op:
                    op["finished_at"] = time.time()
                    if op["status"] == "running":
                        op["status"] = "done"

    threading.Thread(target=worker, name="cspace-%s-%s" % (kind, op_id),
                     daemon=True).start()
    return op_id, {"started": True, "op_id": op_id,
                   "label": _BUSY.get(kind, kind)}


def _op_payload(op_id):
    with _OPS_LOCK:
        op = _OPS.get(str(op_id or ""))
        if not op:
            return {"status": "unknown", "events": [], "result": None}
        return {"status": op["status"], "op_id": op["op_id"],
                "kind": op["kind"], "events": list(op["events"][-_PROGRESS_KEEP:]),
                "result": op["result"], "error": op["error"],
                "detail": op["detail"],
                "elapsed": round(time.time() - op["started_at"], 1)}


# ---------------------------------------------------------------- GET

def _audit_rows(limit):
    path = ledger.store_dir() / "clean_history.jsonl"
    try:
        import json
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
        return [json.loads(x) for x in lines if x.strip()]
    except (OSError, ValueError):
        return []


def _get_status():
    cfg = ledger.settings()
    drv = winapi.list_drives()
    return {
        "platform": os.name,
        "elevated": winapi.is_elevated(),
        "readonly": bool(cfg.get("readonly", True)),
        "settings": cfg,
        "drives": drv,
        "rules": len(rules.load_rules()),
        "links": len(ledger.list_links()),
        "migrations": len(ledger.list_migrations()),
    }


def handle_get(path, qs):
    """GET 一律只读：只读盘点、只读透视、只读账本、只读进度。"""
    tail = path[len("/api/cspace/"):].strip("/")

    def run():
        if tail == "status":
            return _get_status()
        if tail == "rules":
            kind = _one(qs, "kind", "all")
            if kind not in ("all", "clean", "migrate"):
                raise ValueError("kind 只能是 all / clean / migrate")
            if _one(qs, "raw") == "1":
                items = rules.load_rules()
            else:
                items = rules.resolved_targets(kind=kind)
            return {"items": items, "total": len(items), "kind": kind}
        if tail == "candidates":
            kind = _one(qs, "kind", "clean")
            refresh = _one(qs, "refresh") == "1"
            fn = cleaner.candidates if kind == "clean" else migrator.candidates
            items = fn(refresh=refresh)
            return {"items": items, "total": len(items), "kind": kind,
                    "total_bytes": sum(int(x.get("size") or 0) for x in items),
                    "total_human": scanner.human(sum(int(x.get("size") or 0)
                                                     for x in items))}
        if tail == "scan":
            root = _safe_abs(_one(qs, "path"))
            if not root:
                raise ValueError("path 必须是绝对路径")
            if not os.path.isdir(root):
                raise ValueError("目录不存在或不是文件夹")
            items = scanner.scan_level(root)
            return {"path": root, "items": items, "total": len(items),
                    "readonly": True}
        if tail == "bigfiles":
            roots = _paths_arg(_one(qs, "paths"))
            if not roots:
                raise ValueError("paths 至少要给一个绝对路径")
            mb = _positive_int(_one(qs, "min_mb", "100"), 100, 1, 4096)
            items = analyzer.big_files(roots, min_bytes=mb * 1024 * 1024,
                                       limit=_positive_int(_one(qs, "limit", "200"),
                                                           200, 10, 1000))
            return {"items": items, "total": len(items), "min_mb": mb}
        if tail == "duplicates":
            roots = _paths_arg(_one(qs, "paths"))
            if not roots:
                raise ValueError("paths 至少要给一个绝对路径")
            mb = _positive_int(_one(qs, "min_mb", "10"), 10, 1, 4096)
            groups = analyzer.duplicate_groups(roots, min_bytes=mb * 1024 * 1024)
            return {"groups": groups, "total": len(groups),
                    "wasted": sum(int(x.get("wasted") or 0) for x in groups),
                    "wasted_human": scanner.human(
                        sum(int(x.get("wasted") or 0) for x in groups))}
        if tail == "links":
            rows = []
            for entry in ledger.list_links():
                row = dict(entry)
                try:
                    row.update(links.inspect(entry))
                except Exception:
                    row.update({"broken": None, "exists": None,
                                "target_exists": None})
                rows.append(row)
            return {"items": rows, "total": len(rows),
                    "broken": sum(1 for x in rows if x.get("broken"))}
        if tail == "migrations":
            rows = ledger.list_migrations()
            return {"items": rows, "total": len(rows)}
        if tail == "audit":
            limit = _positive_int(_one(qs, "limit", "50"), 50, 1, 500)
            rows = _audit_rows(limit)
            return {"items": rows, "total": len(rows)}
        if tail == "progress":
            return _op_payload(_one(qs, "op"))
        return {"error": "没有这个只读接口", "path": path}

    code, payload = _guard(run)
    if payload.get("error") and code == 200:
        return 404, payload
    return code, payload


# ---------------------------------------------------------------- POST

def _need_write(body):
    """所有写动作的统一步骤：确认只读开关 + 确认显式 confirm。"""
    if _readonly():
        return "当前是只读模式，请先在空间管家设置里解锁「允许执行」"
    if body.get("confirm") is not True:
        return "需要显式确认才会执行；当前只返回计划"
    return ""


def handle_post(path, body):
    """POST 默认 dry-run；只有 confirm 为 JSON 真布尔且已解锁才真动。"""
    body = body if isinstance(body, dict) else {}
    tail = path[len("/api/cspace/"):].strip("/")

    def run():
        if tail == "settings":
            cfg = ledger.settings()
            for key in ("readonly", "recycle", "default_target"):
                if key in body:
                    if key in ("readonly", "recycle") and not isinstance(body[key], bool):
                        raise ValueError("%s 必须是布尔值" % key)
                    cfg[key] = body[key]
            ledger.save_settings(cfg)
            return {"ok": True, "settings": cfg}

        if tail == "clean/plan":
            paths = _paths_arg(body.get("paths"))
            if not paths:
                raise ValueError("paths 至少要给一个绝对路径")
            return cleaner.plan(paths, recycle=body.get("recycle", True) is not False)

        if tail == "clean/run":
            plan_id = str(body.get("plan_id") or "").strip()
            if not plan_id:
                raise ValueError("plan_id 不能为空")
            if body.get("confirm") is not True:
                return cleaner.run(plan_id, confirm=False)
            blocked = _need_write(body)
            if blocked:
                return {"ok": False, "error": blocked}
            op_id, payload = _start_op("clean", plan_id, cleaner.run,
                                       {"plan_id": plan_id, "confirm": True})
            if op_id is None:
                return payload
            return payload

        if tail == "migrate/precheck":
            src = _safe_abs(body.get("src"))
            dst = _safe_abs(body.get("dst"))
            if not src or not dst:
                raise ValueError("src / dst 都必须是绝对路径")
            return migrator.precheck(src, dst)

        if tail == "migrate/run":
            src = _safe_abs(body.get("src"))
            dst = _safe_abs(body.get("dst"))
            if not src or not dst:
                raise ValueError("src / dst 都必须是绝对路径")
            if body.get("confirm") is not True:
                return {"dry_run": True, "precheck": migrator.precheck(src, dst)}
            blocked = _need_write(body)
            if blocked:
                return {"ok": False, "error": blocked}
            op_id, payload = _start_op("migrate", src + ">" + dst, migrator.run,
                                       {"src": src, "dst": dst, "confirm": True})
            if op_id is None:
                return payload
            return payload

        if tail in ("migrate/rollback", "migrate/restore"):
            src = _safe_abs(body.get("src"))
            dst = _safe_abs(body.get("dst")) if tail.endswith("rollback") else ""
            if not src or (tail.endswith("rollback") and not dst):
                raise ValueError("src（回滚还要 dst）必须是绝对路径")
            if body.get("confirm") is not True:
                return {"dry_run": True, "src": src, "dst": dst}
            blocked = _need_write(body)
            if blocked:
                return {"ok": False, "error": blocked}
            if tail.endswith("rollback"):
                return migrator.rollback(src, dst, confirm=True)
            return migrator.restore(src, confirm=True)

        if tail == "links/create":
            kind = str(body.get("kind") or "junction")
            link = _safe_abs(body.get("link"))
            target = _safe_abs(body.get("target"))
            if kind not in ("junction", "symlink", "hardlink") or not link or not target:
                raise ValueError("kind / link / target 不合法")
            if body.get("confirm") is not True:
                return {"dry_run": True, "kind": kind, "link": link, "target": target}
            blocked = _need_write(body)
            if blocked:
                return {"ok": False, "error": blocked}
            if kind == "junction":
                return links.create_junction(link, target, confirm=True)
            if kind == "symlink":
                return links.create_symlink(link, target, confirm=True)
            return links.create_hardlink(link, target, confirm=True)

        if tail == "links/remove":
            link = _safe_abs(body.get("path"))
            if not link:
                raise ValueError("path 必须是绝对路径")
            if body.get("confirm") is not True:
                return {"dry_run": True, "path": link}
            blocked = _need_write(body)
            if blocked:
                return {"ok": False, "error": blocked}
            return links.remove_link(link, confirm=True)

        if tail == "dedup/replace":
            keep = _safe_abs(body.get("keep"))
            dup = _safe_abs(body.get("duplicate"))
            if not keep or not dup:
                raise ValueError("keep / duplicate 都必须是绝对路径")
            if body.get("confirm") is not True:
                return {"dry_run": True, "keep": keep, "duplicate": dup}
            blocked = _need_write(body)
            if blocked:
                return {"ok": False, "error": blocked}
            return analyzer.replace_with_hardlink(keep, dup, confirm=True)

        return {"error": "没有这个接口", "path": path}

    code, payload = _guard(run)
    if payload.get("error") and code == 200 and tail not in (
            "clean/run", "migrate/run", "migrate/rollback", "migrate/restore",
            "links/create", "links/remove", "dedup/replace", "settings"):
        return 404, payload
    return code, payload
