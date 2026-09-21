# -*- coding: utf-8 -*-
"""启动端口清场（用户拍板 2026-09-21）：端口被占时先清场再启动。

- **自家旧实例**（命令行含本包 main.py 完整路径，或 npm 包内 app/main.py）
  → 杀树（TerminateProcess 直杀）——升级/重启最常见的占用者就是没退干净
  的 CodeBee 自己；控制台进程对温和信号（WM_CLOSE）无反应，温和关不掉
  正是用户「旧进程太难杀」的痛点，所以自家实例直接强杀。
- **别人的进程** → 只报告占用者，不发送任何信号；启动流程不得替用户
  关闭可能正在开发中的服务。
- 系统/自身进程拒关（portscan 内置护栏）。
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess

from . import portscan, runner

log = logging.getLogger(__name__)

PS_EXE = "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"


def proc_cmdline(pid):
    """读进程命令行；失败返回空串。仅用于确认旧 CodeBee 实例身份。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return ""
    if pid <= 0:
        return ""
    if os.name != "nt":
        try:
            with open("/proc/%d/cmdline" % pid, "rb") as fh:
                return fh.read().decode("utf-8", "replace").replace("\x00", " ").strip()
        except Exception:
            return ""
    try:
        r = subprocess.run(
            [PS_EXE, "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter 'ProcessId = %d').CommandLine" % pid],
            capture_output=True, timeout=10)
        return r.stdout.decode("utf-8", "replace").strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _command_tokens(cmdline):
    try:
        return [token.strip().strip('"').strip("'")
                for token in shlex.split(str(cmdline or ""), posix=False)]
    except (TypeError, ValueError):
        return []


def is_own_instance(cmdline, main_script, port=None):
    """按启动器、脚本独立参数和端口精确确认 CodeBee 实例。"""
    tokens = _command_tokens(cmdline)
    if len(tokens) < 2:
        return False
    launcher = os.path.splitext(os.path.basename(tokens[0]))[0].lower()
    if launcher not in ("python", "python3", "py"):
        return False
    script = ""
    for token in tokens[1:]:
        if token.startswith("-"):
            continue
        script = token
        break
    if not script or not os.path.isabs(script):
        return False
    actual = os.path.normcase(os.path.normpath(os.path.abspath(script)))
    expected = os.path.normcase(os.path.normpath(os.path.abspath(str(main_script))))
    packaged = actual.replace("\\", "/").lower().endswith(
        "/node_modules/codebee/app/main.py")
    if actual != expected and not packaged:
        return False
    if port is None:
        return True
    try:
        wanted = str(int(port))
    except (TypeError, ValueError):
        return False
    explicit_port = None
    for i, token in enumerate(tokens):
        if token == "--port" and i + 1 < len(tokens):
            explicit_port = tokens[i + 1]
        elif token.startswith("--port="):
            explicit_port = token.split("=", 1)[1]
    # argparse 默认端口可不出现在旧实例参数中；非默认端口必须显式相符。
    return ((explicit_port == wanted) if explicit_port is not None
            else wanted == "8765")


def clear_stale_port(port, main_script):
    """清掉占用端口的进程。返回 (是否清掉, 人话说明)。"""
    holders = [p for p in portscan.listening_ports() if p.get("port") == int(port)]
    if not holders:
        return True, ""
    pid = int(holders[0].get("pid") or 0)
    if pid <= 4 or pid == os.getpid():
        return False, "占用者是系统进程/自身，拒绝清理"
    if is_own_instance(proc_cmdline(pid), main_script, port=port):
        try:
            runner._kill_tree(pid)
            return True, "已结束旧实例 PID %d" % pid
        except Exception as e:
            return False, "旧实例 PID %d 清理失败 %s" % (pid, str(e)[:80])
    return False, "占用者非 CodeBee（PID %d），未自动关闭" % pid
