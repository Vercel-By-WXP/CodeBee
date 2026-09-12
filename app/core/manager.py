# -*- coding: utf-8 -*-
"""智能体管理器：安装检测、版本、安装/升级、模型配置读写。

安全约束：catalog 中的配置文件路径展开后必须落在用户主目录内，
防止相对路径穿越到预期之外的系统位置。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import catalog, paths, runner

CREATE_NO_WINDOW = 0x08000000
VERSION_TTL = 300  # 版本缓存 5 分钟

_LOCK = threading.RLock()
_STATE = {"detected": {}, "versions": {}, "detect_ts": 0.0, "detect_ev": None}


def _expand(p):
    return os.path.abspath(os.path.expanduser(os.path.expandvars(p)))


def _safe_config_path(raw):
    """展开并校验配置路径：必须在用户主目录内（防穿越）。"""
    if not raw:
        return None
    full = _expand(raw)
    home = os.path.abspath(os.path.expanduser("~"))
    try:
        if os.path.commonpath([full, home]) != home:
            return None
    except ValueError:
        return None
    return full


# ---------------------------------------------------------------- 检测

def detect_entry(entry):
    d = entry.get("detect") or {}
    if d.get("cli"):
        path = shutil.which(d["cli"])
        return {"installed": bool(path), "detail": path or ""}
    if d.get("exe"):
        full = _expand(d["exe"])
        return {"installed": os.path.isfile(full), "detail": full if os.path.isfile(full) else ""}
    if d.get("uwp"):
        base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages", d["uwp"])
        return {"installed": os.path.isdir(base), "detail": base if os.path.isdir(base) else ""}
    if d.get("dir"):
        full = _expand(d["dir"])
        return {"installed": os.path.isdir(full), "detail": full if os.path.isdir(full) else ""}
    return {"installed": False, "detail": ""}


def detect_all(force=False):
    """检测全部条目。检测（慢磁盘 IO）在锁外跑：shutil.which/isfile 在
    Windows 上遇到断链的 PATH 项可能卡数秒，持锁会把所有并发请求堵死
    （曾导致 SSE 多连接时服务假死）。等待方有界等待 30s 后拿旧结果。
    """
    with _LOCK:
        if not force and _STATE["detected"] and time.time() - _STATE["detect_ts"] < 60:
            return _STATE["detected"]
        ev = _STATE["detect_ev"]
        lead = ev is None  # 我是本次检测的执行者
        if lead:
            ev = _STATE["detect_ev"] = threading.Event()
    if not lead:
        ev.wait(30)  # 检测完成或超时；两种情况都拿当前最新快照
        with _LOCK:
            return dict(_STATE["detected"])
    try:
        detected = {}
        for entry in catalog.load():
            try:
                detected[entry["id"]] = detect_entry(entry)
            except Exception as e:
                detected[entry["id"]] = {"installed": False, "detail": "检测出错: %r" % e}
        if detected:
            with _LOCK:
                _STATE["detected"] = detected
    finally:
        with _LOCK:
            _STATE["detect_ts"] = time.time()
            _STATE["detect_ev"] = None
        ev.set()
    return detected


def _uwp_version(package_dir):
    import xml.etree.ElementTree as ET
    mf = os.path.join(package_dir, "AppxManifest.xml")
    if not os.path.isfile(mf):
        return None
    try:
        tree = ET.parse(mf)
        for el in tree.iter():
            if el.tag.endswith("}Identity") or el.tag == "Identity":
                return el.get("Version")
    except Exception:
        pass
    return None


def _exe_version(path):
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Item -LiteralPath '%s').VersionInfo.ProductVersion" % path.replace("'", "''")],
            capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=25)
        out = r.stdout.decode("utf-8", "replace").strip()
        return out or None
    except Exception:
        return None


def version_of(entry):
    """版本探测：CLI 走 --version；UWP 读 AppxManifest；exe 走 PowerShell（惰性缓存）。"""
    eid = entry["id"]
    with _LOCK:
        cached = _STATE["versions"].get(eid)
        if cached and time.time() - cached[0] < VERSION_TTL:
            return cached[1]
    det = (detect_all() or {}).get(eid) or {}
    version = None
    cli = (entry.get("detect") or {}).get("cli")
    if cli and det.get("installed"):
        try:
            r = subprocess.run(["cmd", "/c", cli, "--version"], capture_output=True,
                               creationflags=CREATE_NO_WINDOW, timeout=20)
            out = (r.stdout or b"").decode("utf-8", "replace").strip()
            if not out:
                out = (r.stderr or b"").decode("utf-8", "replace").strip()
            version = out.splitlines()[0][:60] if out else None
        except Exception:
            version = None
    elif det.get("detail") and os.path.isdir(det["detail"]):
        version = _uwp_version(det["detail"])
    elif det.get("detail") and os.path.isfile(det["detail"]):
        version = _exe_version(det["detail"])
    with _LOCK:
        _STATE["versions"][eid] = (time.time(), version or "-")
    return version or "-"


# ---------------------------------------------------------------- 模型配置

def _config_path(entry):
    cfg = entry.get("config") or {}
    return _safe_config_path(cfg.get("path"))


def read_model(entry):
    path = _config_path(entry)
    cfg = entry.get("config") or {}
    if not path or not os.path.isfile(path) or not cfg.get("format"):
        return None
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except Exception:
        return None
    if cfg["format"] == "toml-line":
        m = re.search(r'(?m)^\s*model\s*=\s*"([^"]+)"', text)
        return m.group(1) if m else None
    if cfg["format"] == "json":
        try:
            v = json.loads(text).get("model")
            if v:
                return v
        except Exception:
            pass
        m = re.search(r'"model"\s*:\s*"([^"]+)"', text)
        return m.group(1) if m else None
    if cfg["format"] == "jsonc":
        m = re.search(r'"model"\s*:\s*"([^"]+)"', text)
        return m.group(1) if m else None
    return None


def write_model(entry, model):
    """写入默认模型（改动前自动备份 .bak）。仅支持 toml-line / json 两种格式。"""
    path = _config_path(entry)
    cfg = entry.get("config") or {}
    fmt = cfg.get("format")
    if not path:
        return {"ok": False,
                "error": "配置路径无效或不在用户主目录内，已拒绝写入"}
    if fmt not in ("toml-line", "json"):
        return {"ok": False, "error": "该工具的模型配置格式暂不支持自动写入，请手动编辑 %s" % path}
    model = (model or "").strip()
    if not model:
        return {"ok": False, "error": "模型名不能为空"}
    # 写入前二次校验：解析真实路径后必须仍在用户主目录内（防穿越/符号链接），
    # 校验通过后统一改用解析路径写入
    home = os.path.abspath(os.path.expanduser("~"))
    path = os.path.realpath(path)
    try:
        if os.path.commonpath([path, home]) != home:
            return {"ok": False, "error": "配置路径越出用户主目录，已拒绝写入"}
    except ValueError:
        return {"ok": False, "error": "配置路径越出用户主目录，已拒绝写入"}
    try:
        if os.path.isfile(path):
            shutil.copyfile(path, path + ".bak")
        if fmt == "toml-line":
            text = open(path, encoding="utf-8", errors="replace").read()
            new_line = 'model = "%s"' % model
            if re.search(r'(?m)^\s*model\s*=\s*"[^"]*"', text):
                text = re.sub(r'(?m)^\s*model\s*=\s*"[^"]*"', new_line, text)
            else:
                text = text.rstrip("\n") + "\n" + new_line + "\n"
            Path(path).write_bytes(text.encode("utf-8"))
        else:
            try:
                data = json.loads(open(path, encoding="utf-8", errors="replace").read())
            except Exception:
                return {"ok": False, "error": "配置文件不是合法 JSON，已中止（避免覆盖）"}
            data["model"] = model
            Path(path).write_bytes(
                json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
        return {"ok": True, "model": read_model(entry)}
    except Exception as e:
        return {"ok": False, "error": repr(e)}


# ---------------------------------------------------------------- 安装/升级

def run_mgmt_command(entry, op, cancel_event=None, log_path=None):
    """执行 install/upgrade 命令（在任务队列里跑，日志落盘）。"""
    cmd = entry.get(op)
    if not cmd:
        return {"ok": False,
                "error": "未配置 %s 命令：请在 data/catalog.json 的 \"%s\" 里补充，或用官方渠道安装"
                         % (op, entry["id"])}
    res = runner.run_process(shell_cmd=cmd, cwd=str(paths.ROOT),
                             timeout=1800, cancel_event=cancel_event, log_path=log_path)
    detect_all(force=True)
    with _LOCK:
        _STATE["versions"].pop(entry["id"], None)
    return {"ok": res["ok"], "exit_code": res["exit_code"],
            "error": "" if res["ok"] else (res["stderr"][-800:] or "退出码 %s" % res["exit_code"])}


# ---------------------------------------------------------------- 版本检查

_UPDATE_CACHE = {}   # agent_id → (ts, {current, latest, updatable, note})
UPDATE_TTL = 600


def _npm_pkg_name(cmd):
    """从 npm 安装命令里取包名（支持 @scope/name@latest）。

    跳过包名之前的 flag：`npm install -g --ignore-scripts @scope/pkg` 必须取到
    @scope/pkg，否则「检查更新」会拿 flag 当包名去查 registry。
    """
    m = re.search(r"npm\s+(?:install|i)\s+(.+)$", cmd or "")
    if not m:
        return None
    for tok in m.group(1).split():
        if tok.startswith("-"):
            continue
        if tok.startswith("@"):
            m2 = re.match(r"(@[^/]+/[^@]+)", tok)
            return m2.group(1) if m2 else None
        return tok.split("@")[0]
    return None


def _ver_tuple(s):
    return [int(x) for x in re.findall(r"\d+", str(s or ""))[:4]]


def check_update(entry, force=False):
    """检查是否有新版本可用。npm 走 npm view；winget 走 winget upgrade 列表；
    其他渠道标记为不支持。结果缓存 10 分钟。"""
    eid = entry["id"]
    if not force:
        cached = _UPDATE_CACHE.get(eid)
        if cached and time.time() - cached[0] < UPDATE_TTL:
            return cached[1]
    current = version_of(entry)
    cur_num = ".".join(str(x) for x in _ver_tuple(current)) or "-"
    result = {"current": current, "latest": None, "updatable": None, "note": ""}

    cmd = entry.get("install") or entry.get("upgrade") or ""
    pkg = _npm_pkg_name(cmd)
    if pkg:
        r = runner.run_process(argv=["cmd", "/c", "npm", "view", pkg, "version"], timeout=90)
        latest = ""
        if r["ok"]:
            for line in (r["stdout"] or "").splitlines():
                line = line.strip()
                if line and re.match(r"^\d", line):
                    latest = line
        if not latest:
            result["note"] = "查询 npm 失败（网络或 registry 问题）：" + (r["stderr"] or "")[-200:]
        else:
            result["latest"] = latest
            result["updatable"] = bool(_ver_tuple(latest) > _ver_tuple(cur_num))
            if not result["updatable"]:
                result["note"] = "已是最新版本"
    elif "winget" in cmd:
        m = re.search(r"--id\s+([A-Za-z0-9._-]+)", cmd)
        wid = m.group(1) if m else None
        if not wid:
            result["note"] = "无法从命令中解析 winget 包 ID"
        else:
            r = runner.run_process(argv=["cmd", "/c", "winget", "upgrade"], timeout=180)
            if not r["ok"]:
                result["note"] = "winget upgrade 查询失败：" + (r["stderr"] or "")[-200:]
            else:
                hit = [l for l in (r["stdout"] or "").splitlines() if wid.lower() in l.lower()]
                result["updatable"] = bool(hit)
                if hit:
                    result["latest"] = "（winget 有可用更新）"
                else:
                    result["latest"] = cur_num
                    result["note"] = "已是最新版本"
    else:
        result["note"] = "该渠道暂不支持自动检查更新，可直接点升级尝试"

    with _LOCK:
        _UPDATE_CACHE[eid] = (time.time(), result)
    return result


# 「进入智能体目录页自动检查更新」的后台任务状态
_UPDATE_CHECK = {"running": False, "total": 0, "done": 0}


def updates_checking():
    with _LOCK:
        return bool(_UPDATE_CHECK["running"])


def update_info(entry):
    """单个 CLI 的更新检查结果（供管理页卡片展示）。没查过时为 unknown。"""
    with _LOCK:
        cached = _UPDATE_CACHE.get(entry["id"])
    if not cached:
        return {"status": "unknown", "latest": None, "updatable": None,
                "note": "", "checked_at": ""}
    res = cached[1] or {}
    up = res.get("updatable")
    status = "updatable" if up is True else ("current" if up is False else "unsupported")
    return {"status": status, "latest": res.get("latest"), "updatable": up,
            "note": res.get("note") or "",
            "checked_at": time.strftime("%H:%M", time.localtime(cached[0]))}


def _checkable_entries():
    """已安装且配了 install/upgrade 的条目——只有这些才谈得上「是否有新版本」。"""
    detected = detect_all()
    out = []
    for e in catalog.load():
        det = (detected or {}).get(e["id"]) or {}
        if det.get("installed") and (e.get("upgrade") or e.get("install")):
            out.append(e)
    return out


def check_updates_async(force=False):
    """后台逐个检查已安装 CLI 的远端最新版本，返回本次要检查的条目数。

    进入「智能体目录」页时自动触发。单条结果复用 check_update 的 10 分钟缓存，
    所以反复进出页面几乎不产生额外子进程/网络开销；已在跑时不重复起线程。
    """
    with _LOCK:
        if _UPDATE_CHECK["running"]:
            return 0
        entries = _checkable_entries()
        _UPDATE_CHECK.update(running=True, total=len(entries), done=0)

    def _worker():
        try:
            for e in entries:
                try:
                    check_update(e, force=force)
                except Exception:
                    pass
                finally:
                    with _LOCK:
                        _UPDATE_CHECK["done"] += 1
        finally:
            with _LOCK:
                _UPDATE_CHECK["running"] = False

    threading.Thread(target=_worker, name="catalog-update-check", daemon=True).start()
    return len(entries)


def catalog_view():
    """管理页数据：catalog + 检测 + 版本 + 模型 + 编排启用状态。"""
    from . import registry
    entries = catalog.load()
    detected = detect_all(force=False)
    enabled = registry.load_enabled()
    view = []
    for e in entries:
        det = detected.get(e["id"]) or {}
        pref = enabled.get(e["id"]) or {}
        orch_enabled = bool(pref.get("enabled", e.get("default_enabled", False))) if e.get("orch") else False
        view.append({
            "id": e["id"], "name": e.get("name", e["id"]), "note": e.get("note", ""),
            "group": "installed" if det.get("installed") else "installable",
            "installed": det.get("installed", False),
            "detail": det.get("detail", ""),
            "version": version_of(e),
            "config_path": _config_path(e),
            "config_writable": (e.get("config") or {}).get("format") in ("toml-line", "json"),
            "model": read_model(e),
            "orch_kind": (e.get("orch") or {}).get("kind"),
            "orch_enabled": orch_enabled,
            "update": update_info(e),
            "has_install": bool(e.get("install")),
            "has_upgrade": bool(e.get("upgrade")),
        })
    return view
