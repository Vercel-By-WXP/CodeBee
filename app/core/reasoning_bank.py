# -*- coding: utf-8 -*-
"""ReasoningBank 模式库（2026-11-01，借鉴 ruvnet/ruflo 的 ReasoningBank/AgentDB
记忆架构；MIT 许可，只借思路，零代码零依赖）。

跨任务的「成功模式 / 失败教训」本地检索层：
- 存：data/reasoning-bank/rows.jsonl 追加式行存储。两个来源——
  (a) run 收尾自动入账（jobs._run_job finally，零额度零模型）；
  (b) record() 手工/程序登记经验；reindex_memory() 把项目记忆 vault 里
      已采纳的条目批量搬进来（幂等，指纹去重）。
- 检：字符二元组 TF-IDF 余弦（纯 stdlib，不调 LLM、不装依赖）——
  任务类型亲和加权 + 成功优先（失败仍可检回当教训）+ 30 天半衰的时效
  衰减（只占四成权重，老经验不至于灭失）。
- 纪律：绝不抛错连累主流程（同 attention）；坏行跳过不入账；
  同源同型同文按内容指纹去重，重复入账只累计 count 不重复存储。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from pathlib import Path

from . import paths

_LOCK = threading.RLock()
_TOKEN_RE = re.compile(r"[a-z0-9]+", re.ASCII)
_CJK = re.compile(r"[\u4e00-\u9fff]")
_OUTCOME_W = {"success": 1.0, "neutral": 0.75, "failure": 0.5}
_MAX_TEXT = 600
_MAX_ROWS = 5000

_ROWS = None          # 惰性加载的行缓存
_MTIME = 0.0


def _dir():
    return Path(paths.DATA_DIR) / "reasoning-bank"


def _file():
    return _dir() / "rows.jsonl"


def _digest(text):
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:12]


def _tokens(text):
    """中文按字 + 二元组，西文按词，全小写。检索粒度够用即可。"""
    t = str(text or "").lower()
    out = list(_TOKEN_RE.findall(t))
    cjk = _CJK.findall(t)
    out.extend(cjk)
    out.extend(cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1))
    return out


def _load_locked():
    global _ROWS, _MTIME
    f = _file()
    try:
        mt = f.stat().st_mtime
    except OSError:
        mt = 0.0
    if _ROWS is not None and mt == _MTIME:
        return _ROWS
    rows = []
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue          # 坏行跳过：日志损坏绝不拦检索
            if isinstance(d, dict) and d.get("id"):
                rows.append(d)
    except OSError:
        pass
    _ROWS = rows
    _MTIME = mt
    return rows


def _append_locked(row):
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _row_text(text):
    return str(text or "").strip()[:_MAX_TEXT]


def record(text, task_type="", outcome="neutral", score=None, source="manual",
           title="", error="", refs=None, ts=None):
    """登记一条模式/教训。同 (source, task_type, outcome, 正文指纹) 去重，
    重复登记只把已有行的 count +1 并刷新 seen_at。返回行 dict。"""
    text = _row_text(text)
    if not text:
        return {}
    if outcome not in _OUTCOME_W:
        outcome = "neutral"
    key = _digest("%s|%s|%s|%s" % (source, task_type, outcome, text))
    with _LOCK:
        rows = _load_locked()
        for r in rows:
            if r.get("key") == key:
                r["count"] = int(r.get("count") or 1) + 1
                r["seen_at"] = ts or time.time()
                try:
                    _rewrite_locked(rows)
                except OSError:
                    pass
                return dict(r, deduped=True)
        row = {
            "id": "rb-%s-%d" % (key[:8], int((ts or time.time()) * 1000)),
            "key": key, "source": str(source or "manual"),
            "task_type": str(task_type or ""), "title": str(title or ""),
            "text": text, "outcome": outcome,
            "score": float(score) if score is not None else None,
            "error": str(error or "")[:300],
            "refs": dict(refs or {}),
            "ts": ts or time.time(), "seen_at": ts or time.time(),
            "count": 1,
        }
        _append_locked(row)
        rows.append(row)
        _ROWS = rows
        return dict(row)


def _rewrite_locked(rows):
    """去重回写（count/seen_at 更新）。临时文件 + 替换，半截写不毁库。"""
    f = _file()
    tmp = f.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                           for r in rows), encoding="utf-8")
    tmp.replace(f)
    global _MTIME
    try:
        _MTIME = f.stat().st_mtime
    except OSError:
        _MTIME = 0.0


def reindex_memory(max_per_task=50):
    """把项目记忆 vault 的已采纳条目搬进模式库（source=memory，幂等）。
    返回 {"added": n, "scanned": m}。拒绝条目不进（那是诊断不是知识）。"""
    added = scanned = 0
    try:
        from . import memory_vault as mv
        for t, _wd, entries in mv._tasks_with_memory():
            n = 0
            for e in entries:
                if n >= max_per_task:
                    break
                if str(e.get("status") or "") == "rejected":
                    continue
                before = added
                row = record(text=e.get("content") or e.get("title") or "",
                             task_type=str(t.get("type") or ""),
                             outcome="success",
                             source="memory",
                             title=e.get("title") or "",
                             refs={"task_id": t.get("id") or "",
                                   "entry_id": e.get("id") or ""})
                if row and not row.get("deduped"):
                    added += 1
                if row:
                    scanned += 1
                if added > before:
                    n += 1
    except Exception:
        pass
    return {"added": added, "scanned": scanned}


def _vec(tokens, idf):
    v = {}
    for tk in tokens:
        v[tk] = v.get(tk, 0.0) + idf.get(tk, 1.0)
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return v, norm


def retrieve(query, task_type="", k=3, min_score=0.02):
    """按查询文本检索 top-k。返回 [{...row, "sim", "rank"}]，绝不抛错。"""
    q = _row_text(query)
    if not q:
        return []
    try:
        with _LOCK:
            rows = list(_load_locked())
        if not rows:
            return []
        df = {}
        docs = []
        for r in rows:
            tks = _tokens((r.get("title") or "") + " " + (r.get("text") or "")
                          + " " + (r.get("task_type") or ""))
            seen = set(tks)
            for tk in seen:
                df[tk] = df.get(tk, 0) + 1
            docs.append((r, tks))
        n = max(1, len(rows))
        idf = {tk: math.log(1.0 + n / c) for tk, c in df.items()}
        qv, qnorm = _vec(_tokens(q), idf)
        now = time.time()
        scored = []
        for r, tks in docs:
            rv, rnorm = _vec(tks, idf)
            dot = sum(w * rv.get(tk, 0.0) for tk, w in qv.items())
            sim = dot / (qnorm * rnorm) if qnorm and rnorm else 0.0
            if sim <= 0:
                continue
            if task_type and str(r.get("task_type") or "") == str(task_type):
                sim *= 1.6                      # 类型亲和
            sim *= _OUTCOME_W.get(str(r.get("outcome") or "neutral"), 0.75)
            age_days = max(0.0, (now - float(r.get("ts") or now)) / 86400.0)
            sim *= (0.6 + 0.4 / (1.0 + age_days / 30.0))   # 时效衰减只占四成
            if sim >= min_score:
                item = dict(r, sim=round(sim, 4))
                scored.append(item)
        scored.sort(key=lambda x: -x["sim"])
        return scored[:max(1, int(k or 3))]
    except Exception:
        return []


def stats():
    with _LOCK:
        rows = list(_load_locked())
    by_outcome, by_type = {}, {}
    for r in rows:
        by_outcome[str(r.get("outcome") or "neutral")] = \
            by_outcome.get(str(r.get("outcome") or "neutral"), 0) + 1
        bt = str(r.get("task_type") or "") or "（未分型）"
        by_type[bt] = by_type.get(bt, 0) + 1
    return {"rows": len(rows), "by_outcome": by_outcome, "by_type": by_type,
            "file": str(_file())}


def prune(keep_per_type=200):
    """每任务类型最多保留 keep_per_type 条（按 ts 新到旧），超出的删。
    返回删除条数。库有 _MAX_ROWS 硬顶，prune 是给运维的手动阀门。"""
    global _ROWS
    with _LOCK:
        rows = list(_load_locked())
        keep_per_type = max(1, int(keep_per_type or 200))
        seen = {}
        keep, drop = [], []
        for r in sorted(rows, key=lambda x: -float(x.get("ts") or 0)):
            bt = str(r.get("task_type") or "") or "（未分型）"
            seen[bt] = seen.get(bt, 0) + 1
            (keep if seen[bt] <= keep_per_type else drop).append(r)
        if drop:
            try:
                _rewrite_locked(keep)
            except OSError:
                return 0
            _ROWS = keep
        return len(drop)


def reset_for_tests():
    """测试专用：清缓存（不删盘上数据）。"""
    global _ROWS, _MTIME
    with _LOCK:
        _ROWS = None
        _MTIME = 0.0
