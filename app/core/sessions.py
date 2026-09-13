# -*- coding: utf-8 -*-
"""本地已有会话扫描：读取各家 CLI 落盘的会话记录，
供任务"在已有会话上继续"选择。只读解析文件头部，扫描保持轻量。

布局（2026-09 本机实测）：
  codex  : ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<sid>.jsonl
           首行 type=session_meta（payload.id / cwd）；用户消息为
           event_msg(payload.type=user_message, payload.message=...)
  claude : ~/.claude/projects/<项目slug>/<sid>.jsonl
           队列行 {"type":"queue-operation","operation":"enqueue","content":...}
           或消息行 {"type":"user","message":{"content":...}}
  qwen   : ~/.qwen/projects/<项目slug>/chats/<sid>.jsonl
           真实用户消息 type=user 且 provenance=real_user，文本在
           message.parts[].text，行上带 cwd 原始项目路径
  opencode / mimo-code : ~/.local/share/{opencode,mimocode}/*.db（SQLite，
           两家同源同 schema）。只读打开（mode=ro，WAL 可并发读），
           session 表取 id/directory/标题，message+part 取首条用户文本。
"""
from __future__ import annotations

import json
import os
import sqlite3
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


def qwen_root():
    return Path(os.path.expanduser("~/.qwen/projects"))


def opencode_db():
    return Path(os.path.expanduser("~/.local/share/opencode/opencode.db"))


def mimo_db():
    return Path(os.path.expanduser("~/.local/share/mimocode/mimocode.db"))


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
        preview, msg_count, cwd = "", 0, ""
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
                    # 行上的 cwd 是真实项目路径（目录名只是转义后的 slug，不能当路径用）
                    if not cwd and ev.get("cwd"):
                        cwd = ev["cwd"]
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
                    if preview and msg_count > 1 and cwd:
                        break
        except Exception:
            continue
        out.append({
            "agent": "claude-code",
            "session_id": f.stem,
            "file": str(f),
            "project": cwd or f.parent.name,
            "mtime": _fmt_ts(f.stat().st_mtime),
            "turns": msg_count,
            "preview": preview or "（未解析到用户消息）",
        })
        if len(out) >= SCAN_LIMIT:
            break
    return out


def _scan_opencode_db(db_path, agent):
    """opencode 系会话库（opencode / mimocode 同源同 schema）。

    只读打开（mode=ro 不阻塞 WAL）；每会话取标题兜底，再找第一条
    用户消息的首个文本 part 做预览。
    """
    if not db_path.is_file():
        return []
    uri = "file:%s?mode=ro" % str(db_path).replace("\\", "/")
    try:
        con = sqlite3.connect(uri, uri=True)
    except Exception:
        return []
    out = []
    try:
        rows = con.execute(
            "SELECT id, directory, title, time_updated FROM session "
            "WHERE time_archived IS NULL "
            "ORDER BY time_updated DESC LIMIT ?", (SCAN_LIMIT,)).fetchall()
        for sid, directory, title, ts in rows:
            preview = _preview_cut(title)
            try:
                for mid, mdata in con.execute(
                        "SELECT id, data FROM message WHERE session_id=? "
                        "ORDER BY time_created LIMIT 6", (sid,)):
                    if json.loads(mdata).get("role") != "user":
                        continue
                    texts = []
                    for (pdata,) in con.execute(
                            "SELECT data FROM part WHERE session_id=? AND message_id=? "
                            "ORDER BY time_created LIMIT 4", (sid, mid)):
                        p = json.loads(pdata)
                        if isinstance(p, dict) and p.get("text"):
                            texts.append(p["text"])
                    pv = _preview_cut(" ".join(texts))
                    if pv:
                        preview = pv
                    break
            except Exception:
                pass
            out.append({
                "agent": agent,
                "session_id": sid,
                "file": str(db_path),
                "project": directory or "",
                "mtime": _fmt_ts(ts / 1000.0) if ts else "",
                "preview": preview or "（未解析到用户消息）",
            })
    except Exception:
        pass
    finally:
        try:
            con.close()
        except Exception:
            pass
    return out


def _scan_qwen():
    """QwenCode（gemini-cli 系）会话：~/.qwen/projects/<slug>/chats/<sid>.jsonl，
    文件名即会话 id（--resume 直接用它）。"""
    root = qwen_root()
    if not root.is_dir():
        return []
    files = sorted(root.glob("*/chats/*.jsonl"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for f in files[:SCAN_MAX_FILES]:
        preview, project, turns = "", "", 0
        try:
            with open(f, encoding="utf-8", errors="replace") as fp:
                for i, raw in enumerate(fp):
                    if i >= HEAD_LINES:
                        break
                    line = raw.rstrip("\n")
                    if len(line) > 2_000_000:  # 病理超长行跳过，不做解析
                        continue
                    if not line.startswith("{"):
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    if ev.get("type") != "user":
                        continue
                    if ev.get("provenance") not in (None, "real_user"):
                        continue
                    turns += 1
                    if not project and ev.get("cwd"):
                        project = ev["cwd"]
                    if not preview:
                        msg = ev.get("message") or {}
                        texts = [p.get("text", "") for p in (msg.get("parts") or [])
                                 if isinstance(p, dict) and p.get("text")]
                        pv = _preview_cut(" ".join(texts))
                        if pv:
                            preview = pv
                    if preview and turns > 1:
                        break
        except Exception:
            continue
        out.append({
            "agent": "qwencode",
            "session_id": f.stem,
            "file": str(f),
            "project": project or f.parent.parent.name,
            "mtime": _fmt_ts(f.stat().st_mtime),
            "turns": turns,
            "preview": preview or "（未解析到用户消息）",
        })
        if len(out) >= SCAN_LIMIT:
            break
    return out


def scan(force=False):
    """扫描全部支持的会话源。60 秒缓存；键为 catalog 的智能体 id，
    恒定包含五个已知源（无会话则空列表），UI 由此判断哪些工具可选。"""
    if not force and _CACHE["data"] and time.time() - _CACHE["ts"] < 60:
        return _CACHE["data"]
    data = {
        "codex-cli": _scan_codex(),
        "claude-code": _scan_claude(),
        "opencode": _scan_opencode_db(opencode_db(), "opencode"),
        "qwencode": _scan_qwen(),
        "mimo-code": _scan_opencode_db(mimo_db(), "mimo-code"),
    }
    _CACHE["data"] = data
    _CACHE["ts"] = time.time()
    return data
