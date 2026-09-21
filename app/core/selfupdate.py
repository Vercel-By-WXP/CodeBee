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
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import paths, runner

log = logging.getLogger(__name__)

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


def _npm_argv(*args):
    """npm 命令 argv：Windows 的 npm 是 .cmd 垫片须经 cmd /c；POSIX 直接跑。"""
    if os.name == "nt":
        return ["cmd", "/c", "npm"] + list(args)
    return ["npm"] + list(args)


def _npm_meta():
    """npm view <pkg> --json；返回 (latest, relnotes, err)。

    relnotes 来自 registry 元数据里的 README（与查新同一条 npm 通道，不依赖
    GitHub 连通性），展示「新版本更新内容」用。部分 npm 版本 --json 不带
    readme 字段，此时回退到 `npm view <pkg> readme` 纯文本再提取。"""
    r = runner.run_process(
        argv=_npm_argv("view", _PKG_NAME, "--json"), timeout=60)
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
            argv=_npm_argv("view", _PKG_NAME, "readme"), timeout=60)
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


_PENDING_PORT = None   # apply_upgrade 记下的服务端口，升级成功后自动重启用


def apply_upgrade(port=None):
    """发起升级：建 mgmt run 异步跑 npm install -g @latest。返回 {run_id} 或 {error}。

    port=服务端口：升级成功且版本真变时会自动就地重启（用户拍板 2026-09-21：
    升级完不该再要求手动点「重启服务生效」——旧进程滞留是 unknown api/界面
    闪烁/老宠物一类「升级了没生效」事故的总根子）。拿不到端口或运行环境不
    具备时自动跳过，回落版本页的手动重启按钮。"""
    global _PENDING_PORT
    try:
        _PENDING_PORT = int(port) if port else None
    except (TypeError, ValueError):
        _PENDING_PORT = None
    if install_mode() != "npm":
        return {"error": "当前安装方式不支持自动升级（见版本页说明）"}
    from . import store, jobs
    capacity = jobs.capacity_status()
    if not capacity["accepting"]:
        if capacity["restarting"]:
            return {"error": "服务正在完成升级重启，请稍后再试"}
        return {"error": "当前有 %d 个任务运行，已达并发保护上限（%d）；"
                         "升级未启动且不会排队，请在任务结束后重试"
                         % (capacity["active"], capacity["limit"])}
    try:
        run = store.create_run(
            "mgmt", "升级 CodeBee 本体（npm install -g %s@latest）" % _PKG_NAME,
            entry_id="__self__", op="selfupgrade")
    except Exception:
        log.exception("selfupdate: 创建升级运行记录失败")
        return {"error": "升级任务创建失败，请稍后重试"}
    try:
        jobs.enqueue({"kind": "selfupgrade", "run_id": run["id"]})
    except Exception as exc:
        # run 已持久化；启动失败时显式收口，版本页不能停在误导性的待启动状态。
        if isinstance(exc, jobs.JobsBusyError):
            message = "升级未启动：%s" % str(exc)
            log.warning("selfupdate: 升级任务因并发满载未启动 run=%s: %s",
                        run["id"], exc)
        else:
            log.exception("selfupdate: 升级任务启动失败 run=%s", run["id"])
            # 仅并发保护类错误可直接展示；其他内部异常继续隐藏实现细节。
            message = "升级任务启动失败，本次未排队，请稍后重试"
        try:
            store.update_run(run["id"], status="failed",
                             error=message,
                             ended_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        except Exception:
            log.exception("selfupdate: 升级运行失败收口失败 run=%s", run["id"])
        return {"error": message, "run_id": run["id"]}
    return {"run_id": run["id"]}


_LOCKED_RE = re.compile(r"\b(EBUSY|EPERM)\b")
_RETRY_DELAYS = (5, 15)   # 目录被占用时自动重试前的等待秒数（暂时性占用多在此窗口内释放）


def _locked_error(res):
    """npm 失败输出是否为「包目录被占用」类（EBUSY/EPERM）——值得等一等重试。"""
    blob = ((res or {}).get("stderr") or "") + ((res or {}).get("stdout") or "")
    return bool(_LOCKED_RE.search(blob[-4000:]))


def _log_note(log_path, text):
    """向步骤日志追加一行进度说明（run_process 以 append 模式写同一文件）。"""
    if not log_path:
        return
    try:
        with open(log_path, "ab") as fh:
            fh.write(("\n===== %s =====\n" % text).encode("utf-8", "replace"))
    except Exception:
        pass


def _maybe_auto_relaunch(old_pkg, log_path):
    """升级成功后的自动重启（三道守卫，任一不满足就回落手动按钮）：
    ①知道服务端口（apply_upgrade 传入）；②版本真的变了（同版本重装不折腾）；
    ③没有用户任务在跑（jobs._alive 只剩本升级任务自己）——正在干活的任务
    不能被升级重启打断，此时留给用户挑自己合适的时间手动重启。"""
    import threading
    from . import jobs
    if not _PENDING_PORT:
        _log_note(log_path, "未记录服务端口，跳过自动重启——请在版本页手动重启生效")
        return
    new_pkg = package_version()
    if not old_pkg or new_pkg == old_pkg:
        _log_note(log_path, "版本未变化（%s），无需重启" % (new_pkg or "?"))
        return
    if getattr(jobs, "_alive", 0) > 1:
        _log_note(log_path, "检测到还有 %d 个任务在运行，不自动重启——"
                  "完成后请在版本页手动点「重启服务生效」" % (jobs._alive - 1))
        return
    port = _PENDING_PORT

    def _go():
        drain_started = False
        try:
            time.sleep(3.0)   # 留出日志收尾/浏览器看到「升级完成」的窗口
            drain_started = jobs.begin_restart_drain()
            if not drain_started:
                _log_note(log_path, "延时窗口内有新任务进入，不自动重启——"
                          "完成后请在版本页手动点「重启服务生效」")
                return
            _log_note(log_path, "自动重启服务以应用新版本 %s …" % new_pkg)
            if relaunch(port):
                self_quit()
        except Exception:
            log.exception("selfupdate: 自动重启失败，请在版本页手动重启")
        finally:
            # 正常 self_quit 会直接结束进程；若拉起失败、异常或测试替身返回，必须
            # 释放停止接单闸，避免当前实例永久拒绝新任务。
            if drain_started:
                jobs.cancel_restart_drain()
    threading.Thread(target=_go, name="selfupdate-relaunch",
                     daemon=True).start()


def run_upgrade(run_id, log_path, cancel_event=None):
    """worker 线程里执行升级命令（run/step 生命周期由 jobs 层管）。

    包目录被其他进程占用（EBUSY/EPERM：打开包目录的资源管理器/终端窗口、
    杀毒或索引扫描）是升级失败的最常见原因，且多为暂时性——自动重试
    _RETRY_DELAYS 轮，仍败则给人话结论（原始 npm 输出在步骤日志里可查）。
    成功且版本真变时自动重启服务（_maybe_auto_relaunch，守卫见其 docstring）。"""
    old_pkg = package_version()
    res = {}
    for attempt, delay in enumerate((0,) + _RETRY_DELAYS):
        if cancel_event is not None and cancel_event.is_set():
            return {"ok": False, "exit_code": None, "error": "用户主动取消",
                    "cancelled": True}
        if delay:
            _log_note(log_path, "目录被占用（EBUSY/EPERM），%d 秒后自动重试（第 %d/%d 次）"
                      % (delay, attempt, len(_RETRY_DELAYS)))
            if cancel_event is not None and cancel_event.wait(delay):
                return {"ok": False, "exit_code": None, "error": "用户主动取消",
                        "cancelled": True}
            if cancel_event is None:
                time.sleep(delay)
        res = runner.run_process(
            argv=_npm_argv("install", "-g", _PKG_NAME + "@latest"),
            # Windows 上 npm 换版本靠把包目录整体改名（codebee → .codebee-xxx）；
            # cwd 若落在本包内，目录被自身进程占用，rename 必报 EBUSY——钉在包外
            cwd=str(Path.home()), timeout=900, log_path=log_path,
            cancel_event=cancel_event)
        if res.get("cancelled"):
            return {"ok": False, "exit_code": res.get("exit_code"),
                    "error": "用户主动取消", "cancelled": True}
        if res["ok"] or not _locked_error(res):
            break
    if res["ok"]:
        with _LOCK:  # 装完即过期查新缓存，重启后自然拿到新版本
            _CHECK_CACHE["result"] = None
        _maybe_auto_relaunch(old_pkg, log_path)
        return {"ok": True, "exit_code": res["exit_code"], "error": ""}
    stderr = res["stderr"] or ""
    if _locked_error(res):
        err = ("升级失败：codebee 安装目录被其他程序占用（已自动重试 %d 次未恢复）。"
               "常见占用：打开包目录的资源管理器窗口/终端、杀毒或索引扫描。"
               "请关闭相关窗口后回版本页重试；仍不行可退出 CodeBee 后手动执行 "
               "npm install -g %s@latest。" % (len(_RETRY_DELAYS), _PKG_NAME))
    else:
        # npm 的进度条/颜色转义与中文 Windows 的 GBK 输出都进过这里，先洗再用
        err = runner.clean_cli_text(stderr)[-800:] or "退出码 %s" % res["exit_code"]
    return {"ok": False, "exit_code": res["exit_code"], "error": err}


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
        [sys.executable, str(paths.APP_DIR / "main.py"), "--port", str(port),
         "--wait-port", "--no-browser"],
        # 新实例 CWD 同样不得落在包内，否则下次升级 npm 改名包目录再撞 EBUSY
        cwd=str(Path.home()),
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
