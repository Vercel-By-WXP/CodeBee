# -*- coding: utf-8 -*-
"""记忆 MD vault 化（2026-11-01，借鉴 Obsidian 的本地优先 vault 哲学）。

把项目记忆（workdir/.codebee/project-memory.jsonl 的版本化日志）导出成
**一份 Obsidian 风格的 MD 库**：一个条目一个文件、frontmatter 带身份与版本、
目录即数据库——用户可以用任何编辑器手改、可以 git、可以外携。

双向纪律：
- export：vault 目录整体重写（幂等覆盖），源永远是 jsonl 日志；
- sync_back：手改的 MD 通过**追加版本行**回流日志（content+新 sha+version+1，
  与 project_memory._decide 同款模式），绝不改写历史行；
  frontmatter 的 version 落后于日志 = 冲突，如实报 conflict 不回流——
  「拿旧文件盖新版本」比不回流更糟（同 attn-guard 纪律）。

vault 落点：data/memory-vault/<项目slug>/<条目id>.md + _index.md。
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from . import paths

_LOCK = threading.RLock()
_FM_KEY = re.compile(r"^([A-Za-z_][\w\-]*)\s*:\s*(.*)$")


def vault_dir():
    return Path(paths.DATA_DIR) / "memory-vault"


def _slug(s):
    t = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", str(s or "").strip(), flags=re.UNICODE)
    t = re.sub(r"-{2,}", "-", t).strip("-")
    return t or "untitled"


def _fm_value(v):
    if isinstance(v, (int, float)):
        return v
    return str(v)


def _write_fm(path, meta, body):
    lines = ["---"]
    for k, v in meta.items():
        lines.append("%s: %s" % (k, _fm_value(v)))
    lines.append("---")
    lines.append("")
    lines.append(str(body or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _read_fm(path):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None, ""
    text = text.replace("\r\n", "\n").lstrip("\ufeff")
    if not text.startswith("---"):
        return None, text
    lines = text.split("\n")
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return None, text
    meta, body = {}, "\n".join(lines[end + 1:]).lstrip("\n")
    for line in lines[1:end]:
        m = _FM_KEY.match(line.strip())
        if m:
            meta[m.group(1)] = m.group(2)
    return meta, body


def _tasks_with_memory():
    """有项目记忆的任务清单（含已归档）。"""
    from . import store, project_memory as pm
    out = []
    for t in store.list_tasks(archived=None) or []:
        wd = str(t.get("workdir") or "").strip()
        if not wd:
            continue
        try:
            entries = pm.list_entries(wd)
        except Exception:
            continue
        if entries:
            out.append((t, wd, entries))
    return out


def export_vault():
    """导出/刷新整个 vault。返回统计 {projects, entries, files, dir}。"""
    with _LOCK:
        root = vault_dir()
        n_files = 0
        n_entries = 0
        projects = []
        for t, wd, entries in _tasks_with_memory():
            pslug = _slug(t.get("title") or t.get("id") or "project")
            pdir = root / pslug
            index_rows = []
            for e in entries:
                if str(e.get("status") or "") in ("rejected",):
                    continue   # 拒绝条目不进 vault（那是诊断不是知识）
                fname = "%s.md" % _slug(e.get("id") or "entry")
                meta = {
                    "id": e.get("id") or "", "title": e.get("title") or "",
                    "project": pslug, "task_id": t.get("id") or "",
                    "status": e.get("status") or "", "version": e.get("version") or 1,
                    "updated_at": e.get("updated_at") or 0,
                    "source": "codebee-vault",
                }
                _write_fm(pdir / fname, meta, e.get("content") or "")
                n_files += 1
                n_entries += 1
                index_rows.append("- [%s](%s) — %s（%s，v%d）"
                                  % (e.get("title") or e.get("id"),
                                     fname, (e.get("content") or "")[:60].replace("\n", " "),
                                     e.get("status"), e.get("version") or 1))
            _write_fm(pdir / "_index.md",
                      {"project": pslug, "task_id": t.get("id") or "",
                       "entries": len(index_rows), "source": "codebee-vault"},
                      "\n".join(["# %s" % (t.get("title") or pslug), ""]
                                + index_rows))
            n_files += 1
            projects.append({"project": pslug, "task_id": t.get("id") or "",
                             "entries": len(index_rows)})
        return {"dir": str(root), "projects": projects, "files": n_files,
                "entries": n_entries,
                "exported_at": time.strftime("%Y-%m-%d %H:%M:%S")}


def sync_back(dry_run=False):
    """把手改的 MD 回流记忆日志。

    返回 {"updated": [...], "conflicts": [...], "unchanged": n, "skipped": n}。
    - 文件正文 != 日志当前内容 → 追加 version+1 的新行（append-only）；
    - frontmatter version 落后日志当前版本 → conflict（防旧盖新）；
    - 条目已不存在/任务没了 → skipped。"""
    from . import store, project_memory as pm
    root = vault_dir()
    res = {"updated": [], "conflicts": [], "unchanged": 0, "skipped": 0}
    if not root.is_dir():
        return res
    for f in sorted(root.glob("*/*.md")):
        if f.name == "_index.md":
            continue
        meta, body = _read_fm(f)
        if not isinstance(meta, dict) or not meta.get("id") or not meta.get("task_id"):
            res["skipped"] += 1
            continue
        eid = str(meta["id"])
        tid = str(meta["task_id"])
        try:
            fv = int(float(meta.get("version") or 1))
        except (TypeError, ValueError):
            fv = 1
        task = store.get_task(tid)
        wd = str((task or {}).get("workdir") or "").strip()
        if not wd:
            res["skipped"] += 1
            continue
        try:
            row = next((e for e in pm.list_entries(wd) if e.get("id") == eid), None)
        except Exception:
            row = None
        if not row:
            res["skipped"] += 1
            continue
        cur = row.get("content") or ""
        new_body = body.strip("\n")
        if new_body == cur.strip("\n"):
            res["unchanged"] += 1
            continue
        jv = int(row.get("version") or 1)
        if fv != jv:
            res["conflicts"].append({"file": str(f), "id": eid,
                                     "file_version": fv, "journal_version": jv,
                                     "reason": "MD 版本落后于日志（先重新导出再改）"})
            continue
        if dry_run:
            res["updated"].append({"file": str(f), "id": eid,
                                   "task_id": tid, "would_update": True})
            continue
        stamp = time.time()
        digest = pm._content_digest(new_body)
        if digest is None:
            res["skipped"] += 1
            continue
        new_row = dict(row, content=new_body, content_sha256=digest,
                       version=jv + 1, updated_at=stamp,
                       vault_edited=1)
        try:
            with pm._LOCK:
                pm._append(pm._file(wd), new_row)
            res["updated"].append({"file": str(f), "id": eid, "task_id": tid,
                                   "version": jv + 1})
        except (OSError, ValueError):
            res["skipped"] += 1
    return res
