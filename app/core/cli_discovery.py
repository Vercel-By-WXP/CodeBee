"""Dependency-free local CLI capability discovery."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from . import atomic

DEFAULT_CLIS = (
    {"name": "codex", "executable": "codex"},
    {"name": "claude", "executable": "claude"},
    {"name": "qwen", "executable": "qwen"},
    {"name": "aider", "executable": "aider"},
    {"name": "opencode", "executable": "opencode"},
)


def cache_path(data_dir):
    return Path(data_dir) / "cli_capabilities.json"


_TRACEBACK_HEAD = "Traceback (most recent call last)"


def _version_text(text):
    """把版本命令的输出拆成 (version, error)。

    CLI 自身启动失败时会往 stderr 打 Python traceback，首行恒为
    "Traceback (most recent call last):"。原样存进 version 会让智能体管理页
    显示「版本：Traceback (most recent call last):」，把崩溃伪装成版本号，
    也埋没了唯一有用的最后一行（如 ImportError 原因）。这里把首行让给
    version，异常输出转成 error 并只取末行摘要——调用方据此把该 CLI 标为
    不可用，而不是「已安装，版本未知」。
    """
    first = text.splitlines()[0][:200] if text else ""
    if first.startswith(_TRACEBACK_HEAD):
        reason = ""
        for line in reversed(text.splitlines()):
            if line.strip() and not line.startswith((" ", "\t")):
                reason = line.strip()[:160]
                break
        return "", "版本识别失败：CLI 启动即报错%s" % ("（%s）" % reason if reason else "")
    return first, ""


def probe(spec, *, timeout=3, environ=None):
    spec = dict(spec or {})
    executable = spec.get("executable") or spec.get("name")
    path = shutil.which(executable, path=(environ or os.environ).get("PATH"))
    result = {"name": spec.get("name") or executable, "executable": executable,
              "path": path or "", "available": bool(path), "version": "",
              "supports_stdin": bool(spec.get("supports_stdin", True)),
              "output_format": spec.get("output_format", "text"), "error": ""}
    if not path:
        result["install_hint"] = "安装 %s 并确保其位于 PATH" % executable
        return result
    for flag in (spec.get("version_flag", "--version"), "--help"):
        try:
            proc = subprocess.run([path, flag], capture_output=True, text=True,
                                  timeout=timeout, check=False)
            text = (proc.stdout or proc.stderr or "").strip()
            if flag == spec.get("version_flag", "--version"):
                result["version"], result["error"] = _version_text(text)
            result["help_available"] = proc.returncode == 0 or bool(text)
            if text and "json" in text.lower():
                result["output_format"] = "json"
            break
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["error"] = str(exc)[:200]
    return result


def discover(specs=None, *, timeout=3, environ=None):
    return [probe(spec, timeout=timeout, environ=environ)
            for spec in (specs or DEFAULT_CLIS)]


def refresh(path, specs=None, *, timeout=3, environ=None):
    """Probe configured CLIs and atomically write the cache."""
    rows = discover(specs=specs, timeout=timeout, environ=environ)
    target = Path(path)
    atomic.write_json(target, {"clis": rows})
    return rows


def save(results, path):
    atomic.write_json(Path(path), {"clis": results})


def load(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data.get("clis", []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        return []
