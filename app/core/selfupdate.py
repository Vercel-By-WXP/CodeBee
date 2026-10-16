# -*- coding: utf-8 -*-
"""Tutti 自更新：版本读取 / 新版本检测 / 一键升级 / 就地重启。

安装模式判定（mode）：
  npm   — 运行副本位于 node_modules 下且带 package.json（npm 全局安装的包），
          可查新、可升级、可重启；
  repo  — 带 .git 的开发仓库：**永不自动升级**（会覆盖开发中的代码），提示走 git pull；
  other — 裸源码拷贝，提示手动替换。

升级 = 在标准 mgmt run 里从 npm registry 直接下载 CodeBee `.tgz`（不依赖
Node.js/npm，日志实时落盘、SSE 可看进度）。解包校验后替换包目录文件，当前进程
已加载进内存不受影响，装完后由自动重启换新代码：新进程先等旧端口释放再 bind
（Windows SO_REUSEADDR 允许双 LISTEN 同时存在，必须先验旧进程真退了），旧进程
发送完重启响应后自退。

安全：两处子进程命令的 argv 均为**行内字面量列表**（可执行文件与全部参数不来自任何
外部输入；重启仅透传 argparse 校验过的整型端口），shell 全程 False，绝不拼接用户输入。
改发布名时（见 test_selfupdate 与 package.json 的一致性断言）同步改 _PKG_NAME。
"""
from __future__ import annotations

import json
import io
import base64
import hashlib
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import paths, runner

log = logging.getLogger(__name__)

_PKG_NAME = "codebee"   # npm 发布名；必须与 package.json 的 name 一致（单测断言）
_UPDATE_TTL = 600   # 查新结果缓存（秒）
_LOCK = threading.Lock()
_CHECK_CACHE = {"ts": 0.0, "result": None}
_NPM_REGISTRY = "https://registry.npmjs.org"
_DIRECT_TARBALL_CAP = 120 * 1024 * 1024


def package_version():
    """读本包 package.json 的 version；读不到返回 ''（开发仓库未同步版本号时）。"""
    try:
        pj = (paths.ROOT / "package.json").resolve()
        return str(json.loads(pj.read_text(encoding="utf-8")).get("version") or "")
    except Exception:
        return ""


def install_mode():
    """npm / repo / source / other（见模块 docstring）。"""
    if os.environ.get("CODEBEE_DESKTOP") == "1":
        return "desktop"
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
    """npm 命令 argv：复用通用 CLI 探测并正确包装 Windows .cmd 垫片。

    服务进程的 PATH 可能早于 Node.js 安装，``runner.resolve_command`` 会
    额外探测常见的用户 npm 目录；找到 ``npm.cmd`` 时仍经 ``cmd /c`` 启动。
    """
    return runner.resolve_command("npm") + list(args)


def _npm_missing_error(text):
    """把 Windows ``npm 不是内部或外部命令`` 转成可操作的提示。"""
    blob = str(text or "")
    if not re.search(
            r"(?:不是内部或外部命令|not recognized as an internal or external command|"
            r"command not found|找不到.*(?:npm|命令)|npm.*not found|"
            r"找不到可执行文件.*npm)",
            blob, re.I):
        return ""
    return ("未找到 npm 命令。请安装 Node.js（npm 会随 Node.js 一起安装），"
            "并将 Node.js 目录加入 PATH；安装完成后重启 CodeBee，再检查更新。"
            "Windows 7 请安装仍支持 Win7 的 Node.js LTS 版本。")


def _registry_get(url, cap=2 * 1024 * 1024):
    """读取 npm registry 的固定 HTTPS 地址，不依赖 node/npm。"""
    parsed = urllib.parse.urlsplit(str(url))
    if parsed.scheme != "https" or parsed.hostname not in {
            "registry.npmjs.org", "registry.yarnpkg.com"}:
        raise ValueError("拒绝非 npm registry 地址")
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            target = urllib.parse.urlsplit(newurl)
            if target.scheme != "https" or target.hostname not in {
                    "registry.npmjs.org", "registry.yarnpkg.com"}:
                raise ValueError("registry 重定向目标不受信任")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    req = urllib.request.Request(str(url), headers={
        "User-Agent": "CodeBee-SelfUpdate/1.0", "Accept": "application/json"})
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(req, timeout=60) as resp:
        data = resp.read(cap + 1)
    if len(data) > cap:
        raise ValueError("registry 响应过大")
    return data


def _direct_npm_meta():
    """不依赖 npm 读取 latest 元数据；返回 (version, readme, tarball_url, integrity, error)。"""
    try:
        data = _registry_get("%s/%s/latest" % (_NPM_REGISTRY, _PKG_NAME))
        meta = json.loads(data.decode("utf-8"))
        if not isinstance(meta, dict):
            raise ValueError("registry 返回格式错误")
        dist = meta.get("dist") if isinstance(meta.get("dist"), dict) else {}
        return (str(meta.get("version") or ""), _relnotes(meta.get("readme") or ""),
                str(dist.get("tarball") or ""), str(dist.get("integrity") or ""), "")
    except Exception as exc:
        return "", "", "", "", str(exc)


def _safe_unpack_package(data, dest):
    """安全解包 npm .tgz，返回 package/ 内容目录。"""
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    files = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for member in tf.getmembers():
            name = member.name.replace("\\", "/")
            parts = [p for p in name.split("/") if p]
            if not parts or parts[0] != "package" or any(p in (".", "..") for p in parts):
                raise ValueError("npm 包内路径非法")
            if member.issym() or member.islnk() or not member.isfile():
                continue
            files += 1
            total += int(member.size or 0)
            if files > 10000 or total > _DIRECT_TARBALL_CAP:
                raise ValueError("npm 包体量超过上限")
            target = (dest / "/".join(parts[1:])).resolve()
            if dest != target and dest not in target.parents:
                raise ValueError("npm 包路径越界")
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(member)
            if src is None:
                continue
            with target.open("wb") as out:
                shutil.copyfileobj(src, out, length=256 * 1024)
    root = dest
    if not (root / "package.json").is_file():
        raise ValueError("npm 包缺少 package.json")
    return root


def _direct_upgrade(log_path=None):
    """直接下载并覆盖当前包；返回 runner.run_process 兼容结果。"""
    version, _notes, tarball, integrity, error = _direct_npm_meta()
    if not tarball:
        return {"ok": False, "exit_code": 1, "stdout": "", "stderr": error or "无法取得 npm 包地址"}
    try:
        data = _registry_get(tarball, cap=_DIRECT_TARBALL_CAP)
        if integrity:
            algo, sep, encoded = integrity.partition("-")
            if algo != "sha512" or not sep or not encoded:
                raise ValueError("npm 包完整性字段格式错误")
            actual = base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")
            if actual != encoded:
                raise ValueError("npm 包完整性校验失败")
        with tempfile.TemporaryDirectory(prefix="codebee-update-") as td:
            staged = _safe_unpack_package(data, Path(td) / "package")
            staged_version = str(json.loads((staged / "package.json").read_text(encoding="utf-8")).get("version") or "")
            if not staged_version or _ver_tuple(staged_version) < _ver_tuple(version):
                raise ValueError("下载的 npm 包版本校验失败")
            # npm 包的 files 白名单决定应覆盖的内容；copytree 保留当前进程已加载模块，
            # 随后由原有自动重启切换到新文件。
            shutil.copytree(staged, paths.ROOT, dirs_exist_ok=True)
        _log_note(log_path, "已使用内置 registry 下载通道升级到 %s" % staged_version)
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}
    except Exception as exc:
        return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "内置升级失败：%s" % exc}


def _npm_meta():
    """兼容旧调用名：从 npm registry 读取 latest，不依赖 npm 命令。

    relnotes 来自 registry 元数据里的 README（与查新同一条 npm 通道，不依赖
    GitHub 连通性），展示「新版本更新内容」用。部分 npm 版本 --json 不带
    readme 字段，此时回退到 `npm view <pkg> readme` 纯文本再提取。"""
    if os.environ.get("TUTTI_SELFUPDATE_OFFLINE") == "1":
        # 测试离线闸：桌面模式查新不再真跑 npm（Sidecar e2e 确定性）。
        return "", "", "selfupdate meta disabled (offline test mode)"
    if os.environ.get("TUTTI_SELFUPDATE_OFFLINE") == "1":
        return "", "", "selfupdate meta disabled (offline test mode)"
    ver, notes, _tarball, _integrity, err = _direct_npm_meta()
    return ver, notes, ("" if ver else "内置 registry 查新失败：%s" % (err or "版本号缺失"))


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
    if mode == "desktop":
        # Desktop installers are attached to GitHub Releases. The Electron
        # shell can download the matching asset and hand it to the installer;
        # the browser fallback opens the public release list.
        out["download_url"] = "https://github.com/Vercel-By-WXP/CodeBee/releases"
        # 桌面壳不能自我替换引擎，但「查新」照常可用：安装包引擎版本与 npm 同源。
        # 查到新版时前端渲染「打开下载页」；完整桌面安装由壳负责。
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
            out["note"] = ("桌面版更新：退出应用后从下载页安装新版，任务和配置保留。"
                           if out["has_update"] else "")
        else:
            out["note"] = "无法比较版本（本地或 registry 版本号缺失）"
        with _LOCK:
            _CHECK_CACHE["ts"] = time.time()
            _CHECK_CACHE["result"] = dict(out)
        return out
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
    具备时才跳过自动重启。"""
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
            "mgmt", "升级 CodeBee 本体（registry 直传）",
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
    """升级成功后的自动重启：
    ①知道服务端口（apply_upgrade 传入）；②版本真的变了（同版本重装不折腾）；
    ③等当前用户任务自然结束后再原子停止接单——正在干活的任务不能被升级
    重启打断，也不再要求用户找到一个可能不存在的手动重启按钮。"""
    import threading
    from . import jobs
    if not _PENDING_PORT:
        _log_note(log_path, "未记录服务端口，跳过自动重启——请在版本页手动重启生效")
        return
    new_pkg = package_version()
    if not old_pkg or new_pkg == old_pkg:
        _log_note(log_path, "版本未变化（%s），无需重启" % (new_pkg or "?"))
        return
    port = _PENDING_PORT

    def _go():
        drain_started = False
        wait_noted = False
        try:
            time.sleep(3.0)   # 留出日志收尾/浏览器看到「升级完成」的窗口
            while not drain_started:
                if (not wait_noted and
                        (getattr(jobs, "_alive", 0) > 1 or
                         getattr(jobs, "_chat_alive", 0) > 0)):
                    _log_note(log_path, "等待当前任务完成后自动重启服务…")
                    wait_noted = True
                drain_started = jobs.begin_restart_drain()
                if not drain_started:
                    jobs.wait_for_idle(timeout=1.0)
            _log_note(log_path, "自动重启服务以应用新版本 %s …" % new_pkg)
            if relaunch(port):
                self_quit()
        except Exception:
            log.exception("selfupdate: 自动重启失败")
        finally:
            # 正常 self_quit 会直接结束进程；若拉起失败、异常或测试替身返回，必须
            # 释放停止接单闸，避免当前实例永久拒绝新任务。
            if drain_started:
                jobs.cancel_restart_drain()
    threading.Thread(target=_go, name="selfupdate-relaunch",
                     daemon=True).start()


def run_upgrade(run_id, log_path, cancel_event=None):
    """worker 线程里执行 registry 直传升级（不依赖 Node.js/npm）。"""
    old_pkg = package_version()
    if cancel_event is not None and cancel_event.is_set():
        return {"ok": False, "exit_code": None, "error": "用户主动取消",
                "cancelled": True}
    res = _direct_upgrade(log_path)
    if res.get("cancelled"):
        return {"ok": False, "exit_code": res.get("exit_code"),
                "error": "用户主动取消", "cancelled": True}
    if res["ok"]:
        with _LOCK:  # 装完即过期查新缓存，重启后自然拿到新版本
            _CHECK_CACHE["result"] = None
        _maybe_auto_relaunch(old_pkg, log_path)
        return {"ok": True, "exit_code": res["exit_code"], "error": ""}
    stderr = res.get("stderr") or ""
    err = runner.clean_cli_text(stderr)[-800:] or "退出码 %s" % res.get("exit_code")
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
    if install_mode() == "desktop":
        return False  # Only the desktop shell may replace its owned sidecar.
    port = int(port)
    if port < 1 or port > 65535:
        return False
    from .manager import tmp_data_no_home_write
    if tmp_data_no_home_write():
        # 测试实例防毒闸的延伸：TUTTI_DATA 在临时目录的进程绝不拉起真实
        # 端口服务——relaunch 的目标端口往往是 8765（真实服务所在），
        # 新实例启动清场会把生产服务当「自家旧实例」杀掉（2026-09-23 实案）
        log.warning("relaunch 拒绝：测试实例（TUTTI_DATA 在临时目录）"
                    "不得拉起真实端口服务")
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
