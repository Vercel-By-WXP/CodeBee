# -*- coding: utf-8 -*-
"""零额度改版自检（P0-b，2026-11-01，借 Purrge 成本分层：检测零额度、判断才调模型）。

现状痛点（复核登记）：发布链改版后唯一的表现是一句「可能未登录或改版」；
`calibrated()` 只查表在不在、不校验命中——线上可能跑在半张失效表上而不自知。

三段式（成本分层纪律）：
  1. 盘点（零额度）：从流程表抽出全部定位选择器（sel 槽位），对目标 DOM
     批量算命中率——fixture 模式给 `present` 集合即可，不碰浏览器；
  2. 红线（零额度）：任何定位选择器未命中 → red（定位型挂一个就是那步废）；
  3. 判断（才调模型）：仅 red 时调编排者模型定位改版（给候选替代选择器），
     健康时模型调用计数恒为 0（验收口径）。

落盘：data/publish/selector-health.jsonl（追加式台账，latest 可查）。
页面来源三档：真页（page 鸭子类型）/ fixture 集合（present）/ 离线审计
（都没有 → 只盘点表结构，hit-rate=None 不判红）。
"""
from __future__ import annotations

import json
import threading
import time

from .. import paths

_LOCK = threading.RLock()
RED_NOTE = "定位选择器未全部命中（平台可能改版）"

# 批量命中检查：一次 evaluate 问全部选择器（真实页用）
_CHECK_JS = "(sels)=>sels.map(s=>({s:s,ok:!!document.querySelector(s)}));"


def steps_selectors(steps):
    """流程表里的定位选择器清单（sel 槽位；text 型步骤无法离线核验不收）。"""
    out = []
    seen = set()
    for st in steps or []:
        if not isinstance(st, dict):
            continue
        s = str(st.get("sel") or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def evaluate(page, sels, present=None):
    """命中率盘点。page=真页（鸭子类型 .call）或 None（fixture/离线模式）。
    present = fixture 页「存在的选择器集合」（测试/本地 fixture 页注入）。
    离线审计（page/present 都缺）→ rate=None 不判红。"""
    sels = [str(s) for s in (sels or [])]
    if page is not None:
        try:
            res = page.call(_CHECK_JS, sels, timeout=15.0) or []
            res = [{"s": str(r.get("s")), "ok": bool(r.get("ok"))} for r in res]
        except Exception:
            res = [{"s": s, "ok": None} for s in sels]   # 页面不可测 → 未知
    elif present is not None:
        res = [{"s": s, "ok": s in present} for s in sels]
    else:
        res = [{"s": s, "ok": None} for s in sels]
    known = [r for r in res if r["ok"] is not None]
    misses = [r["s"] for r in res if r["ok"] is False]
    total = len(known)
    hits = total - len(misses)
    return {"total": len(sels), "checked": total,
            "hits": hits, "misses": misses,
            "rate": (round(hits / total, 3) if total else None)}


def _model_locate(plat, action, misses):
    """红线后的模型定位（唯一耗额度的一步；失败静默返回空串）。
    calls 只数真实发出的模型调用——没配编排者/调用异常都不计。"""
    hint, calls = "", 0
    try:
        from .. import modelhub
        orch = modelhub.resolve_orchestrator()
        if orch:
            prov, model = orch
            prompt = (
                "CodeBee 发布流程自检：%s 平台 %s 动作的以下 CSS 选择器在页面上"
                "全部未命中，怀疑平台改版：%s。请判断最可能的改版形态，并给出"
                "候选替代选择器。只输出 JSON 数组（字符串数组，可为空），不要"
                "任何解释。" % (plat, action,
                                 "、".join(m[:60] for m in misses[:8])))
            r = modelhub.chat(prov.get("id"), model, prompt,
                              max_tokens=400, timeout=60)
            calls = 1
            text = (r.get("text") or "") if r.get("ok") else ""
            start, end = text.find("["), text.rfind("]")
            if start >= 0 and end > start:
                arr = json.loads(text[start:end + 1])
                if isinstance(arr, list):
                    hint = "；".join(str(x)[:80] for x in arr[:5])
    except Exception:
        hint = ""
    return {"hint": hint, "calls": calls}


def health_check(plat, action, page=None, present=None, model_locate=True,
                 steps=None):
    """自检主入口。steps 可注入（测试）；缺省 load_flow(plat, action)。

    返回 {platform, action, source_note, total, checked, hits, misses, rate,
          red, model_calls, model_hint, recorded, generated_at}。
    red 判定：已知结果的选项里有未命中（fixture/真页模式）；离线审计不判红。"""
    if steps is None:
        from .publish_browser import load_flow
        steps = load_flow(plat, action)
    sels = steps_selectors(steps)
    rep = evaluate(page, sels, present=present)
    offline = present is None and page is None
    rep["red"] = bool(rep["misses"]) if not offline else False
    rep["model_calls"] = 0
    rep["model_hint"] = ""
    if rep["red"] and model_locate:
        loc = _model_locate(plat, action, rep["misses"])
        rep["model_calls"] = loc["calls"]
        rep["model_hint"] = loc["hint"]
    rep.update({"platform": str(plat or ""), "action": str(action or ""),
                "offline": offline,
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    rep["recorded"] = _record(rep)
    return rep


def sample(plat, action, page):
    """真页采样（P0-b 真页采样挂钩）：发布流程跑完后对还开着的页面做一次
    命中率自检并入台账。**零模型调用**（红线记录本身就是告警面，发布热路径
    不烧额度）、**完全 fail-open**（自检任何故障不许连累发布链）。"""
    try:
        return health_check(plat, action, page=page, model_locate=False)
    except Exception:
        return None


def _health_file():
    return paths.PUBLISH_DIR / "selector-health.jsonl"


def _record(rep):
    """红线/台账落盘（append-only；失败静默——自检永不连累发布链）。"""
    try:
        with _LOCK:
            paths.PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
            with open(_health_file(), "a", encoding="utf-8") as f:
                f.write(json.dumps(rep, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def latest(plat=None, action=None):
    """最近一次自检结果（可按平台/动作过滤）。无台账返回 None。"""
    try:
        with _LOCK:
            if not _health_file().exists():
                return None
            rows = []
            for line in _health_file().read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if plat and r.get("platform") != plat:
                    continue
                if action and r.get("action") != action:
                    continue
                rows.append(r)
            return rows[-1] if rows else None
    except Exception:
        return None
