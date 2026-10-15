"""Optional CodeBee host requirements, using strict SemVer minimums."""
from __future__ import annotations

import json
import re
from pathlib import Path

_VERSION = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$")


def host_version():
    try:
        package = Path(__file__).resolve().parents[2] / "package.json"
        return str(json.loads(package.read_text(encoding="utf-8")).get("version") or "")
    except (OSError, ValueError):
        return ""


def _version(raw):
    match = _VERSION.fullmatch(raw) if isinstance(raw, str) else None
    if not match:
        raise ValueError("codebee.min_version 必须是完整 SemVer 版本号")
    parts = match.group(4).split(".") if match.group(4) else []
    if any(x.isdigit() and len(x) > 1 and x.startswith("0") for x in parts):
        raise ValueError("codebee.min_version 的预发布数字不得有前导零")
    prerelease = tuple((0, int(x)) if x.isdigit() else (1, x) for x in parts)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)),
            0 if parts else 1, prerelease)


def check(manifest):
    requirement = manifest.get("codebee", {})
    if not isinstance(requirement, dict) or set(requirement) - {"min_version"}:
        raise ValueError("codebee 仅支持 min_version 宿主版本约束")
    minimum = requirement.get("min_version", "")
    if "min_version" in requirement:
        required = _version(minimum)
    current = host_version()
    compatible, reason = True, ""
    if minimum:
        try:
            compatible = _version(current) >= required
        except ValueError:
            compatible = False
        if not compatible:
            reason = "插件需要 CodeBee >= %s，当前宿主版本 %s" % (minimum, current or "unknown")
    return {"host_version": current, "min_version": minimum,
            "compatible": compatible, "reason": reason}
