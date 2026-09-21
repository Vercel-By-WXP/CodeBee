# -*- coding: utf-8 -*-
"""端口/进程扫描与项目归属推断（借鉴 leftopen 38★）。

三个机制：
- **端口→进程→项目归属推断**：netstat/ss 拿端口→PID，再从进程 CWD 向上走找
  .git/package.json 等项目根——知道该进程属于哪个项目/用户
- **本地 vs LAN 区分**：127.0.0.1 与 0.0.0.0/LAN 的安全边界
- **温和关闭、强杀兜底**：先 SIGTERM（Windows taskkill /PID 不带 /F）并
  验证端口真的释放；控制台/服务进程对 WM_CLOSE 无反应，温和无效升级
  强杀，终裁以端口释放为准（关闭前/强杀前都重验 PID）

跨平台（Windows netstat + PowerShell / POSIX ss + /proc）纯标准库。
"""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import time

log = logging.getLogger(__name__)

_PROJECT_MARKERS = (".git", "package.json", "pyproject.toml", "Cargo.toml",
                    "go.mod", "pom.xml", "build.gradle", ".codebee")
_SYSTEM_PROCS = {"system", "idle", "kernel", "svchost", "launchd", "init",
                 "systemd", "sshd", "explorer", "finder", "windowserver"}


def _project_from_cwd(cwd):
    """从 CWD 向上走到项目根（含标志文件的最深目录名）。"""
    if not cwd:
        return ""
    cur = os.path.abspath(cwd)
    origin = cur
    home = os.path.abspath(os.path.expanduser("~"))
    while cur and cur != os.path.dirname(cur):
        # 家目录及以上散落的标志文件（package.json 等）是环境噪音不是项目；
        # 只有进程就跑在家目录本身时才认它为归属。
        if cur == home and cur != origin:
            return ""
        for marker in _PROJECT_MARKERS:
            if os.path.exists(os.path.join(cur, marker)):
                return os.path.basename(cur)
        cur = os.path.dirname(cur)
    return ""


def _proc_detail(pid):
    """POSIX：读 /proc/<pid>/cwd 和 comm 获取进程详情。"""
    detail = {"name": "", "project": ""}
    try:
        cwd = os.readlink("/proc/%d/cwd" % pid)
        with open("/proc/%d/comm" % pid) as f:
            detail["name"] = f.read().strip()
        proj = _project_from_cwd(cwd)
        if proj:
            detail["project"] = proj
    except (OSError, PermissionError):
        pass
    return detail


def _parse_ss(output):
    """解析 ss/netstat -tlnp 输出为端口条目列表。"""
    ports, pid_map = [], {}
    for ln in output.splitlines():
        m = re.search(r":(\d{4,5})\s", ln)
        if not m:
            continue
        port = int(m.group(1))
        pm = re.search(r"pid=(\d+)", ln)
        pid = int(pm.group(1)) if pm else 0
        local_only = "127.0.0.1" in ln or "[::1]" in ln or "localhost" in ln
        if pid and pid not in pid_map:
            pid_map[pid] = _proc_detail(pid)
        ports.append({"port": port, "pid": pid, "local_only": local_only,
                      "process": pid_map.get(pid, {}).get("name", ""),
                      "project": pid_map.get(pid, {}).get("project", "")})
    dedup = {}
    for p in ports:
        key = p["port"]
        if key not in dedup or (p["pid"] and not dedup[key]["pid"]):
            dedup[key] = p
    return sorted(dedup.values(), key=lambda x: x["port"])


def _ports_linux():
    """Linux/macOS：ss 优先，netstat 兜底。"""
    try:
        proc = subprocess.run(["ss", "-tlnp"], capture_output=True, timeout=15)
        if proc.returncode == 0 and proc.stdout:
            return _parse_ss(proc.stdout.decode("utf-8", "replace"))
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        proc = subprocess.run(["netstat", "-tlnp"], capture_output=True, timeout=15)
        if proc.returncode == 0 and proc.stdout:
            return _parse_ss(proc.stdout.decode("utf-8", "replace"))
    except (OSError, subprocess.TimeoutExpired):
        pass
    return []


def _ports_windows(with_names=True):
    """Windows：netstat -ano -p TCP 拿端口，PowerShell 一次性补进程名。"""
    try:
        proc = subprocess.run(
            ["C:\\Windows\\System32\\netstat.exe", "-ano", "-p", "TCP"],
            capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return []
    ports = []
    for ln in proc.stdout.decode("utf-8", "replace").splitlines():
        parts = ln.split()
        if len(parts) < 5 or parts[0] != "TCP" or parts[3] != "LISTENING":
            continue
        local = parts[1]
        pid_str = parts[4]
        if not pid_str.isdigit():
            continue
        addr, _, port_str = local.rpartition(":")
        if not port_str.isdigit():
            continue
        ports.append({"port": int(port_str), "pid": int(pid_str),
                      "local_only": addr in ("127.0.0.1", "[::1]", "::1"),
                      "process": "", "project": ""})
    if with_names and ports:
        try:
            pn = subprocess.run(
                ["C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
                 "-NoProfile", "-Command",
                 "Get-Process | Select-Object Id,ProcessName | ConvertTo-Json -Compress"],
                capture_output=True, timeout=15)
            procs = json.loads(pn.stdout.decode("utf-8", "replace"))
            if isinstance(procs, dict):
                procs = [procs]
            name_map = {p.get("Id"): p.get("ProcessName", "") for p in procs}
            for p in ports:
                p["process"] = name_map.get(p["pid"], "")
        except Exception:
            pass
    dedup = {}
    for p in ports:
        dedup.setdefault(p["port"], p)
    return sorted(dedup.values(), key=lambda x: x["port"])


def listening_ports(with_names=True):
    """扫描本机所有 LISTEN 端口，返回 [{port, pid, process, project, local_only}]。

    project 从进程 CWD 推断项目根目录名（POSIX /proc 可得，Windows 留空）。
    with_names=False：跳过进程名/归属补全（Windows 下省掉 PowerShell 一次
    起跳，重验场景用），process/project 恒为空串。
    结果按端口号排序，重复端口去重（保留有 PID 信息的条目）。"""
    if os.name == "nt":
        return _ports_windows(with_names)
    return _ports_linux()


def _held_by(port, pid):
    """端口当前是否仍被指定 PID 监听（轻量扫描，不起 PowerShell）。"""
    return any(pp["port"] == int(port) and pp["pid"] == int(pid)
               for pp in listening_ports(with_names=False))


def _wait_release(port, pid, deadline_s):
    """轮询等待该 PID 释放端口；释放即 True，超时 False。"""
    end = time.time() + deadline_s
    while True:
        if not _held_by(port, pid):
            return True
        if time.time() >= end:
            return False
        time.sleep(0.3)


def _win_force_kill(pid):
    """Windows 强杀：TerminateProcess 直杀根进程（os.kill 的 SIGTERM 语义，
    毫秒级不挂），taskkill /F /T 短等待兜底扫子孙（同 runner._kill_tree 策略；
    taskkill 病态挂死时靠 timeout 放弃，不拖死调用方）。"""
    try:
        os.kill(pid, signal.SIGTERM)   # Windows 语义 = TerminateProcess
    except OSError:
        pass                           # 可能已被温和信号杀掉
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       timeout=8, capture_output=True)
    except Exception:
        pass                           # 兜底失败不阻塞终裁验证


def close_port(port):
    """关闭端口上的进程。返回 (ok, message)。

    温和优先（POSIX SIGTERM / Windows WM_CLOSE）并验证端口真的释放。控制台/
    服务进程对 WM_CLOSE 无反应，taskkill 不带 /F 对它们必败——2026-09-21
    用户实测「扫描端口后关不掉」根因，且旧实现不看返回码假报成功。因此：
    温和无效升级强杀，强杀前重验同一 PID 仍监听（防 PID 复用误杀），终裁
    以端口释放为准，绝不假报成功。"""
    for p in listening_ports():
        if p["port"] != port:
            continue
        pid = p.get("pid", 0)
        if not pid or pid <= 4:
            return False, "系统进程，不关闭"
        if pid == os.getpid():
            return False, "不能关闭自身服务进程"
        if p.get("process", "").lower() in _SYSTEM_PROCS:
            return False, "系统服务，不关闭"
        # 重验 PID 仍在监听该端口（防 PID 复用竞态）；轻量扫描省掉 PowerShell
        if not _held_by(port, pid):
            return False, "PID %d 已不在端口 %d 上监听（竞态）" % (pid, port)
        if os.name == "posix":
            try:
                os.kill(pid, 15)   # SIGTERM
            except OSError as e:
                return False, str(e)[:160]
        else:
            try:
                subprocess.run(["taskkill", "/PID", str(pid)],
                               timeout=10, capture_output=True)
            except Exception:
                pass               # 温和失败不在此定论，交给验证+升级
        if _wait_release(port, pid, 3):
            return True, "已关闭（PID %d），端口 %d 已释放" % (pid, port)
        # 温和无效（无窗口进程拒收 WM_CLOSE / 忽略 SIGTERM）→ 升级强杀
        if not _held_by(port, pid):
            return True, "已关闭（PID %d），端口 %d 已释放" % (pid, port)
        if os.name == "posix":
            try:
                os.kill(pid, 9)    # SIGKILL
            except OSError as e:
                return False, str(e)[:160]
        else:
            _win_force_kill(pid)
        if _wait_release(port, pid, 3):
            return True, "温和信号无效，已强制结束（PID %d），端口 %d 已释放" % (pid, port)
        return False, "PID %d 未响应关闭，端口 %d 仍被监听，请手动处理" % (pid, port)
    return False, "端口 %d 未找到监听进程" % port
