# -*- coding: utf-8 -*-
"""AI 磁盘清理：扫描 → 判定 → 人勾选 → 先归档再清（2026-11-01，
借鉴 Purrge「空间不足时提醒，AI 逐项判断能清什么，你勾选确认后才清理」）。

CodeBee 的磁盘只增不减：run 步骤日志、用量/审计 jsonl、错误台账、临时 workdir。
用户不该需要知道哪些能删——这就是傻瓜化在运维侧的落点。

三段式（与 Purrge 对齐）：
  scan()   —— 只读盘点：数据目录各子目录的大小/文件数/最近写入/可清性判定；
  judge()  —— AI 逐项判定（可选）：把盘点喂给编排者模型，只许标
               「cleanable / keep / ask」，标不了就回落启发式（never 不可清）；
  clean()  —— 用户勾选后才执行：**移动到 data/trash/<时间戳>/**（同盘 rename，
              秒级、可撤销），不是删除。restore() 从回收站整体搬回。

安全红线：
- 白名单之外的一个字节都不动（clean 只接受 scan() 发出的 path）；
- DATA_DIR 之外的目标直接拒绝；tasks/ 与 models.json 等活数据永不进清理清单；
- 绝不直接 unlink——只归档；回收站里躺多久由用户自己决定。
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

from . import paths

_LOCK = threading.RLock()

# 永不可清：活数据（任务、模型配置、编排配置、发布台账、作品信息）
_NEVER = {"tasks", "profiles", "publish", "agents", "flows", "books"}
# 可清候选（按风险分级）：logs=纯日志可清 / review=审计与台账（建议保留近期）
_CANDIDATES = {
    "runs": "logs",          # 运行记录与步骤日志（终态后只剩排查价值）
    "usage": "review",       # 用量台账（统计数据源，清了趋势图断史）
    "errors": "review",      # 错误台账
    "audit": "review",       # Jev/审计 jsonl（哈希链）
    "memory-summary": "logs",  # 总结缓存（可再生）
    "workspace": "review",   # 内置默认工作目录（可能有用户文件，必须人确认）
    "tmp": "logs",
}


def _dir_size(p):
    total = 0
    files = 0
    latest = 0.0
    try:
        for f in p.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
                    files += 1
                    latest = max(latest, f.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        pass
    return total, files, latest


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0
    return "%.1f GB" % n


def scan():
    """只读盘点。返回 {"items": [...], "total_bytes": n, "generated_at": ...}
    每项：{"path"(DATA_DIR 相对名), "kind", "size", "size_human", "files",
    "last_write", "heuristic": "cleanable"|"review"|"never"}"""
    items = []
    total = 0
    data = Path(paths.DATA_DIR)
    try:
        entries = sorted(x for x in data.iterdir() if x.is_dir())
    except OSError:
        entries = []
    for d in entries:
        name = d.name
        if name in _NEVER or name == "trash":
            continue
        kind = _CANDIDATES.get(name)
        size, files, latest = _dir_size(d)
        total += size
        if kind == "logs":
            heuristic = "cleanable" if size > 0 else "skip"
        elif kind == "review":
            heuristic = "review" if size > 0 else "skip"
        else:
            heuristic = "never"
        items.append({"path": name, "kind": kind or "unknown",
                      "size": size, "size_human": _human(size),
                      "files": files,
                      "last_write": (time.strftime("%m-%d %H:%M", time.localtime(latest))
                                     if latest else ""),
                      "last_write_epoch": latest,
                      "heuristic": heuristic})
    items.sort(key=lambda x: -x["size"])
    return {"items": items, "total_bytes": total,
            "total_human": _human(total),
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}


def judge(items=None, timeout=60):
    """AI 逐项判定（可选步骤）：scan() 结果喂编排者，只许三选一。
    无模型/失败/响应不合规 → 每项回落启发式，绝不编造。"""
    scan_res = scan() if items is None else {"items": items}
    rows = [{"path": it["path"], "kind": it["kind"], "size_human": it["size_human"],
             "files": it["files"], "last_write": it["last_write"],
             "heuristic": it["heuristic"]} for it in scan_res.get("items") or []]
    verdicts = {}
    try:
        from . import modelhub
        orch = modelhub.resolve_orchestrator()
        if orch:
            prov, model = orch
            prompt = (
                "你是 CodeBee 的磁盘清理判定助手。下面是数据目录各子目录的盘点。"
                "对每一项只输出一个 JSON 数组，元素形如 "
                '{"path":"...","verdict":"cleanable|keep|ask","reason":"一句话中文理由"}。'
                "规则：审计/台账类（audit/usage/errors）默认 keep；纯日志（runs 的"
                "旧步骤日志、缓存）可 cleanable；含用户文件的（workspace）必须 ask；"
                "不确定一律 ask。不输出数组以外的任何内容。\n\n"
                + json.dumps(rows, ensure_ascii=False))
            res = modelhub.chat(prov.get("id"), model, prompt,
                                max_tokens=1200, timeout=timeout)
            text = (res.get("text") or "") if res.get("ok") else ""
            start, end = text.find("["), text.rfind("]")
            if start >= 0 and end > start:
                for v in json.loads(text[start:end + 1]):
                    if isinstance(v, dict) and v.get("path") \
                            and v.get("verdict") in ("cleanable", "keep", "ask"):
                        verdicts[str(v["path"])] = {
                            "verdict": v["verdict"],
                            "reason": str(v.get("reason") or "")[:200]}
    except Exception:
        verdicts = {}
    for it in scan_res.get("items") or []:
        v = verdicts.get(it["path"])
        if v:
            it["ai_verdict"] = v["verdict"]
            it["ai_reason"] = v["reason"]
        else:
            it["ai_verdict"] = it["heuristic"]
            it["ai_reason"] = ""
    return scan_res


def _trash_root():
    return Path(paths.DATA_DIR) / "trash"


def trash_list():
    """回收站内容：{"entries": [{name, items, size_human, when}]}"""
    out = []
    root = _trash_root()
    try:
        for d in sorted(root.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            size, files, _ = _dir_size(d)
            out.append({"name": d.name, "items": files,
                        "size": size, "size_human": _human(size),
                        "when": d.name})
    except OSError:
        pass
    return {"entries": out, "root": str(root)}


def clean(selected, now=None):
    """把用户勾选的子目录移动进回收站。selected = scan() 里的 path 名列表。

    返回 {"moved": [...], "refused": [{"path", "reason"}], "trash": 批次名}。
    白名单之外的 path 直接拒绝；回铁律：只 rename（同盘）不删除。"""
    batch = time.strftime("%Y%m%d-%H%M%S") if now is None else str(now)
    moved, refused = [], []
    # 白名单只收「可清候选」两档：AI 判定与启发式之外的未知目录一律拒收
    allowed = {it["path"] for it in scan()["items"]
               if it.get("kind") in ("logs", "review")}
    root = _trash_root() / batch
    data = Path(paths.DATA_DIR)
    for name in selected or []:
        name = str(name or "").strip().strip("/\\")
        src = (data / name).resolve()
        try:
            inside = src == data.resolve() or data.resolve() in src.parents
        except OSError:
            inside = False
        if not inside:
            # 路径穿越/外部目录：先于一切判定拒收
            refused.append({"path": name, "reason": "不在可清理白名单"})
            continue
        if not src.is_dir():
            refused.append({"path": name, "reason": "目录不存在"})
            continue
        if name not in allowed or name in _NEVER:
            refused.append({"path": name, "reason": "不在可清理白名单"})
            continue
        with _LOCK:
            try:
                root.mkdir(parents=True, exist_ok=True)
                dst = root / name
                if dst.exists():
                    refused.append({"path": name, "reason": "回收站批次已含同名目录"})
                    continue
                shutil.move(str(src), str(dst))
                moved.append(name)
            except OSError as exc:
                refused.append({"path": name, "reason": str(exc)[:120]})
    return {"moved": moved, "refused": refused, "trash": batch}


def restore(batch):
    """把回收站批次整体搬回原位（目录名 = 数据目录子目录名）。"""
    root = _trash_root() / str(batch or "").strip().strip("/\\")
    data = Path(paths.DATA_DIR)
    if not root.is_dir() or data.resolve() not in root.resolve().parents:
        raise ValueError("回收站批次不存在")
    restored, refused = [], []
    for d in sorted(root.iterdir()):
        dst = data / d.name
        try:
            if dst.exists():
                refused.append({"path": d.name, "reason": "目标已存在同名目录"})
                continue
            shutil.move(str(d), str(dst))
            restored.append(d.name)
        except OSError as exc:
            refused.append({"path": d.name, "reason": str(exc)[:120]})
    if not refused and root.is_dir():
        try:
            root.rmdir()
        except OSError:
            pass
    return {"restored": restored, "refused": refused}
