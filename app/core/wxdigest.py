# -*- coding: utf-8 -*-
"""微信群摘要：监控文件夹里的聊天记录导出，按群增量生成 AI 摘要。

数据流：导出 txt/md 丢进监控文件夹（文件名=群名，每条以「2024-01-01 12:34 发送者」
开头，UTF-8/GBK 都认）→ scan() 解析消息 → 按群游标（last_ts+边界消息身份）取增量
→ builtin_agent 直连模型生成摘要 → digests.jsonl 追加 + 游标前进。
调度挂在 automation._tick（fire_due 自节流，同 zentao/publish 模式）。

增量语义：
  - 首次接入不回溯历史，只摘要最近一窗（MAX_WINDOW_MSGS 条 / max_chars 字）；
  - 之后每次只摘游标之后的新消息，窗口超限时每拍摘一窗，余量下拍接着摘；
  - 同秒多条消息靠「末条身份」（sender+内容指纹）精确接续，不会重摘/漏摘；
  - 摘要失败游标不动，下拍自动重试（同 zentao 重试节流）。
隐私：聊天记录只落本地 data/；摘要请求会发给当前绑定链上的模型（设置页明示）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import builtin_agent, paths, runner

log = logging.getLogger(__name__)

_LOCK = threading.RLock()
_FILE = paths.DATA_DIR / "wxdigest.json"
_DIR = paths.DATA_DIR / "wxdigest"           # digests.jsonl 追加式台账
_STATE = {
    "config": {},       # 持久配置（_CFG_DEFAULTS）
    "cursors": {},      # 群名 → {last_ts, sender, hash, mtime, size}
    "unseen": 0,        # 未读摘要数（蜂徽章）
    "last_scan": "",
    "next_scan": "",
    "last_error": "",
}
_LOADED = False

INTERVAL_MIN, INTERVAL_MAX = 5, 1440
RETRY_DELAY_MIN = 10
MAX_WINDOW_MSGS = 400        # 单次摘要窗口条数上限
MAX_MSG_CHARS = 2000         # 单条消息进提示词的截断长度
VIEW_DIGESTS = 100           # view() 返回的摘要条数上限（最新在前）

_CFG_DEFAULTS = {
    "enabled": False,
    "watch_dir": "",         # 监控文件夹（绝对路径）
    "interval_minutes": 30,  # 扫描节流
    "max_chars": 12000,      # 单窗正文字符上限（喂给模型）
}
_UPDATABLE = ("enabled", "watch_dir", "interval_minutes", "max_chars")

_PROMPT = """你是 CodeBee 的群聊摘要助手。下面是微信群「%s」的一段聊天记录（共 %d 条）。请输出中文摘要，格式：
- 一句话总览
- 主要话题与结论（要点列表，关键决策注明是谁说的）
- 待办/行动项（谁要做什么；没有写「无」）
- 未决/争议（没有可省略此节）
只输出摘要本身，总长 300 字以内，不要复述原文，不要输出任何机器标记。

【聊天记录】
%s"""


# ---------------------------------------------------------------- 解析

# 行首时间戳 + 发送者：2024-01-01 12:34:56 昵称 / 2024/1/1 12:34 昵称 / 2024年1月1日 12:34 昵称
_STAMP_RE = re.compile(
    r"^\s*(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?"
    r"\s*秒?\s*[:：]?\s*(.*)$")
# 群名头：文件首行【群名】xxx 或 群名：xxx
_GROUP_RE = re.compile(r"^\s*(?:【([^】]{1,60})】|(?:群(?:聊)?名(?:称)?|群)[:：]\s*(\S.{0,58}?))\s*$")


def _norm_ts(y, mo, d, h, mi, s):
    try:
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0)) \
            .strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""


def parse_messages(text):
    """导出文本 → [ {ts, sender, text, hash} ]（按出现序，时间乱序时调用方排序）。

    格式：每条以行首时间戳开头，行尾是发送者；其后续行（直到下一条时间戳）是
    消息正文（可多行）。文件首行若是「【群名】」/「群名：xxx」头则被忽略（群名
    由调用方取）。识别不了时间戳的行忽略——容错脏文件。"""
    msgs = []
    cur = None
    group_line_skipped = False
    for raw in text.splitlines():
        line = raw.rstrip()
        m = _STAMP_RE.match(line)
        if m:
            ts = _norm_ts(m.group(1), m.group(2), m.group(3),
                          m.group(4), m.group(5), m.group(6))
            if not ts:
                continue
            cur = {"ts": ts, "sender": m.group(7).strip().lstrip("-").strip(),
                   "text": "", "hash": ""}
            msgs.append(cur)
            continue
        if not line.strip():
            continue
        if cur is None:
            group_line_skipped = True     # 时间戳前的杂行（群名头/标题）不入消息
            continue
        if not cur["text"]:
            # 「昵称：内容」同行形态（部分工具导出把发送者放正文首行）
            mm = re.match(r"^([^:：]{1,30})[:：]\s*(.*)$", line.strip())
            if mm and not cur["sender"] and mm.group(2):
                cur["sender"] = mm.group(1).strip()
                line = mm.group(2)
            elif not cur["sender"]:
                cur["sender"] = line.strip()[:30]
                continue
        cur["text"] = (cur["text"] + "\n" if cur["text"] else "") + line.strip()
    for m in msgs:
        m["text"] = m["text"].strip()[:MAX_MSG_CHARS]
        m["hash"] = hashlib.sha256((m["sender"] + "\x00" + m["text"])
                                   .encode("utf-8", "replace")).hexdigest()[:8]
    return [m for m in msgs if m["text"] or m["sender"]]


def group_name_of(path, text):
    """群名：文件头「【群名】/群名：」优先，否则文件名去扩展名。"""
    for raw in (text or "").splitlines()[:3]:
        m = _GROUP_RE.match(raw)
        if m:
            return (m.group(1) or m.group(2) or "").strip()
    return Path(path).stem.strip() or "未命名群"


# ---------------------------------------------------------------- 游标与窗口

def _new_since(msgs, cursor):
    """游标之后的增量。游标消息以「ts+sender+hash」在同秒批次里定位末条，
    精确接续；游标消息已被裁剪/重导时退回按时间过滤。"""
    if not cursor or not cursor.get("last_ts"):
        return list(msgs)
    ts = cursor.get("last_ts")
    boundary = -1
    for i, m in enumerate(msgs):
        if m["ts"] == ts and m["sender"] == cursor.get("sender") \
                and m["hash"] == cursor.get("hash"):
            boundary = i
    if boundary >= 0:
        return msgs[boundary + 1:]
    return [m for m in msgs if m["ts"] > ts]


def _cap(max_chars):
    """窗口字符上限：配置缺失回落默认（钳制已由 save_config 负责，这里不设下限）。"""
    try:
        v = int(max_chars)
    except (TypeError, ValueError):
        return 12000
    return v if v > 0 else 12000


def _window_from_head(msgs, max_chars):
    """从头装一个摘要窗口（条数/字符双上限），返回 (窗口, 余量)。"""
    out, used = [], 0
    cap = _cap(max_chars)
    for m in msgs:
        w = len(m["sender"]) + len(m["text"]) + 24
        if out and used + w > cap:
            break
        if len(out) >= MAX_WINDOW_MSGS:
            break
        out.append(m)
        used += w
    return out, msgs[len(out):]


def _tail_window(msgs, max_chars):
    """首次接入：从末尾往前装一窗（不回溯更早历史）。"""
    out, used = [], 0
    cap = _cap(max_chars)
    for m in reversed(msgs):
        w = len(m["sender"]) + len(m["text"]) + 24
        if out and used + w > cap:
            break
        if len(out) >= MAX_WINDOW_MSGS:
            break
        out.append(m)
        used += w
    out.reverse()
    return out


def _cursor_from(msg):
    return {"last_ts": msg["ts"], "sender": msg["sender"], "hash": msg["hash"],
            "mtime": 0.0, "size": 0}


# ---------------------------------------------------------------- 台账

def _digests_path():
    return _DIR / "digests.jsonl"


def _append_digest(rec):
    _DIR.mkdir(parents=True, exist_ok=True)
    with open(str(_digests_path()), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _read_digests(limit=VIEW_DIGESTS):
    try:
        raw = _digests_path().read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if isinstance(rec, dict):   # 脏行（如整批数组误落单行）跳过不炸
            out.append(rec)
    out.sort(key=lambda d: str(d.get("created_at") or ""), reverse=True)
    return out[:limit]


# ---------------------------------------------------------------- 摘要主流程

def _summarize(cfg, group, window, watch):
    bi = builtin_agent.resolve()
    if not bi:
        raise RuntimeError("无可用模型供应商，请先到「绑定」页配置模型")
    body = "\n".join("%s %s: %s" % (
        m["ts"], m["sender"] or "?", m["text"].replace("\n", " ")) for m in window)
    prompt = _PROMPT % (group, len(window), body)
    r = builtin_agent.run(bi, prompt, workdir=str(watch), timeout=240)
    if not r.get("ok"):
        raise RuntimeError(r.get("error") or "模型调用失败")
    text = (r.get("text") or "").strip()
    if not text:
        raise RuntimeError("模型返回空摘要")
    return text, bi


def _process_file(fp, cfg):
    """处理一个导出文件：解析 → 增量 → 摘要 → 落台账 + 推游标。返回生成条数。
    摘要失败抛 RuntimeError（游标不动，下拍重试）。"""
    st = fp.stat()
    try:
        text = runner.read_text_any_enc(fp)
    except Exception as e:
        raise RuntimeError("读取失败 %s: %s" % (fp.name, e))
    group = group_name_of(fp, text)
    with _LOCK:
        cur = dict(_STATE["cursors"].get(group) or {})
    # 文件没变且已消费到末尾：跳过重parse（大文件性能护栏）
    if cur.get("mtime") == st.st_mtime and cur.get("size") == st.st_size \
            and not _pending_expect(cur):
        return 0
    msgs = sorted(parse_messages(text), key=lambda m: m["ts"])
    if not msgs:
        return 0
    if cur.get("last_ts"):
        window, rest = _window_from_head(_new_since(msgs, cur),
                                         cfg.get("max_chars"))
    else:
        # 首次接入：只摘尾部一窗（不回溯历史），故无余量
        window, rest = _tail_window(msgs, cfg.get("max_chars")), []
    if not window:
        new_cur = cur
        new_cur.update({"mtime": st.st_mtime, "size": st.st_size,
                        "pending": False})
        with _LOCK:
            _STATE["cursors"][group] = new_cur
            _save_locked()
        return 0
    summary, bi = _summarize(cfg, group, window, fp.parent)
    last = window[-1]
    rec = {"id": "d%s-%s" % (time.strftime("%Y%m%d%H%M%S"),
                             hashlib.sha256(group.encode("utf-8")).hexdigest()[:6]),
           "group": group, "from_ts": window[0]["ts"], "to_ts": last["ts"],
           "count": len(window), "text": summary,
           "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "model": bi.get("model") or "", "provider": bi.get("provider_name") or "",
           "source": fp.name}
    with _LOCK:
        new_cur = _cursor_from(last)
        # pending：本窗装不下的余量还在文件里，文件没变也要继续摘（否则余量
        # 会被上面的 mtime 短路永久吃掉——新增消息超过一窗时必现）。
        new_cur.update({"mtime": st.st_mtime, "size": st.st_size,
                        "pending": bool(rest)})
        _STATE["cursors"][group] = new_cur
        _STATE["unseen"] = int(_STATE.get("unseen") or 0) + 1
        _append_digest(rec)
        _save_locked()
    log.info("wxdigest: 群「%s」新摘要（%d 条消息 → %d 字%s）",
             group, len(window), len(summary),
             "，余量 %d 条待续摘" % len(rest) if rest else "")
    return 1


def _pending_expect(cur):
    """文件没变也不能跳过的两种情况：上次摘要失败（游标没 mtime 落账）、
    上窗有装不下的余量（pending）。"""
    return (not cur.get("mtime")) or bool(cur.get("pending"))


def _poll(force=False):
    """一次扫描：监控文件夹里所有 txt/md 逐个增量摘要。异常不外抛。"""
    _ensure_loaded()
    with _LOCK:
        cfg = _cfg()
    out = {"ok": True, "made": 0, "error": "", "skipped": ""}
    if not force:
        if not cfg.get("enabled"):
            out["skipped"] = "disabled"
            return out
        with _LOCK:
            nxt = _STATE.get("next_scan") or ""
        if nxt:
            try:
                if datetime.fromisoformat(nxt) > datetime.now():
                    out["skipped"] = "not_due"
                    return out
            except ValueError:
                pass
    err = ""
    watch = str(cfg.get("watch_dir") or "").strip()
    if not watch:
        err = "未设置监控文件夹"
    else:
        wdir = Path(watch).expanduser()
        if not wdir.is_dir():
            err = "监控文件夹不存在：%s" % watch
        else:
            files = sorted(list(wdir.glob("*.txt")) + list(wdir.glob("*.md")))
            for fp in files:
                try:
                    out["made"] += _process_file(fp, cfg)
                except Exception as e:
                    msg = "%s：%s" % (fp.name, e)
                    err = (err + "；" + msg) if err else msg
                    log.warning("wxdigest: %s", msg)
    now = datetime.now()
    delay = timedelta(minutes=max(INTERVAL_MIN, int(cfg.get("interval_minutes") or 30)))
    if err:
        delay = timedelta(minutes=RETRY_DELAY_MIN)
    with _LOCK:
        _STATE["last_scan"] = now.strftime("%Y-%m-%d %H:%M:%S")
        _STATE["next_scan"] = (now + delay).strftime("%Y-%m-%d %H:%M:%S")
        _STATE["last_error"] = err
        _save_locked()
    out["error"] = err
    out["ok"] = not err
    return out


# ---------------------------------------------------------------- 配置/视图

def _cfg():
    cfg = dict(_CFG_DEFAULTS)
    cfg.update(_STATE.get("config") or {})
    return cfg


def save_config(patch):
    """部分更新配置。watch_dir 不校验存在（目录可以后建），扫描时给提示。"""
    _ensure_loaded()
    patch = patch if isinstance(patch, dict) else {}
    with _LOCK:
        cfg = _cfg()
        for k in _UPDATABLE:
            if k not in patch:
                continue
            v = patch[k]
            if k == "watch_dir":
                v = str(v or "").strip()
                if v and not Path(v).expanduser().is_absolute():
                    raise ValueError("监控文件夹必须是绝对路径")
            elif k == "interval_minutes":
                try:
                    v = max(INTERVAL_MIN, min(INTERVAL_MAX, int(v)))
                except (TypeError, ValueError):
                    raise ValueError("interval_minutes 必须是 %d-%d 的整数"
                                     % (INTERVAL_MIN, INTERVAL_MAX))
            elif k == "max_chars":
                try:
                    v = max(4000, min(60000, int(v)))
                except (TypeError, ValueError):
                    raise ValueError("max_chars 必须是 4000-60000 的整数")
            elif k == "enabled":
                v = bool(v)
            cfg[k] = v
        _STATE["config"] = cfg
        if cfg.get("enabled"):
            _STATE["next_scan"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save_locked()
    return view()["config"]


def view():
    """前端视图：配置 + 群游标 + 最近摘要（新在前）+ 未读数 + 模型可用性。"""
    _ensure_loaded()
    with _LOCK:
        cfg = dict(_cfg())
        cursors = [dict({"name": name, "last_ts": c.get("last_ts") or ""},
                        **{k: c.get(k) for k in ("mtime", "size")})
                   for name, c in sorted((_STATE.get("cursors") or {}).items())]
        unseen = int(_STATE.get("unseen") or 0)
        last_scan, next_scan, last_err = (_STATE.get("last_scan") or "",
                                          _STATE.get("next_scan") or "",
                                          _STATE.get("last_error") or "")
    has_model = False
    try:
        has_model = builtin_agent.resolve() is not None
    except Exception:
        has_model = False
    return {"config": cfg, "groups": cursors, "digests": _read_digests(),
            "unseen": unseen, "last_scan": last_scan, "next_scan": next_scan,
            "last_error": last_err, "has_model": has_model}


def seen_clear():
    """打开面板即清零未读徽章。"""
    _ensure_loaded()
    with _LOCK:
        _STATE["unseen"] = 0
        _save_locked()
    return view()


def pet_digest():
    """桌面蜜蜂（/api/pet_state）的最小喂食集：未读数 + 最新一条摘要。
    只读 jsonl 尾窗，不整体载入；异常一律兜底零值，绝不影响蜜蜂轮询。"""
    try:
        _ensure_loaded()
        with _LOCK:
            unseen = int(_STATE.get("unseen") or 0)
        latest = None
        p = _digests_path()
        if p.exists():
            with open(str(p), "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 65536))
                chunk = f.read().decode("utf-8", "replace")
            for line in chunk.splitlines()[::-1]:
                line = line.strip()
                if not line:
                    continue
                try:
                    latest = json.loads(line)
                    break
                except Exception:
                    continue
        return {"unseen": unseen, "latest": latest}
    except Exception:
        return {"unseen": 0, "latest": None}


# ---------------------------------------------------------------- 调度接线

def fire_due():
    """automation._tick 每拍调用：内部自节流，未启用/没到点零开销返回。"""
    _ensure_loaded()
    with _LOCK:
        if not (_STATE.get("config") or {}).get("enabled"):
            return None
    return _poll(force=False)


def scan_now():
    """手动「立即扫描」：绕过启用闸与节流。"""
    return _poll(force=True)


def start():
    """服务启动接线：加载状态。"""
    _ensure_loaded()
    with _LOCK:
        return len(_STATE.get("cursors") or {})


# ---------------------------------------------------------------- 装载

def _save_locked():
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = {"version": 1, "config": _STATE.get("config") or {},
           "cursors": _STATE.get("cursors") or {},
           "unseen": int(_STATE.get("unseen") or 0),
           "last_scan": _STATE.get("last_scan") or "",
           "next_scan": _STATE.get("next_scan") or "",
           "last_error": _STATE.get("last_error") or ""}
    _FILE.write_text(json.dumps(tmp, ensure_ascii=False, indent=1), encoding="utf-8")


def _normalize(d):
    d = d if isinstance(d, dict) else {}
    cfg = dict(_CFG_DEFAULTS)
    cfg.update(d.get("config") or {})
    return {"config": cfg,
            "cursors": {str(k): dict(v) for k, v in (d.get("cursors") or {}).items()
                        if isinstance(v, dict)},
            "unseen": int(d.get("unseen") or 0),
            "last_scan": str(d.get("last_scan") or ""),
            "next_scan": str(d.get("next_scan") or ""),
            "last_error": str(d.get("last_error") or "")}


def load(force=False):
    global _LOADED
    with _LOCK:
        if _LOADED and not force:
            return 0
        try:
            raw = _FILE.read_text(encoding="utf-8")
            data = _normalize(json.loads(raw))
        except FileNotFoundError:
            data = _normalize({})
        except Exception:
            log.exception("wxdigest: 状态文件损坏，按全新状态起")
            data = _normalize({})
        _STATE.update(data)
        _LOADED = True
        return len(_STATE.get("cursors") or {})


def _ensure_loaded():
    if not _LOADED:
        load()


def _test_reset():
    """测试钩子：清空内存状态（配合测试重绑 _FILE/_DIR 用）。"""
    with _LOCK:
        _STATE["config"] = dict(_CFG_DEFAULTS)
        _STATE["cursors"] = {}
        _STATE["unseen"] = 0
        _STATE["last_scan"] = _STATE["next_scan"] = _STATE["last_error"] = ""
        globals()["_LOADED"] = True
