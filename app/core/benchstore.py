# -*- coding: utf-8 -*-
"""评测基准台存储层（L0）：data/eval_bench.json 的读写与「实测分软信号」。

evalbench（L2，跑评测）与 dispatch（L0，模型链评分）都要摸这份数据——
读写下沉到这里，两边同层合法引用（dispatch 不能 import L2 的 evalbench，
分层政策 L_i 只准 import L_j (j<=i)）。

实测分软信号口径（写入 docs/execution-standard.md 候选打分权重表）：
  bonus = clamp((overall − 7.0) × 1.5, −4.5, +4.5)
  7 分以下开始倒扣、9 分封顶 +4.5；无数据/超新鲜度记 0——缺数据不猜分。
  新鲜度 14 天：评测会过期（evaluation.py 同理念），过期实测不参与排序。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import paths

_LOCK = threading.RLock()
FRESH_DAYS = 14            # 实测分新鲜度（天）；0 = 永不过期（测试用）
_BASELINE = 7.0            # 及格线：低于它开始倒扣
_K = 1.5                   # 斜率：每 1 分 ±1.5
_MAX_BONUS = 4.5           # 封顶（与在线信号 ±6 同量级，翻不动硬约束）


def _path():
    return Path(paths.DATA_DIR) / "eval_bench.json"


def read_doc():
    with _LOCK:
        try:
            data = json.loads(_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def write_doc(data):
    with _LOCK:
        _path().parent.mkdir(parents=True, exist_ok=True)
        tmp = _path().with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_path())


def read_results():
    results = read_doc().get("results")
    return [r for r in results if isinstance(r, dict)] if isinstance(results, list) else []


def _fresh(row, max_age_days):
    """行是否在新鲜度窗口内。ts 缺失/解析失败按过期处理（宁缺毋滥）。"""
    if max_age_days <= 0:
        return True
    try:
        ts = time.mktime(time.strptime(str(row.get("ts") or ""),
                                       "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return False
    return (time.time() - ts) <= max_age_days * 86400.0


def bonus_map(max_age_days=FRESH_DAYS):
    """{(provider_id, model): {"overall": x, "ts": s}}——每键取最新一条
    已得分记录，且必须在新鲜度窗口内。"""
    latest = {}
    for r in read_results():
        if not r.get("scored") or r.get("overall") is None:
            continue
        key = (str(r.get("provider_id") or ""), str(r.get("model") or ""))
        if not key[1]:
            continue
        prev = latest.get(key)
        if prev is None or str(r.get("ts") or "") > str(prev.get("ts") or ""):
            latest[key] = r
    return {k: {"overall": float(v["overall"]), "ts": str(v.get("ts") or "")}
            for k, v in latest.items() if _fresh(v, max_age_days)}


def bonus_for(provider_id, model, max_age_days=FRESH_DAYS):
    """单条目的实测软信号。返回 (score, reason)；无数据/过期 → (0.0, "")。"""
    try:
        info = bonus_map(max_age_days).get((str(provider_id or ""), str(model or "")))
        if not info:
            return 0.0, ""
        score = max(-_MAX_BONUS, min(_MAX_BONUS, (info["overall"] - _BASELINE) * _K))
        score = round(score, 2)
        return score, "实测 %.1f 分（%+.1f，%s）" % (
            info["overall"], score, (info.get("ts") or "")[:10])
    except Exception:
        return 0.0, ""
