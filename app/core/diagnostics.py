# -*- coding: utf-8 -*-
"""运行时不变量注册表：质量闸门从 pytest 测试期提升到运行时。

设计稿：docs/migration/01-defense-patterns.md §5F。
参考 dsh packages/runtime-diagnostics/invariants/src/index.ts：
  enabled/package_allowlist/package_blocklist 三过滤；失败抛 InvariantError 含 packageName。

Tutti 实施简化为单文件 + pytest 钩子（设计稿说明）；register(name, fn) 注册断言，
run_for(source, ctx) 在指定 source 下跑所有断言并返回失败列表。
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

log = logging.getLogger(__name__)


class InvariantError(Exception):
    def __init__(self, source: str, check_name: str, msg: str):
        super().__init__(f"[{source}/{check_name}] {msg}")
        self.source = source
        self.check_name = check_name


class InvariantRegistry:
    """按 source 分组的运行时断言注册表。

    source 是来源标识（如 "pipeline"/"store"），name 是断言名；
    fn(ctx) -> Optional[str] 返回 None 表示通过，返回字符串即失败消息。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._checks: dict = {}  # source -> {name: fn}

    def register(self, source: str, name: str, fn):
        with self._lock:
            self._checks.setdefault(source, {})[name] = fn
            log.debug("invariant registered: %s/%s", source, name)

    def unregister(self, source: str, name: str = None):
        """移除断言；name=None 时清空整个 source。"""
        with self._lock:
            if source not in self._checks:
                return
            if name is None:
                del self._checks[source]
            else:
                self._checks[source].pop(name, None)

    def run_for(self, source: str, ctx: dict, *, fail_fast: bool = False):
        """在 source 下跑所有断言。

        Returns:
            [(source, name, message), ...] 失败列表（空 = 全过）。

        Raises:
            InvariantError: fail_fast=True 且首个失败。
        """
        fails = []
        with self._lock:
            checks = dict(self._checks.get(source, {}))
        for name, fn in checks.items():
            try:
                msg = fn(ctx)
            except Exception as e:
                msg = f"check raised: {e!r}"
            if msg:
                fails.append((source, name, msg))
                log.warning("invariant FAIL: %s/%s: %s", source, name, msg)
                if fail_fast:
                    raise InvariantError(source, name, msg)
        return fails

    def list(self, source: Optional[str] = None):
        """列出已注册的 source/name；source=None 时全部。"""
        with self._lock:
            if source is None:
                return [(s, n) for s, m in self._checks.items() for n in m]
            return [(source, n) for n in self._checks.get(source, {})]


invariants = InvariantRegistry()  # 单例


# === 默认 invariants ===
def register_default_checks():
    """注册 Tutti 默认运行时断言。"""

    def review_no_all_fail_zero(ctx):
        """评审全部 0 分但 run 标 success → 不允许（已锁 test_quality_gates:62-75）。"""
        if (ctx.get("review_scores")
                and all(s == 0 for s in ctx["review_scores"])
                and ctx.get("final_status") == "success"):
            return "评审全部 0 分但 run 标 success"
        return None

    def step_count_consistency(ctx):
        """实际 step 数与预期不一致。"""
        if ctx.get("actual_steps") != ctx.get("expected_steps"):
            return f"step count {ctx.get('actual_steps')} != expected {ctx.get('expected_steps')}"
        return None

    invariants.register("pipeline", "review_no_all_fail_zero", review_no_all_fail_zero)
    invariants.register("pipeline", "step_count_consistency", step_count_consistency)