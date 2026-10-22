# -*- coding: utf-8 -*-
"""CLI 配置写盘台账：stash 可还原 + applied 审计（magpie 纪律借鉴）。

magpie（yetone/magpie，2026-10-22 调研）对宿主 agent 配置文件的写入纪律：
stash.json 记被替换的旧值（切回即还原）、applied.json 记每个目标最后被写成
什么/何时、还原前校验「文件没被别人动过」才做字节级还原。CodeBee 侧的落法：

- 记录点在 manager._atomic_write_bytes 这个唯一写盘咽喉，整文件字节级快照
  （键=解析后路径），stash 每路径保留最近 3 笔；
- restore(path) 只在「当前内容仍是我们最后一次写入的」时动手——文件被
  用户/其他工具改过就拒绝盲目还原（字节级还原语义，不是 merge）；
- 台账只增见识不拦写：入册失败绝不影响配置写盘本身；
- 台账文件在数据目录（TUTTI_DATA 隔离），测试环境天然不碰真实记录。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path

from . import paths

_LOCK = threading.RLock()
_FILE_NAME = "bind_ledger.json"
_MAX_STASH_PER_PATH = 3
_MAX_PATHS = 64
_MAX_SNAPSHOT_BYTES = 256 * 1024


def _file():
    return Path(paths.default_data_dir()) / _FILE_NAME


def _load():
    try:
        with open(_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data.setdefault("applied", {})
            data.setdefault("stash", {})
            return data
    except (OSError, ValueError):
        pass
    return {"applied": {}, "stash": {}}


def _save(data):
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=_FILE_NAME + ".", suffix=".tmp",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _sha(data):
    return hashlib.sha256(data or b"").hexdigest()


def _now_ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _norm(path):
    # realpath 与 Path.resolve 在 Windows 上的盘符大小写形态可能不同，
    # 归一后比较（normcase 同时统一分隔符大小写语义）
    return os.path.normcase(str(path))


def _in_data_dir(resolved):
    try:
        base = _norm(Path(paths.default_data_dir()).resolve())
        s = _norm(resolved)
        return s.startswith(base + os.sep) or s == base
    except Exception:
        return False


def _resolve_agent(resolved_path):
    """路径反查 agent id（调用方没带 meta 时的兜底归因；查不到返回空串）。"""
    try:
        from . import catalog
        from .manager import _safe_config_path
        for entry in catalog.load() or []:
            if not isinstance(entry, dict):
                continue
            raw = (entry.get("config") or {}).get("path")
            if not raw:
                continue
            try:
                if _norm(Path(_safe_config_path(raw)).resolve()) == _norm(resolved_path):
                    return str(entry.get("id") or "")
            except Exception:
                continue
    except Exception:
        pass
    return ""


def record_write(resolved_path, previous_bytes, new_bytes, meta=None):
    """写盘成功后入册：旧字节进 stash（可还原），新摘要进 applied（审计）。

    prev/new 传 None 表示「该文件此前不存在」。任何异常都被吞掉——台账
    永远不能反过来卡配置写盘。"""
    resolved_path = str(resolved_path)
    if not resolved_path or _in_data_dir(resolved_path):
        return
    if not meta:
        aid = _resolve_agent(resolved_path)
        if aid:
            meta = {"agent": aid}
    too_big = previous_bytes is not None and \
        len(previous_bytes) > _MAX_SNAPSHOT_BYTES
    entry = {
        "ts": _now_ts(),
        "before": None if (previous_bytes is None or too_big)
        else base64.b64encode(previous_bytes).decode("ascii"),
        "before_sha": _sha(previous_bytes) if previous_bytes is not None else None,
        "before_bytes": len(previous_bytes) if previous_bytes is not None else 0,
        "snapshot_skipped": bool(too_big),
        "after_sha": _sha(new_bytes),
        "meta": meta or {},
    }
    with _LOCK:
        data = _load()
        stash = data["stash"].setdefault(resolved_path, [])
        stash.append(entry)
        del stash[:-_MAX_STASH_PER_PATH]
        data["applied"][resolved_path] = {
            "ts": entry["ts"], "sha256": entry["after_sha"],
            "size": len(new_bytes or b""), "meta": meta or {},
        }
        # 路径数封顶：按最近一笔时间淘汰最老的路径，防长尾配置无限累积
        if len(data["stash"]) > _MAX_PATHS:
            ranked = sorted(data["stash"].items(),
                            key=lambda kv: kv[1][-1]["ts"] if kv[1] else "")
            for key, _ in ranked[:len(data["stash"]) - _MAX_PATHS]:
                data["stash"].pop(key, None)
        _save(data)


def view():
    """API 视图：applied 原样 + stash 摘要（不含字节体，可能很大）。"""
    with _LOCK:
        data = _load()
    stash = []
    for path, entries in (data.get("stash") or {}).items():
        for e in entries:
            stash.append({"path": path, "ts": e.get("ts"),
                          "before_sha": e.get("before_sha"),
                          "before_bytes": e.get("before_bytes") or 0,
                          "after_sha": e.get("after_sha"),
                          "snapshot_skipped": bool(e.get("snapshot_skipped")),
                          "meta": e.get("meta") or {}})
    return {"applied": data.get("applied") or {}, "stash": stash}


def restore(path):
    """把 path 还原成我们最近一次写入前的字节。

    铁律（magpie 字节级还原语义）：当前文件内容仍等于我们最后一次写入的
    内容才动手；被任何人改过就拒绝——绝不在别人改动之上盲目覆盖。旧态是
    「文件不存在」且未被再次改动时，删掉我们建的那个文件。"""
    path = str(path or "")
    if not path:
        return {"ok": False, "error": "缺少 path"}
    with _LOCK:
        data = _load()
        entries = (data.get("stash") or {}).get(path) or []
        if not entries:
            return {"ok": False, "error": "该路径没有可还原的写盘记录"}
        latest = entries[-1]
        if latest.get("snapshot_skipped"):
            return {"ok": False,
                    "error": "旧内容超过快照上限未入册，无法还原（只记了摘要）"}
        try:
            with open(path, "rb") as fh:
                current = fh.read()
        except OSError:
            current = None
        if _sha(current) != latest.get("after_sha"):
            return {"ok": False,
                    "error": "文件在写入后又被改动过（当前内容≠我们最后一次写入），"
                             "拒绝盲目还原；请先人工确认再操作"}

        def _apply(payload):
            if payload is None:
                try:
                    os.remove(path)
                    return True
                except OSError as e:
                    return False
            d = os.path.dirname(path) or "."
            fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".",
                                       suffix=".restore", dir=d)
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(payload)
                os.replace(tmp, path)
                return True
            except Exception:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                return False

        if latest.get("before") is None:
            if latest.get("before_sha") is None:
                # 写入前文件不存在：删掉我们建的这个
                if not _apply(None):
                    return {"ok": False, "error": "删除文件失败"}
                action, restored_bytes = "removed", None
            else:
                return {"ok": False, "error": "旧内容快照缺失，无法还原"}
        else:
            restored_bytes = base64.b64decode(latest["before"])
            if not _apply(restored_bytes):
                return {"ok": False, "error": "还原写入失败"}
            action = "restored"
        entries.pop()
        if not entries:
            data["stash"].pop(path, None)
        data["applied"][path] = {
            "ts": _now_ts(),
            "sha256": _sha(restored_bytes),
            "size": len(restored_bytes or b""),
            "meta": {"restored_from": latest.get("ts")},
        }
        try:
            _save(data)
        except Exception:
            pass
        return {"ok": True, "path": path, "action": action,
                "restored_from": latest.get("ts")}
