# -*- coding: utf-8 -*-
"""工作目录原子认领租约（2026-11-01，借鉴 Cumora 的原子工作认领原语）。

背景：同工作目录互斥原本只靠内存态（pipeline._workdir_blocker 扫 store 任务表），
两次被实案打穿——2026-09-23 禅道双单、2026-10-26 同 workdir 孪生 run 互斥没拦住。
内存互斥看不见的东西：
  1. 同机第二个 CodeBee 实例（生产 8765 + 测试实例、桌面端+服务）指向同一工作目录；
  2. 同实例内的竞态窗口（检查与起跑之间）。

本模块把「认领」做成显式原语：run 起跑前以 **独占创建**（O_CREAT|O_EXCL）写
`<workdir>/.codebee/workdir.lease.json`，拿到租约才开工。持有者靠心跳保活；
持有者 pid 已死或心跳超时视为陈旧，可被夺走。失败方走与内存互斥同款的排队回滚。

纪律：
- 绝不抛错、绝不挡主流程——租约是第二道闸，内存互斥仍是第一道；任何 I/O 异常
  都按「拿不到信息」放行（gate fail-open）。
- 不 import store/pipeline（避免环）；阻塞回滚由调用方（pipeline）做。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

# 心跳间隔与陈旧阈值：阈值须远大于心跳间隔（容忍系统休眠恢复、GC 停顿），
# 又不能大到「死进程的租约长时间挡路」。30s 心跳 / 150s 陈旧。
HEARTBEAT_S = 30.0
STALE_S = 150.0

LEASE_REL = os.path.join(".codebee", "workdir.lease.json")

_LOCK = threading.RLock()
_HEARTBEATS = {}   # run_id(str) -> threading.Event（stop 信号）


# ---------------------------------------------------------------- 基础工具

def norm_wd(workdir):
    return str(workdir or "").strip().rstrip("/\\")


def lease_path(workdir):
    wd = norm_wd(workdir)
    if not wd:
        return None
    return Path(wd) / LEASE_REL


def _now_f():
    return round(time.time(), 3)


def pid_alive(pid):
    """持有者进程是否还活着。Windows 下绝不能用 os.kill(pid, 0)——
    它对非 CTRL 信号会直接 TerminateProcess，会把人家杀了。用 ctypes。"""
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
            if not h:
                return False
            try:
                code = ctypes.c_ulong()
                if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                    return False
                return code.value == STILL_ACTIVE
            finally:
                k32.CloseHandle(h)
        except Exception:
            return True   # 探测失败按「可能活着」处理，宁可保守
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True


def _read(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write(path, data):
    """写入租约。先试独占创建（认领的原子性所在）；已存在时（夺走陈旧租约）
    才退化为原子覆盖。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except FileExistsError:
        from . import atomic
        atomic.write_json(path, data, indent=None)
        return True


# ---------------------------------------------------------------- 认领 / 心跳 / 释放

def acquire(workdir, run_id, task_id="", force=False, now=None):
    """认领租约。返回 {"ok": bool, "skipped": str|"" , "holder": dict|"", "stale": bool}

    - 无 workdir / 路径不可用：ok=True + skipped（gate fail-open，不挡主流程）；
    - 自己已持有：ok=True（幂等重入，顺带续心跳）；
    - 他人新鲜持有：ok=False + holder（调用方走排队回滚）；
    - 他人陈旧持有（心跳超时或 pid 已死）：夺走，ok=True + stale=True；
    - force=True：无视新鲜持有直接夺走（对应 run.force_start 人工逃生口）。
    """
    path = lease_path(workdir)
    if path is None:
        return {"ok": True, "skipped": "no_workdir", "holder": "", "stale": False}
    ts = _now_f() if now is None else float(now)
    me = {"run_id": str(run_id or ""), "task_id": str(task_id or ""),
          "pid": os.getpid(), "acquired_at": ts, "heartbeat_at": ts}
    with _LOCK:
        holder = _read(path)
        if holder and str(holder.get("run_id") or "") == me["run_id"]:
            # 幂等重入：续心跳即可
            holder.update(heartbeat_at=ts)
            try:
                _write(path, holder)
            except Exception:
                pass
            return {"ok": True, "skipped": "", "holder": "", "stale": False}
        if holder and not force:
            hb = float(holder.get("heartbeat_at") or holder.get("acquired_at") or 0)
            fresh = (ts - hb) <= STALE_S
            if fresh and pid_alive(holder.get("pid")):
                return {"ok": False, "skipped": "", "holder": holder, "stale": False}
        try:
            _write(path, me)
        except Exception as exc:
            # 写不进去（只读盘/路径非法）：fail-open，不挡主流程
            return {"ok": True, "skipped": "lease_io:%s" % type(exc).__name__,
                    "holder": "", "stale": False}
        return {"ok": True, "skipped": "",
                "stale": bool(holder), "holder": holder or ""}


def heartbeat(workdir, run_id, now=None):
    """续租。只有持有者本人能续；返回 bool。绝不抛错。"""
    path = lease_path(workdir)
    if path is None:
        return False
    with _LOCK:
        holder = _read(path)
        if not holder or str(holder.get("run_id") or "") != str(run_id or ""):
            return False
        holder["heartbeat_at"] = _now_f() if now is None else float(now)
        try:
            _write(path, holder)
            return True
        except Exception:
            return False


def release(workdir, run_id):
    """释放租约。只删自己持有的；没有租约/别人的租约一律不动。"""
    path = lease_path(workdir)
    if path is None:
        return False
    with _LOCK:
        holder = _read(path)
        if not holder or str(holder.get("run_id") or "") != str(run_id or ""):
            return False
        try:
            path.unlink()
            return True
        except OSError:
            return False


# ---------------------------------------------------------------- 心跳线程

def start_heartbeat(workdir, run_id):
    """给已认领的 run 起后台心跳线程（守护线程，30s 一拍）。重复调用幂等。"""
    rid = str(run_id or "")
    if not rid:
        return None
    with _LOCK:
        if rid in _HEARTBEATS:
            return _HEARTBEATS[rid]
        stop = threading.Event()
        _HEARTBEATS[rid] = stop

    def _loop():
        while not stop.wait(HEARTBEAT_S):
            if not heartbeat(workdir, rid):
                # 租约被夺（不该发生）：停止空转
                break

    t = threading.Thread(target=_loop, name="wd-lease-%s" % rid[:12], daemon=True)
    t.start()
    return stop


def stop_heartbeat(run_id):
    with _LOCK:
        stop = _HEARTBEATS.pop(str(run_id or ""), None)
    if stop is not None:
        stop.set()


# ---------------------------------------------------------------- 持有者视图（诊断/注意力面板用）

def holder_view(workdir):
    """读租约持有者信息（脱敏视图）。无租约返回 None。"""
    path = lease_path(workdir)
    if path is None:
        return None
    holder = _read(path)
    if not holder:
        return None
    hb = float(holder.get("heartbeat_at") or holder.get("acquired_at") or 0)
    stale = (time.time() - hb) > STALE_S or not pid_alive(holder.get("pid"))
    return {"run_id": holder.get("run_id") or "", "task_id": holder.get("task_id") or "",
            "pid": holder.get("pid") or 0, "acquired_at": holder.get("acquired_at") or 0,
            "heartbeat_at": hb, "stale": bool(stale)}
