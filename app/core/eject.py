# -*- coding: utf-8 -*-
"""一键带走（eject，2026-11-01，借鉴 ruflo 的 `ruflo eject` 产品动作：
harness 可以换，数据永远是用户的）。

把一个任务的全部产物导出成**自包含目录 + zip**：manifest.json（脱敏任务档案）
+ runs.json（运行史）+ workdir/（产物全量副本，受上限纪律约束）+ 作品信息
与项目记忆。只读源、不删任何东西，导出目录整体拷走就能离开 CodeBee 读。

上限纪律（防止一个巨型 workdir 拖死导出）：
- 单文件 > _MAX_FILE 字节跳过并记 manifest.skipped；
- 总量 > _MAX_TOTAL 字节、文件数 > _MAX_FILES 即停止拷贝（如实记录截断）；
- .git / node_modules / __pycache__ / *.pyc 永不进包。

纪律：绝不抛错——导出失败返回 {"ok": False, "error": ...}，不连累任务本身；
manifest 里的疑似密钥字段一律打码（api_key/token/secret/password/cookie）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import zipfile
from pathlib import Path

from . import paths

_LOCK = threading.RLock()

_MAX_FILE = 32 * 1024 * 1024
_MAX_TOTAL = 256 * 1024 * 1024
_MAX_FILES = 5000
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
_SECRET_KEY = re.compile(r"(api_key|apikey|token|secret|password|cookie)",
                         re.IGNORECASE)


def _slug(s):
    t = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", str(s or "").strip(),
               flags=re.UNICODE)
    t = re.sub(r"-{2,}", "-", t).strip("-")
    return t[:40] or "task"


def _mask(v):
    """递归打码疑似密钥字段——导出包可能被转发，密钥绝不随包走。"""
    if isinstance(v, dict):
        out = {}
        for k, val in v.items():
            if _SECRET_KEY.search(str(k)) and val:
                out[k] = "***"
            else:
                out[k] = _mask(val)
        return out
    if isinstance(v, list):
        return [_mask(x) for x in v]
    return v


def _export_dir():
    return Path(paths.DATA_DIR) / "exports"


def _slim_run(r):
    keys = ("id", "status", "type", "title", "created_at", "ended_at",
            "chapter_scores")
    out = {k: r.get(k) for k in keys if r.get(k) is not None}
    err = str(r.get("error") or "").strip()
    if err:
        out["error"] = err[:400]
    return out


def _copy_workdir(src, dst, skipped, truncated):
    """带上限纪律的目录副本。返回 (files, bytes)。"""
    n_files = 0
    n_bytes = 0
    total = 0
    count = 0
    src_root = Path(src)
    for root, dirs, files in os.walk(str(src_root)):
        rel_root = Path(root).relative_to(src_root)
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        out_root = dst / rel_root
        if count > _MAX_FILES or total > _MAX_TOTAL:
            truncated.append(str(rel_root))
            dirs[:] = []
            continue
        try:
            out_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            truncated.append(str(rel_root))
            continue
        for fn in files:
            if fn.endswith(".pyc") or fn.endswith(".pyo"):
                continue
            count += 1
            if count > _MAX_FILES:
                truncated.append(str(rel_root / fn))
                return n_files, n_bytes
            s = Path(root) / fn
            try:
                sz = s.stat().st_size
            except OSError:
                continue
            if sz > _MAX_FILE:
                skipped.append({"file": str(rel_root / fn), "bytes": sz,
                                "reason": "超单文件上限"})
                continue
            if total + sz > _MAX_TOTAL:
                truncated.append(str(rel_root / fn))
                return n_files, n_bytes
            try:
                shutil.copy2(s, out_root / fn)
                total += sz
                n_bytes += sz
                n_files += 1
            except OSError:
                skipped.append({"file": str(rel_root / fn), "bytes": sz,
                                "reason": "读取失败"})
    return n_files, n_bytes


def export_task(task_id):
    """导出一个任务。返回
    {"ok", "task_id", "title", "dir", "zip", "zip_name", "files", "bytes",
     "runs", "skipped", "truncated", "workdir_missing"}。404 由调用方判。"""
    try:
        from . import store
        task = store.get_task(str(task_id or ""))
        if not task:
            return {"ok": False, "error": "任务不存在"}
        with _LOCK:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            name = "%s_%s" % (stamp, _slug(task.get("title") or task_id))
            out = _export_dir() / name
            out.mkdir(parents=True, exist_ok=True)
            files = 0
            total_bytes = 0
            skipped = []
            truncated = []
            wd_missing = False
            wd = str(task.get("workdir") or "").strip()
            if wd and Path(wd).is_dir():
                wf, wb = _copy_workdir(wd, out / "workdir", skipped, truncated)
                files += wf
                total_bytes += wb
            else:
                wd_missing = True
            runs = []
            try:
                for r in store.list_runs(limit=400):
                    if str(r.get("task_id") or "") == str(task_id):
                        runs.append(_slim_run(r))
            except Exception:
                pass
            books = []
            try:
                from .publish import ledger as pub_ledger
                books = pub_ledger.books_for(str(task_id)) or []
            except Exception:
                pass
            manifest = _mask({
                "schema": "codebee-eject/1",
                "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "task": task, "runs": runs, "books": books,
                "workdir_missing": wd_missing,
                "skipped": skipped[:200], "truncated": truncated[:200],
            })
            (out / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=1),
                encoding="utf-8")
            (out / "runs.json").write_text(
                json.dumps({"runs": runs}, ensure_ascii=False, indent=1),
                encoding="utf-8")
            zip_name = name + ".zip"
            zip_path = _export_dir() / zip_name
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
                for p in sorted(out.rglob("*")):
                    if p.is_file():
                        z.write(p, str(Path(name) / p.relative_to(out)))
            zbytes = zip_path.read_bytes()
            return {"ok": True, "task_id": str(task_id),
                    "title": str(task.get("title") or ""),
                    "dir": str(out), "zip": str(zip_path), "zip_name": zip_name,
                    "files": files, "bytes": total_bytes, "runs": len(runs),
                    "books": len(books),
                    "skipped": skipped[:200], "truncated": truncated[:200],
                    "workdir_missing": wd_missing}
    except Exception as exc:
        return {"ok": False, "error": "导出失败：%s" % (str(exc)[:200] or
                                                       exc.__class__.__name__)}


def export_zip_bytes(task_id):
    """导出并返回 (zip_name, bytes)；失败返回 None。供下载端点用。"""
    out = export_task(task_id)
    if not out.get("ok"):
        return None
    try:
        return out["zip_name"], Path(out["zip"]).read_bytes()
    except OSError:
        return None
