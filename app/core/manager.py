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
_STATE = {"detected": {}, "versions": {}, "detect_ts": 0.0}


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
    with _LOCK:
        if not force and _STATE["detected"] and time.time() - _STATE["detect_ts"] < 60:
            return _STATE["detected"]
        detected = {}
        for entry in catalog.load():
            try:
                detected[entry["id"]] = detect_entry(entry)
            except Exception as e:
                detected[entry["id"]] = {"installed": False, "detail": "检测出错: %r" % e}
        _STATE["detected"] = detected
        _STATE["detect_ts"] = time.time()
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
            "group": e.get("cli_group", "installable"),
            "installed": det.get("installed", False),
            "detail": det.get("detail", ""),
            "version": version_of(e),
            "config_path": _config_path(e),
            "config_writable": (e.get("config") or {}).get("format") in ("toml-line", "json"),
            "model": read_model(e),
            "orch_kind": (e.get("orch") or {}).get("kind"),
            "orch_enabled": orch_enabled,
            "orch_model": pref.get("model") or "",
            "has_install": bool(e.get("install")),
            "has_upgrade": bool(e.get("upgrade")),
        })
    return view
