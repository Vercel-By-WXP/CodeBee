# -*- coding: utf-8 -*-
"""策略参数外置（Raven Evolver 借鉴·机制闸第一件）：data/strategy.json 热更。

目的：把散落在 router/history/benchstore 里的评分权重从「改代码+发版」变成
「改数据+即时生效」，并为冠军/挑战者影子对账（strategy_lab/router._shadow_note）
提供可覆写的权重字典。读取优先级（get）：

    调用方 overrides（挑战者打分）> 文件 weights > 模块常量（getattr 回退，
    保持现有测试 monkeypatch 模块常量的语义）> 规格内置默认。

设计约束：
- 独立模块：只 import paths/atomic；引用目标模块（router 等）仅在取默认值
  时 lazy import，杜绝 import 环。
- 坏文件/未知键不致命：回默认 + warning，绝不让路由因配置文件手滑而挂。
- 全键带钳位范围：写入钳位、读取也钳位（防绕过 UI 手改文件越界）。
- CAPABILITY 能力基线表刻意不外置（数据大而稳定，走代码演进）。
"""
from __future__ import annotations

import importlib
import json
import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

from . import atomic, paths

_LOCK = threading.RLock()
_FILE = paths.DATA_DIR / "strategy.json"
_ADOPT_HISTORY_MAX = 50

# 键规格：module/attr = 默认值来源（getattr，兼容 monkeypatch）；min/max = 钳位。
_KEYS: Dict[str, Dict[str, Any]] = {
    # router.py —— 会话粘性（prompt cache 亲和）
    "sticky_ttl_s": {"module": "app.core.router", "attr": "STICKY_TTL_S",
                     "min": 0, "max": 86400, "int": True},
    "sticky_bonus": {"module": "app.core.router", "attr": "STICKY_BONUS",
                     "min": -100.0, "max": 100.0},
    # router.py —— 连败冷却
    "streak_bench_min": {"module": "app.core.router", "attr": "STREAK_BENCH_MIN",
                         "min": 1, "max": 50, "int": True},
    "streak_bench_penalty": {"module": "app.core.router", "attr": "STREAK_BENCH_PENALTY",
                             "min": -500.0, "max": 0.0},
    # router.py —— 账号台账死号沉底
    "ledger_down_penalty": {"module": "app.core.router", "attr": "LEDGER_DOWN_PENALTY",
                            "min": -1000.0, "max": 0.0},
    # router.py —— 绑定链可用性
    "binding_empty_penalty": {"module": "app.core.router", "attr": "BINDING_EMPTY_PENALTY",
                              "min": -200.0, "max": 0.0},
    "binding_multi_bonus": {"module": "app.core.router", "attr": "BINDING_MULTI_BONUS",
                            "min": 0.0, "max": 100.0},
    # router.py —— 历史败率扣分封顶
    "history_loss_cap": {"module": "app.core.router", "attr": "HISTORY_LOSS_CAP",
                         "min": 0.0, "max": 100.0},
    # router.py —— 配额软罚封顶（正值，施加时取负）
    "quota_penalty_cap": {"module": "app.core.router", "attr": "QUOTA_PENALTY_CAP",
                          "min": 0.0, "max": 200.0},
    # history.py —— 连败统计窗
    "streak_window_s": {"module": "app.core.history", "attr": "STREAK_WINDOW_S",
                        "min": 60, "max": 30 * 86400, "int": True},
    # benchstore.py —— 实测分软信号口径
    "fresh_days": {"module": "app.core.benchstore", "attr": "FRESH_DAYS",
                   "min": 0, "max": 365},
    "bonus_baseline": {"module": "app.core.benchstore", "attr": "_BASELINE",
                       "min": 0.0, "max": 10.0},
    "bonus_k": {"module": "app.core.benchstore", "attr": "_K",
                "min": 0.0, "max": 10.0},
    "bonus_max": {"module": "app.core.benchstore", "attr": "_MAX_BONUS",
                  "min": 0.0, "max": 50.0},
}

# mtime 缓存：热更（文件变了立刻生效）但不重复解析。
_CACHE: Dict[str, Any] = {"mtime": None, "doc": {}}


def _clamp(key: str, value) -> Optional[float]:
    spec = _KEYS.get(key)
    if not spec:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    num = max(float(spec["min"]), min(float(spec["max"]), num))
    if spec.get("int"):
        return int(round(num))
    return round(num, 4)


def _default(key: str):
    spec = _KEYS[key]
    try:
        mod = importlib.import_module(spec["module"])
        value = getattr(mod, spec["attr"])
    except Exception:
        return spec["min"]
    clamped = _clamp(key, value)
    return spec["min"] if clamped is None else clamped


def _read_doc() -> Dict[str, Any]:
    with _LOCK:
        try:
            mtime = _FILE.stat().st_mtime_ns
        except OSError:
            _CACHE["mtime"], _CACHE["doc"] = None, {}
            return {}
        if mtime != _CACHE["mtime"]:
            try:
                doc = json.loads(_FILE.read_text(encoding="utf-8"))
                _CACHE["mtime"], _CACHE["doc"] = mtime, (doc if isinstance(doc, dict) else {})
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "strategy.json 解析失败，回退模块默认：%s", exc)
                _CACHE["mtime"], _CACHE["doc"] = mtime, {}
        return _CACHE["doc"]


def _write_doc(doc: Dict[str, Any]) -> None:
    doc["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    atomic.write_json(_FILE, doc)
    with _LOCK:
        _CACHE["mtime"], _CACHE["doc"] = None, {}


def get(key: str, overrides: Optional[Dict[str, Any]] = None):
    """取一个策略参数：overrides > 文件 weights > 模块常量 > 规格默认。"""
    if overrides:
        value = _clamp(key, overrides.get(key))
        if value is not None:
            return value
    weights = _read_doc().get("weights")
    if isinstance(weights, dict):
        value = _clamp(key, weights.get(key))
        if value is not None:
            return value
    return _default(key)


def effective_weights() -> Dict[str, float]:
    """当前生效权重（含文件覆写与钳位）——UI 展示与挑战者基线用。"""
    return {key: get(key) for key in sorted(_KEYS)}


def ranges() -> Dict[str, Dict[str, float]]:
    return {key: {"min": spec["min"], "max": spec["max"]} for key, spec in _KEYS.items()}


def raw_weights() -> Dict[str, Any]:
    weights = _read_doc().get("weights")
    return dict(weights) if isinstance(weights, dict) else {}


def challenger() -> Optional[Dict[str, Any]]:
    ch = _read_doc().get("challenger")
    return ch if isinstance(ch, dict) else None


def challenger_weights() -> Optional[Dict[str, Any]]:
    """挑战者完整权重字典（当前生效值 ⊕ 挑战者覆写）；未启用返回 None。"""
    ch = challenger()
    if not ch or not ch.get("enabled"):
        return None
    base = effective_weights()
    for key, value in (ch.get("weights") or {}).items():
        clamped = _clamp(key, value)
        if clamped is not None:
            base[key] = clamped
    return base


def set_challenger(name: str, patch: Optional[Dict[str, Any]] = None,
                   enabled: bool = True) -> Tuple[bool, str]:
    name = str(name or "").strip() or "challenger"
    if len(name) > 64:
        return False, "挑战者名称过长（≤64）"
    clean = {}
    for key, value in (patch or {}).items():
        if key not in _KEYS:
            return False, "未知策略键：%s" % key
        clamped = _clamp(key, value)
        if clamped is None:
            return False, "非法数值：%s=%r" % (key, value)
        clean[key] = clamped
    with _LOCK:
        doc = dict(_read_doc())
        doc["challenger"] = {"name": name, "enabled": bool(enabled),
                             "weights": clean,
                             "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        _write_doc(doc)
    return True, ""


def clear_challenger() -> None:
    with _LOCK:
        doc = dict(_read_doc())
        doc.pop("challenger", None)
        _write_doc(doc)


def adopt_challenger(summary: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """挑战者权重并入主权重 + 记采用史 + 关挑战者。仅由人工（API/UI）触发。"""
    with _LOCK:
        doc = dict(_read_doc())
        ch = doc.get("challenger")
        if not isinstance(ch, dict) or not ch.get("weights"):
            return False, "没有已配置的挑战者"
        weights = dict(doc.get("weights") or {})
        for key, value in ch["weights"].items():
            clamped = _clamp(key, value)
            if clamped is not None:
                weights[key] = clamped
        doc["weights"] = weights
        history = [h for h in (doc.get("adopt_history") or [])
                   if isinstance(h, dict)]
        history.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "name": ch.get("name") or "challenger",
                        "weights": dict(ch["weights"]),
                        "summary": summary or {}})
        doc["adopt_history"] = history[-_ADOPT_HISTORY_MAX:]
        doc.pop("challenger", None)
        _write_doc(doc)
    return True, ""


def update_weights(patch: Dict[str, Any]) -> Tuple[bool, str]:
    clean = {}
    for key, value in (patch or {}).items():
        if key not in _KEYS:
            return False, "未知策略键：%s" % key
        clamped = _clamp(key, value)
        if clamped is None:
            return False, "非法数值：%s=%r" % (key, value)
        clean[key] = clamped
    if not clean:
        return False, "没有可更新的键"
    with _LOCK:
        doc = dict(_read_doc())
        weights = dict(doc.get("weights") or {})
        weights.update(clean)
        doc["weights"] = weights
        _write_doc(doc)
    return True, ""


def reset_weights() -> None:
    with _LOCK:
        doc = dict(_read_doc())
        doc.pop("weights", None)
        _write_doc(doc)


def default_strategy() -> str:
    value = _read_doc().get("strategy_default")
    return str(value).strip().lower() if value else ""


def set_default_strategy(name: str) -> Tuple[bool, str]:
    name = str(name or "").strip().lower()
    if not name:
        return False, "策略名不能为空"
    with _LOCK:
        doc = dict(_read_doc())
        doc["strategy_default"] = name
        _write_doc(doc)
    return True, ""


def adopt_history() -> list:
    value = _read_doc().get("adopt_history")
    return [h for h in value if isinstance(h, dict)] if isinstance(value, list) else []


def doc() -> Dict[str, Any]:
    """API 视图：原始文件内容 + 生效值 + 范围（前端据此渲染与校验）。"""
    raw = _read_doc()
    return {
        "weights": effective_weights(),
        "raw_weights": raw_weights(),
        "ranges": ranges(),
        "challenger": challenger(),
        "challenger_effective": challenger_weights(),
        "strategy_default": default_strategy(),
        "adopt_history": adopt_history(),
        "updated_at": raw.get("updated_at") or "",
    }


__all__ = ["get", "effective_weights", "ranges", "raw_weights", "challenger",
           "challenger_weights", "set_challenger", "clear_challenger",
           "adopt_challenger", "update_weights", "reset_weights",
           "default_strategy", "set_default_strategy", "adopt_history", "doc"]
