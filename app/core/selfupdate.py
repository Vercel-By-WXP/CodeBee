# -*- coding: utf-8 -*-
"""Tutti 自更新：版本读取 / 新版本检测 / 一键升级 / 就地重启。

安装模式判定（mode）：
  npm   — 运行副本位于 node_modules 下且带 package.json（npm 全局安装的包），
          可查新、可升级、可重启；
  repo  — 带 .git 的开发仓库：**永不自动升级**（会覆盖开发中的代码），提示走 git pull；
  other — 裸源码拷贝，提示手动替换。

升级 = 在标准 mgmt run 里跑 `npm install -g codebee@latest`（日志实时落盘、
SSE 可看进度）。npm 替换的是包目录文件，当前进程已加载进内存不受影响，装完后由
「重启」换新代码：新进程先等旧端口释放再 bind（Windows SO_REUSEADDR 允许双 LISTEN
同时存在，必须先验旧进程真退了），旧进程发送完重启响应后自退。

安全：两处子进程命令的 argv 均为**行内字面量列表**（可执行文件与全部参数不来自任何
外部输入；重启仅透传 argparse 校验过的整型端口），shell 全程 False，绝不拼接用户输入。
改发布名时（见 test_selfupdate 与 package.json 的一致性断言）同步改 _PKG_NAME。
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time

from . import paths, runner

_PKG_NAME = "codebee"   # npm 发布名；必须与 package.json 的 name 一致（单测断言）
_UPDATE_TTL = 600   # 查新结果缓存（秒）
_LOCK = threading.Lock()
_CHECK_CACHE = {"ts": 0.0, "result": None}


def package_version():
    """读本包 package.json 的 version；读不到返回 ''（开发仓库未同步版本号时）。"""
    try:
        pj = (paths.ROOT / "package.json").resolve()
        return str(json.loads(pj.read_text(encoding="utf-8")).get("version") or "")
    except Exception:
        return ""


def install_mode():
    """npm / repo / source / other（见模块 docstring）。"""
    try:
        parts = [p.lower() for p in paths.ROOT.resolve().parts]
    except Exception:
        return "other"
    has_pkg = (paths.ROOT / "package.json").is_file()
    if "node_modules" in parts and has_pkg:
        return "npm"
    if (paths.ROOT / ".git").exists():
        return "repo"
    return "source" if has_pkg else "other"


def _ver_tuple(s):
    return [int(x) for x in re.findall(r"\d+", str(s or ""))[:4]]


_RELNOTES_RE = re.compile(
    r"<!--\s*relnotes:start\s*-->(.*?)<!--\s*relnotes:end\s*-->", re.S)


def _relnotes(readme):
    """README 里 relnotes 标记段的内容（发布前手工更新，见 CHANGELOG.md 头部说明）。"""
    m = _RELNOTES_RE.search(str(readme or ""))
    return m.group(1).strip() if m else ""


def _npm_meta():
    """npm view <pkg> --json；返回 (latest, relnotes, err)。

    relnotes 来自 registry 元数据里的 README（与查新同一条 npm 通道，不依赖
    GitHub 连通性），展示「新版本更新内容」用。部分 npm 版本 --json 不带
    readme 字段，此时回退到 `npm view <pkg> readme` 纯文本再提取。"""
    r = runner.run_process(
        argv=["cmd", "/c", "npm", "view", _PKG_NAME, "--json"], timeout=60)
    if not r["ok"]:
        return "", "", (r["stderr"] or r["stdout"] or "")[-200:] or "npm 命令失败"
    ver, notes = "", ""
    try:
        meta = json.loads(r["stdout"] or "{}")
        if isinstance(meta, dict):
            ver = str(meta.get("version") or "")
            notes = _relnotes(meta.get("readme") or "")
    except Exception:
        pass
    if not ver:  # 旧 npm --json 失败时退回纯文本解析
        m = re.search(r"\d+\.\d+\.\d+[\w.\-]*", r["stdout"] or "")
        ver = m.group(0) if m else ""
    if ver and not notes:
        r2 = runner.run_process(
            argv=["cmd", "/c", "npm", "view", _PKG_NAME, "readme"], timeout=60)
        if r2["ok"]:
            notes = _relnotes(r2["stdout"] or "")
    return ver, notes, ("" if ver else "npm 输出无法解析")


def changelog_section(version):
    """本地 CHANGELOG.md 中 version 对应小节（升级重启后「本次更新内容」用）。"""
    v = re.escape(str(version or "").lstrip("v"))
    m = re.search(r"(?ms)^## v?%s\b[^\n]*$(.*?)(?=^## |\Z)" % v,
                  _read_changelog())
    if not m:
        return ""
    body = m.group(0).strip()
    return body


def _read_changelog():
    try:
        return (paths.ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    except Exception:
        return ""


def check(force=False):
    """GET /api/selfupdate 载荷：{mode, current, latest, has_update, note,
    notes, whatsnew}。notes=远端新版本的更新内容（npm readme relnotes 段）；
    whatsnew=当前版本在本地 CHANGELOG.md 的小节（升级重启后弹「本次更新内容」）。"""
    mode = install_mode()
    cur = package_version()
    out = {"mode": mode, "current": cur, "latest": "", "has_update": False,
           "note": "", "notes": "", "whatsnew": changelog_section(cur)}
    if mode != "npm":
        out["note"] = ("开发仓库模式：请用 git pull 更新（自动升级会覆盖未提交的代码）"
                       if mode == "repo" else "非 npm 安装，无法自动更新")
        return out
    with _LOCK:
        if not force and _CHECK_CACHE["result"] and \
                time.time() - _CHECK_CACHE["ts"] < _UPDATE_TTL:
            return dict(_CHECK_CACHE["result"])
    latest, notes, err = _npm_meta()
    out["latest"] = latest
    out["notes"] = notes
    if err:
        out["note"] = "查询新版本失败：" + err
    elif latest and cur:
        out["has_update"] = _ver_tuple(latest) > _ver_tuple(cur)
    else:
        out["note"] = "无法比较版本（本地或 registry 版本号缺失）"
    with _LOCK:
        _CHECK_CACHE["ts"] = time.time()
        _CHECK_CACHE["result"] = dict(out)
    return out


def apply_upgrade():
    """发起升级：建 mgmt run 异步跑 npm install -g @latest。返回 {run_id} 或 {error}。"""
    if install_mode() != "npm":
        return {"error": "当前安装方式不支持自动升级（见版本页说明）"}
    from . import store, jobs
    run = store.create_run("mgmt", "升级 CodeBee 本体（npm install -g %s@latest）" % _PKG_NAME,
                           entry_id="__self__", op="selfupgrade")
    jobs.enqueue({"kind": "selfupgrade", "run_id": run["id"]})
    return {"run_id": run["id"]}


def run_upgrade(run_id, log_path):
    """worker 线程里执行升级命令（run/step 生命周期由 jobs 层管）。"""
    res = runner.run_process(
        argv=["cmd", "/c", "npm", "install", "-g", _PKG_NAME + "@latest"],
        cwd=str(paths.ROOT), timeout=900, log_path=log_path)
    if res["ok"]:
        with _LOCK:  # 装完即过期查新缓存，重启后自然拿到新版本
            _CHECK_CACHE["result"] = None
    return {"ok": res["ok"], "exit_code": res["exit_code"],
            "error": "" if res["ok"] else (res["stderr"][-800:] or "退出码 %s" % res["exit_code"])}


def _port_free(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", int(port))) != 0
    except Exception:
        return True
    finally:
        s.close()


def wait_port_before_bind(port):
    """新进程入口（--wait-port）：轮询直到旧实例释放端口（最多 ~30 秒）。
    超时也放行——旧进程若没退，bind 失败自会报错，不会出现双实例串流。"""
    for _ in range(60):
        if _port_free(port):
            return
        time.sleep(0.5)


def relaunch(port):
    """就地重启：拉起新实例（--wait-port 等新端口可 bind 时旧实例已自退）。
    调用方发送完重启响应后应 self_quit()。port 经 int() 强校验，argv 全字面量。"""
    port = int(port)
    if port < 1 or port > 65535:
        return False
    subprocess.Popen(
        [sys.executable, "main.py", "--port", str(port),
         "--wait-port", "--no-browser"],
        cwd=str(paths.APP_DIR),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=(0x00000008 | 0x00000200) if os.name == "nt" else 0)
    return True


def self_quit():
    """旧进程体面自退（HTTP 响应已发出后调用）。"""
    try:
        from . import remote
        remote.stop_quick_tunnel()
    except Exception:
        pass
    os._exit(0)
