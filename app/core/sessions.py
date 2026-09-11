# -*- coding: utf-8 -*-
"""本地已有会话扫描：读取 codex / claude 在磁盘上的会话记录，
供任务"在已有会话上继续"选择。只读解析文件头部，扫描保持轻量。

布局（2026-09 本机实测）：
  codex  : ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<sid>.jsonl
           首行 type=session_meta（payload.id / cwd）；用户消息为
           event_msg(payload.type=user_message, payload.message=...)
  claude : ~/.claude/projects/<项目slug>/<sid>.jsonl
           队列行 {"type":"queue-operation","operation":"enqueue","content":...}
           或消息行 {"type":"user","message":{"content":...}}
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

SCAN_LIMIT = 12       # 每个智能体最多返回条数
SCAN_MAX_FILES = 60   # 最多检查的文件数
HEAD_LINES = 80       # 每个文件最多读取行数
LINE_CAP = 3000       # 单行读取字符上限

# 与 catalog 的 orch.kind 对应的会话根目录
_CACHE = {"data": None, "ts": 0.0}


def codex_root():
    return Path(os.path.expanduser("~/.codex/sessions"))


def claude_root():
    return Path(os.path.expanduser("~/.claude/projects"))


def _fmt_ts(epoch):
    return time.strftime("%m-%d %H:%M", time.localtime(epoch))


def _preview_cut(text):
    text = " ".join(str(text or "").split())
    for prefix in ("<user_instructions", "<permissions", "<ENVIRONMENT", "<environment_context"):
        if text.startswith(prefix):
            return ""
    return text[:140]


def _scan_codex():
    root = codex_root()
    if not root.is_dir():
        return []
    files = sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for f in files[:SCAN_MAX_FILES]:
        sid, cwd, preview, turn_count = "", "", "", 0
        try:
            with open(f, encoding="utf-8", errors="replace") as fp:
                for i, raw in enumerate(fp):
                    if i >= HEAD_LINES:
                        break
                    line = raw.strip()
                    if not line.startswith("{"):
                        continue
                    is_meta = '"session_meta"' in line[:300]
                    # meta 行可能很长（含 base_instructions），完整解析；其余超长行截断尽力解析
                    try:
                        ev = json.loads(line if (is_meta or len(line) < 20000) else line[:LINE_CAP])
                    except Exception:
                        continue
                    if ev.get("type") == "session_meta":
                        payload = ev.get("payload") or {}
                        sid = payload.get("id") or sid
                        cwd = payload.get("cwd") or cwd
                    elif ev.get("type") == "event_msg" and (ev.get("payload") or {}).get("type") == "user_message":
                        turn_count += 1
                        if not preview:
                            preview = _preview_cut((ev.get("payload") or {}).get("message"))
                    elif ev.get("type") == "response_item" and (ev.get("payload") or {}).get("role") == "user":
                        if not preview:
                            for c in (ev["payload"].get("content") or []):
                                if isinstance(c, dict) and c.get("type") == "input_text":
                                    pv = _preview_cut(c.get("text"))
                                    if pv:
                                        preview = pv
                                    break
                    if sid and cwd and turn_count > 2:
                        break
        except Exception:
            continue
        out.append({
            "agent": "codex-cli",
            "session_id": sid or f.stem,
            "file": str(f),
            "project": cwd,
            "mtime": _fmt_ts(f.stat().st_mtime),
            "turns": turn_count,
            "preview": preview or "（未解析到用户消息）",
        })
        if len(out) >= SCAN_LIMIT:
            break
    return out


def _scan_claude():
    root = claude_root()
    if not root.is_dir():
        return []
    files = []
    for proj in root.iterdir():
        if proj.is_dir():
            files.extend(proj.glob("*.jsonl"))
    files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for f in files[:SCAN_MAX_FILES]:
        preview, msg_count = "", 0
        try:
            with open(f, encoding="utf-8", errors="replace") as fp:
                for i, raw in enumerate(fp):
                    if i >= HEAD_LINES:
                        break
                    line = raw[:LINE_CAP]
                    if not line.startswith("{"):
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    if ev.get("type") == "queue-operation" and ev.get("operation") == "enqueue":
                        if not preview:
                            preview = _preview_cut(ev.get("content"))
                    elif ev.get("type") == "user":
                        msg_count += 1
                        msg = (ev.get("message") or {})
                        content = msg.get("content")
                        if isinstance(content, list):
                            content = " ".join(
                                b.get("text", "") for b in content if isinstance(b, dict))
                        if not preview:
                            pv = _preview_cut(content)
                            if pv and not str(content or "").startswith("<"):
                                preview = pv
                    if preview and msg_count > 1:
                        break
        except Exception:
            continue
        out.append({
            "agent": "claude-code",
            "session_id": f.stem,
            "file": str(f),
            "project": f.parent.name,
            "mtime": _fmt_ts(f.stat().st_mtime),
            "turns": msg_count,
            "preview": preview or "（未解析到用户消息）",
        })
        if len(out) >= SCAN_LIMIT:
            break
    return out


def scan(force=False):
    """扫描全部支持的会话源。60 秒缓存。"""
    if not force and _CACHE["data"] and time.time() - _CACHE["ts"] < 60:
        return _CACHE["data"]
    data = {"codex-cli": _scan_codex(), "claude-code": _scan_claude()}
    _CACHE["data"] = data
    _CACHE["ts"] = time.time()
    return data
