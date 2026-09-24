"""工作目录成品扫描：剪枝遍历 + 短 TTL 缓存 + 在飞合并。

历史实现用 Path.rglob("*") 全量展开再逐文件 is_file()/stat() 过滤，几万文件的
工作目录单次要 1.6~4 秒；而详情页/侧栏在任务运行中是轮询调用——每次轮询都是
一次全仓扫描，UI 整体被拖卡（2026-09-24 详情页卡顿案）。这里改为 os.walk
剪枝（跳过类目录整个不进入），并给重复轮询加 (workdir, t0, 任务状态) 键的短 TTL 缓存
与在飞合并：并发同键只扫一次，后到者等结果。
"""
from __future__ import annotations

import os
import threading
import time

# 过滤语义与历史 rglob 版逐条对齐：
# - SKIP_DIRS：任意路径段（含文件名本身）命中即剔除；
# - BUILD_DIRS：仅祖先目录命中即剔除（文件名叫 dist 不受影响）；
# - 点开头：仅祖先目录算「隐藏」（根下的 .gitignore 这类隐藏文件仍是候选）。
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode"}

# 构建产物/依赖缓存目录：里面的文件是工具链再生成的，不是任务成品
BUILD_DIRS = {
    "target", "build", "dist", "out", "bin", "obj",           # 通用构建输出
    "surefire", "failsafe-reports", "test-output", "reports",  # 测试/报告输出
    ".next", ".nuxt", ".output", ".gradle", ".gradle-home",    # 前端/Gradle
    "__MACOSX",
}

# CLI 自身的历史/评审中间件不属于用户成果。它们通常落在工作目录根部，
# 只按隐藏目录过滤会漏掉 Aider 的 .aider.* 与流程生成的 *_review.json。
_PROCESS_ARTIFACT_NAMES = {
    ".aider.chat.history.md", ".aider.input.history", ".aider.input.history.md",
    ".aider.tags.cache.v4", ".aider.tags.cache.v3", ".aider.conf.yml",
}
_PROCESS_ARTIFACT_SUFFIXES = ("_review.json", ".review.json")
_PROCESS_ARTIFACT_PREFIXES = ("tutti_prompt_", "_tutti_prompt_")

SCAN_CAP = 800             # 防超大目录拖垮接口；截断后再排序取最新
CACHE_TTL_RUNNING = 2.0    # 任务在跑：轮询热，新鲜期要短
CACHE_TTL_IDLE = 10.0      # 终态任务：详情反复开关/检查器同刷，可稍长
_CACHE_KEEP = 128          # 缓存条目上限（按时间淘汰最旧）

_now = time.monotonic      # 模块级别名，测试可替换时钟

_LOCK = threading.Lock()
_CACHE = {}     # (root, t0, running) -> (时间戳, files)；files 恒为全量候选
_INFLIGHT = {}  # (root, t0, running) -> Lock；同键并发只跑一次扫描


def _is_process_artifact(rel_name):
    """判断工作目录文件是否为 CLI/评审过程产物，而非可交付成果。"""
    name = str(rel_name or "").replace("\\", "/")
    base = name.rsplit("/", 1)[-1].lower()
    if base in _PROCESS_ARTIFACT_NAMES:
        return True
    if base.startswith(".") and base.startswith(".aider"):
        return True
    if base.endswith(_PROCESS_ARTIFACT_SUFFIXES):
        return True
    if base.startswith(_PROCESS_ARTIFACT_PREFIXES):
        return True
    return False


def _walk_files(root, t0):
    """剪枝遍历：跳过类目录整个不进入；返回 mtime>=t0 的候选（无序）。"""
    files = []
    for dp, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and d not in BUILD_DIRS
                       and not d.startswith(".")]
        done = False
        for name in filenames:
            if name in SKIP_DIRS:   # 历史语义：文件名本身命中也剔除
                continue
            try:
                st = os.stat(os.path.join(dp, name))
            except OSError:
                continue
            if st.st_mtime < t0:
                continue
            rel_dir = os.path.relpath(dp, root)
            rel = name if rel_dir == "." else \
                rel_dir.replace("\\", "/") + "/" + name
            if _is_process_artifact(rel):
                continue
            files.append({"name": rel,
                          "size": st.st_size, "mtime": int(st.st_mtime)})
            if len(files) >= SCAN_CAP:
                done = True
                break
        if done:
            break
    return files


def _cache_get(key, ttl):
    with _LOCK:
        ent = _CACHE.get(key)
        if ent and _now() - ent[0] < ttl:
            return ent[1]
    return None


def _cache_put(key, files):
    with _LOCK:
        _CACHE[key] = (_now(), files)
        while len(_CACHE) > _CACHE_KEEP:
            _CACHE.pop(min(_CACHE, key=lambda k: _CACHE[k][0]), None)


def _inflight_lock(key):
    with _LOCK:
        lock = _INFLIGHT.get(key)
        if lock is None:
            lock = _INFLIGHT[key] = threading.Lock()
        return lock


def scan(root, t0, limit=200, running=False, cache=False):
    """扫工作目录成品，按 mtime 新→旧取前 limit 条，返回文件字典列表。

    cache=True 时同键 (root, t0, running) 在 TTL 内直接复用上次结果，运行中
    任务用短 TTL；无论是否缓存，同键在飞扫描都会合并——轮询方只会等一次扫描。
    """
    # 运行态和终态必须分开缓存：任务刚结束时不能沿用运行中缓存里的旧
    # 文件列表，否则终态较长 TTL 会把最后写入的产物遮住。
    key = (str(root), t0, bool(running))
    ttl = CACHE_TTL_RUNNING if running else CACHE_TTL_IDLE
    if cache:
        hit = _cache_get(key, ttl)
        if hit is not None:
            return hit[:limit]
    klock = _inflight_lock(key)
    try:
        with klock:
            if cache:   # 排到锁时他人可能已扫完入缓存
                hit = _cache_get(key, ttl)
                if hit is not None:
                    return hit[:limit]
            files = sorted(_walk_files(root, t0), key=lambda f: -f["mtime"])
            if cache:
                _cache_put(key, files)
    finally:
        with _LOCK:
            if _INFLIGHT.get(key) is klock:
                _INFLIGHT.pop(key, None)
    return files[:limit]
