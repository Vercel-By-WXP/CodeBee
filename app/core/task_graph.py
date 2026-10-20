"""Small standard-library helpers for dependency-aware task execution.

The orchestration pipeline can use these helpers without committing to a
particular executor.  Nodes are dictionaries with an ``id`` (or ``title``)
and optional ``depends_on`` list.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field


class DependencyError(ValueError):
    """Raised for unknown dependencies or cycles."""


def _node_id(node, index):
    value = node.get("id") or node.get("title") or "step-%d" % (index + 1)
    return str(value).strip() or "step-%d" % (index + 1)


def normalize(nodes):
    """Return copied nodes with stable ids and normalized dependency lists."""
    result = []
    ids = set()
    for index, raw in enumerate(nodes or ()):
        if not isinstance(raw, dict):
            continue
        node = dict(raw)
        ident = _node_id(node, index)
        if ident in ids:
            raise DependencyError("duplicate task id: %s" % ident)
        ids.add(ident)
        node["id"] = ident
        deps = node.get("depends_on") or []
        if isinstance(deps, str):
            deps = [deps]
        if not isinstance(deps, (list, tuple, set)):
            raise DependencyError("depends_on must be a list for %s" % ident)
        node["depends_on"] = [str(dep).strip() for dep in deps if str(dep).strip()]
        result.append(node)
    return result


def topological_order(nodes):
    """Return a deterministic topological ordering, or raise DependencyError."""
    items = normalize(nodes)
    by_id = {n["id"]: n for n in items}
    indegree = {ident: 0 for ident in by_id}
    outgoing = {ident: [] for ident in by_id}
    for node in items:
        for dep in node["depends_on"]:
            if dep not in by_id:
                raise DependencyError("unknown dependency %s for %s" % (dep, node["id"]))
            indegree[node["id"]] += 1
            outgoing[dep].append(node["id"])
    ready = sorted(ident for ident, count in indegree.items() if count == 0)
    order = []
    while ready:
        ident = ready.pop(0)
        order.append(by_id[ident])
        for child in sorted(outgoing[ident]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()
    if len(order) != len(items):
        raise DependencyError("cycle detected in task dependencies")
    return order


@dataclass
class Budget:
    """Task-level quota tracker. Zero/None means unlimited."""
    max_calls: int = 0
    max_duration: float = 0.0
    max_tokens: int = 0
    started_at: float = field(default_factory=time.monotonic)
    calls: int = 0
    tokens: int = 0

    def elapsed(self):
        return max(0.0, time.monotonic() - self.started_at)

    def check(self, *, calls=0, tokens=0):
        if self.max_calls and self.calls + calls > self.max_calls:
            return False, "max_calls exceeded"
        if self.max_tokens and self.tokens + tokens > self.max_tokens:
            return False, "max_tokens exceeded"
        if self.max_duration and self.elapsed() > self.max_duration:
            return False, "max_duration exceeded"
        return True, ""

    def consume(self, *, calls=1, tokens=0):
        ok, reason = self.check(calls=calls, tokens=tokens)
        if not ok:
            return False, reason
        self.calls += max(0, int(calls))
        self.tokens += max(0, int(tokens))
        return True, ""

    def snapshot(self):
        return {"max_calls": self.max_calls, "max_duration": self.max_duration,
                "max_tokens": self.max_tokens, "calls": self.calls,
                "tokens": self.tokens, "elapsed": round(self.elapsed(), 3)}


class CliConcurrency:
    """Per-CLI semaphore registry; context manager friendly and observable."""
    def __init__(self, limits=None, default=1):
        self.default = max(1, int(default))
        self._lock = threading.RLock()
        self._semaphores = {}
        self._limits = dict(limits or {})

    def _get(self, cli):
        name = str(cli or "default")
        with self._lock:
            if name not in self._semaphores:
                limit = max(1, int(self._limits.get(name, self.default)))
                self._semaphores[name] = threading.BoundedSemaphore(limit)
            return self._semaphores[name]

    def acquire(self, cli, timeout=None):
        return self._get(cli).acquire(timeout=timeout)

    def release(self, cli):
        self._get(cli).release()

    def run(self, cli, fn, *args, timeout=None, **kwargs):
        if not self.acquire(cli, timeout=timeout):
            return {"ok": False, "queued": True, "error": "CLI concurrency limit"}
        try:
            return fn(*args, **kwargs)
        finally:
            self.release(cli)


def run_dag(nodes, worker, *, max_workers=4, completed=None):
    """Execute ready nodes in parallel while respecting dependencies.

    ``worker(node, dependency_results)`` returns an arbitrary result. Each
    worker receives a stable mapping of its transitive dependency ancestors as
    they stood when its wave was scheduled; unrelated sibling results cannot
    leak into that input. Completed ids are skipped, which makes this suitable
    for checkpoint-based resume.
    """
    ordered = normalize(nodes)
    topo = topological_order(ordered)  # validates references/cycles
    by_id = {n["id"]: n for n in ordered}
    dependency_ancestors = {}
    for node in topo:
        ancestors = []
        seen_ancestors = set()
        for dep in node["depends_on"]:
            for ancestor in (*dependency_ancestors[dep], dep):
                if ancestor not in seen_ancestors:
                    seen_ancestors.add(ancestor)
                    ancestors.append(ancestor)
        dependency_ancestors[node["id"]] = tuple(ancestors)
    results = dict(completed or {})
    pending = set(by_id) - set(results)
    while pending:
        ready = sorted(i for i in pending
                       if all(dep in results for dep in by_id[i]["depends_on"]))
        if not ready:
            raise DependencyError("cycle or failed dependency prevents progress")
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(ready)))) as pool:
            futures = {
                pool.submit(
                    worker,
                    by_id[i],
                    {dep: results[dep] for dep in dependency_ancestors[i]},
                ): i
                for i in ready
            }
            for future, ident in ((f, futures[f]) for f in futures):
                results[ident] = future.result()
                pending.remove(ident)
    return results
