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
                result["version"] = text.splitlines()[0][:200] if text else ""
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
