# -*- coding: utf-8 -*-
"""Surface 会话日志：只追加事件流 + surface 派生模型输入。

设计稿：docs/migration/02-context-compaction.md §1A。
参考 dsh packages/core/session/src/types.ts:222-271（SessionEvent）与
surface.ts:140-167（append 同步校验 + replace generation）。

核心不变量：
  1. 只追加：append 是唯一写入口，seq 单调，写入即落盘（JSONL）。
  2. 模型可见即已记录：模型输入一律由 derive_messages() 从日志折叠派生，
     不存在"日志外的模型输入"。
  3. surface replace：压缩通过 surface_op={"op":"replace",start,end} 把一段
     事件折叠为单条摘要消息，replace_generation 单调递增（1D 重试守门用）。
"""
from __future__ import annotations

import json
import threading
import time


class SessionEvent:
    __slots__ = ("seq", "type", "data", "ts", "surface_op", "turn_id")

    def __init__(self, seq, ev_type, data, ts=None, surface_op=None, turn_id=None):
        self.seq = seq
        self.type = ev_type
        self.data = data
        self.ts = ts if ts is not None else time.time()
        self.surface_op = surface_op
        self.turn_id = turn_id

    def to_dict(self):
        d = {"seq": self.seq, "type": self.type, "data": self.data, "ts": self.ts}
        if self.surface_op is not None:
            d["surface_op"] = self.surface_op
        if self.turn_id:
            d["turn_id"] = self.turn_id
        return d

    @classmethod
    def from_dict(cls, d):
        return cls(d["seq"], d["type"], d.get("data") or {},
                   ts=d.get("ts"), surface_op=d.get("surface_op"),
                   turn_id=d.get("turn_id"))


# 进入模型 surface 的事件类型（其余仅记录，不派生）
_SURFACE_TYPES = ("system_message", "user_message", "assistant_message")


class Session:
    """一次 run 的会话日志。线程安全；落盘路径缺省不持久化（仅内存）。"""

    def __init__(self, run_id: str, store_path=None):
        self.run_id = run_id
        self.store_path = str(store_path) if store_path else None
        self._lock = threading.Lock()
        self._events = []
        self._seq = 0
        self._replace_generation = 0
        # surface 视图：list[SessionEvent]（已折叠）；惰性重建
        self._surface = []
        self._surface_dirty = True
        if self.store_path:
            self._load()

    # ---- 写入 ----

    def append(self, ev_type, data, *, surface_op=None, turn_id=None):
        """追加事件。surface_op:
          None                        -> 追加进 surface（若是 surface 类型）
          "shadow"                    -> 仅记录，不进 surface（审计/中间产物）
          {"op":"replace","start_seq":N,"end_seq":M} -> 把 [N,M] 折叠为本条
        """
        with self._lock:
            self._seq += 1
            ev = SessionEvent(self._seq, ev_type, data,
                              surface_op=surface_op, turn_id=turn_id)
            self._events.append(ev)
            if isinstance(surface_op, dict) and surface_op.get("op") == "replace":
                self._replace_generation += 1
            self._surface_dirty = True
            self._persist_one(ev)
            return ev

    # ---- 派生 ----

    def _fold(self):
        """按事件序重放 surface。replace 折叠 [start_seq, end_seq] 区间为该条摘要。"""
        surface = []  # list[SessionEvent]
        for ev in self._events:
            op = ev.surface_op
            if op == "shadow":
                continue
            if isinstance(op, dict) and op.get("op") == "replace":
                start, end = op["start_seq"], op["end_seq"]
                surface = [e for e in surface if not (start <= e.seq <= end)]
                surface.append(ev)
                continue
            if ev.type in _SURFACE_TYPES:
                surface.append(ev)
        return surface

    def derive_messages(self):
        """模型输入消息列表 [{role, content}]。system 节点去重合并（保留最后一条同 role）。"""
        with self._lock:
            if self._surface_dirty:
                self._surface = self._fold()
                self._surface_dirty = False
            snap = list(self._surface)
        msgs = []
        for ev in snap:
            role = "system" if ev.type == "system_message" else (
                "assistant" if ev.type == "assistant_message" else "user")
            msgs.append({"role": role, "content": str(ev.data.get("content", "")),
                         "seq": ev.seq})
        # 同 role 连续合并（CLI 提示词偏好单条 system/user）
        merged = []
        for m in msgs:
            if merged and merged[-1]["role"] == m["role"]:
                merged[-1]["content"] += "\n\n" + m["content"]
                merged[-1]["seq"] = m["seq"]
            else:
                merged.append(dict(m))
        return merged

    def replace_generation(self):
        with self._lock:
            return self._replace_generation

    def events(self):
        with self._lock:
            return list(self._events)

    # ---- 持久化 ----

    def _persist_one(self, ev):
        if not self.store_path:
            return
        with open(self.store_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")

    def _load(self):
        """启动恢复：逐行读 JSONL，坏行跳过（lazy skip）。"""
        try:
            with open(self.store_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line.startswith("{"):
                        continue
                    try:
                        self._events.append(SessionEvent.from_dict(json.loads(line)))
                    except Exception:
                        continue
            if self._events:
                self._seq = max(e.seq for e in self._events)
                self._replace_generation = sum(
                    1 for e in self._events
                    if isinstance(e.surface_op, dict) and e.surface_op.get("op") == "replace")
        except OSError:
            pass