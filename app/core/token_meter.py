# -*- coding: utf-8 -*-
"""Token 压力估算：按 run 累计 usage，给出「已用/容量」压力比。

设计稿：docs/migration/02-context-compaction.md §1C。
参考 dsh packages/llm/token-meter/src/index.ts：滑动窗口 + usage 锚点 + 失败重定价。

Tutti 简化实现：每 run 一个累计窗口（deque 有上限），usage 来自
runner._parse_codex_jsonl / _parse_claude_json 已解析的字段；
容量查 DEFAULT_CAPACITY 表（可被 data/model_capacity.json 覆盖）。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from pathlib import Path

log = logging.getLogger(__name__)

# 默认模型容量（token）。未命中 key 时用 "" 兜底行。
DEFAULT_CAPACITY = {
    "": 128_000,
    "gpt-5": 400_000,
    "gpt-5.5": 400_000,
    "deepseek-chat": 128_000,
    "deepseek-reasoner": 64_000,
    "qwen3-max": 256_000,
    "qwen3.8-flash": 256_000,
}

# 压缩触发阈值（设计稿 §1B 默认 0.8，可被 orchestration.json 覆盖）
DEFAULT_PRESSURE_THRESHOLD = 0.8

# 容量覆盖文件（热改立即生效）
_CAPACITY_FILE = Path(__file__).resolve().parents[2] / "data" / "model_capacity.json"


def _load_capacity():
    """默认表 + 可选覆盖文件合并。文件缺失/坏 JSON 静默用默认。"""
    cap = dict(DEFAULT_CAPACITY)
    try:
        if _CAPACITY_FILE.is_file():
            data = json.loads(_CAPACITY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cap.update({str(k): int(v) for k, v in data.items()})
    except Exception:
        pass
    return cap


class TokenMeter:
    """每 run 独立累计窗口；线程安全。"""

    def __init__(self, capacity=None, *, window_size=200):
        self._capacity_override = capacity
        self._window_size = window_size
        self._lock = threading.Lock()
        # run_id -> deque[(ts, input, output, reasoning, cached)]
        self._windows: dict = {}
        # run_id -> 最后一次见到的 model 名（pressure_ratio 未显式传 model 时用）
        self._last_model: dict = {}
        self._cap_cache = None
        self._cap_mtime = 0.0

    def _capacity(self):
        """容量表（带 mtime 缓存，热改 data/model_capacity.json 立即生效）。"""
        try:
            mtime = _CAPACITY_FILE.stat().st_mtime
        except OSError:
            mtime = 0.0
        if self._cap_cache is None or mtime != self._cap_mtime:
            if self._capacity_override is not None:
                self._cap_cache = dict(self._capacity_override)
            else:
                self._cap_cache = _load_capacity()
            self._cap_mtime = mtime
        return self._cap_cache

    def accumulate(self, run_id: str, usage, model: str = ""):
        """记录一次调用的 usage。usage 字段缺失按 0 处理；usage=None 跳过。

        cached 单独记录但不计入 used()——缓存读不占新一轮上下文压力
        （与 runner._parse_claude_json 的口径一致：cached 是省钱的部分）。
        """
        if not usage:
            return
        with self._lock:
            if model:
                self._last_model[run_id] = model
            window = self._windows.setdefault(run_id, deque(maxlen=self._window_size))
            window.append((
                time.time(),
                int(usage.get("input") or 0),
                int(usage.get("output") or 0),
                int(usage.get("reasoning") or 0),
                int(usage.get("cached") or 0),
            ))

    def used(self, run_id: str) -> int:
        """窗口内累计（input + output + reasoning），不含 cached。"""
        with self._lock:
            window = self._windows.get(run_id)
            if not window:
                return 0
            return sum(i + o + r for _, i, o, r, _ in window)

    def cached(self, run_id: str) -> int:
        with self._lock:
            window = self._windows.get(run_id)
            if not window:
                return 0
            return sum(c for _, _, _, _, c in window)

    def pressure_ratio(self, run_id: str, model: str = "") -> float:
        """已用 / 容量。model 未传时用该 run 最后一次见到的模型名。"""
        cap_table = self._capacity()
        with self._lock:
            m = model or self._last_model.get(run_id, "")
        cap = cap_table.get(m, cap_table.get("", 128_000))
        return self.used(run_id) / max(cap, 1)

    def reset(self, run_id: str):
        with self._lock:
            self._windows.pop(run_id, None)
            self._last_model.pop(run_id, None)


token_meter = TokenMeter()  # 单例