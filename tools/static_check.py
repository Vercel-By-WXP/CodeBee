"""Reject newly introduced Ruff or Pyright diagnostics.

The checker is intentionally a standard-library-only developer tool.  The
linters themselves are installed by developers or CI, never by the runtime
application.  Existing diagnostics are recorded in the baseline; only a
diagnostic whose fingerprint is absent from that baseline fails the check.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / ".static-check-baseline.json"
PYRIGHT_CONFIG = ROOT / "pyrightconfig.json"
TARGETS = ("app", "tools", "scripts")
RUFF_SELECT = ("E4", "E7", "E9", "F")
SCHEMA_VERSION = 1


def _relative_file(value: Any) -> str:
    """Return a stable repository-relative path for a tool diagnostic."""
    raw = str(value or "").replace("\\", "/")
    root = str(ROOT).replace("\\", "/").rstrip("/")
    root_prefix = root + "/"
    if raw.casefold().startswith(root_prefix.casefold()):
        return raw[len(root_prefix) :]

    # A baseline may be created on Windows and checked on Linux (or the
    # reverse).  Match the repository's known source roots independently of
    # the host path flavor so absolute tool output remains stable.
    for target in TARGETS:
        marker = "/%s/" % target
        index = raw.casefold().find(marker.casefold())
        if index >= 0 and (
            index == 0
            or raw.startswith("/")
            or re.match(r"^[A-Za-z]:/", raw)
        ):
            return raw[index + 1 :]
    return raw.lstrip("/")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ruff_diagnostics(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("Ruff JSON output must be a list")
    result = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        location = item.get("location")
        if not isinstance(location, Mapping):
            location = {}
        result.append(
            {
                "tool": "ruff",
                "file": _relative_file(item.get("filename")),
                "code": str(item.get("code") or ""),
                "line": _int(location.get("row")),
                "column": _int(location.get("column")),
                "message": str(item.get("message") or ""),
            }
        )
    return result


def _pyright_diagnostics(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise ValueError("Pyright JSON output must be an object")
    raw_items = payload.get("generalDiagnostics")
    if not isinstance(raw_items, list):
        raise ValueError("Pyright JSON output has no generalDiagnostics list")
    result = []
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        span = item.get("range")
        start = span.get("start") if isinstance(span, Mapping) else {}
        if not isinstance(start, Mapping):
            start = {}
        result.append(
            {
                "tool": "pyright",
                "file": _relative_file(item.get("file")),
                "severity": str(item.get("severity") or ""),
                "rule": str(item.get("rule") or ""),
                "line": _int(start.get("line")),
                "column": _int(start.get("character")),
                "message": str(item.get("message") or ""),
            }
        )
    return result


def fingerprint(diagnostic: Mapping[str, Any]) -> str:
    """Create a deterministic identity for one normalized diagnostic."""
    relevant = {
        key: diagnostic.get(key)
        for key in (
            "tool",
            "file",
            "code",
            "severity",
            "rule",
            "line",
            "column",
            "message",
        )
        if diagnostic.get(key) not in (None, "")
    }
    encoded = json.dumps(relevant, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def stable_fingerprint(diagnostic: Mapping[str, Any]) -> str:
    """Identify a diagnostic independently of source line movement.

    Adding code above an existing warning should not turn the warning into a
    new finding. Keep the original location-sensitive fingerprint for
    backwards compatibility and use this companion identity for baselines.
    """
    relevant = {
        key: diagnostic.get(key)
        for key in ("tool", "file", "code", "severity", "rule", "message")
        if diagnostic.get(key) not in (None, "")
    }
    encoded = json.dumps(relevant, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def new_diagnostics(
    diagnostics: Iterable[Mapping[str, Any]], baseline: Set[str]
) -> List[Dict[str, Any]]:
    """Return current diagnostics not represented by the baseline."""
    return [
        dict(item)
        for item in diagnostics
        if fingerprint(item) not in baseline and stable_fingerprint(item) not in baseline
    ]


def _command(name: str, env_name: str) -> str:
    configured = os.environ.get(env_name)
    command = configured or shutil.which(name)
    if not command:
        raise RuntimeError(
            "%s not found; install developer tools from requirements-dev.txt"
            % name
        )
    return command


def _run(command: str, args: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            [command] + list(args),
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise RuntimeError("failed to run %s: %s" % (command, exc)) from exc
    if result.returncode not in (0, 1):
        raise RuntimeError(
            "%s failed with exit %d: %s"
            % (command, result.returncode, result.stderr.strip()[:500])
        )
    if not result.stdout.strip():
        raise RuntimeError(
            "%s produced no JSON output (exit %d): %s"
            % (command, result.returncode, result.stderr.strip()[:500])
        )
    return result.stdout


def _version(command: str) -> str:
    lines = _run_text(command, ("--version",)).strip().splitlines()
    if not lines:
        raise RuntimeError("%s produced no version output" % command)
    return lines[0]


def _run_text(command: str, args: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            [command] + list(args),
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise RuntimeError("failed to run %s: %s" % (command, exc)) from exc
    if result.returncode not in (0, 1):
        raise RuntimeError(
            "%s failed with exit %d: %s"
            % (command, result.returncode, result.stderr.strip()[:500])
        )
    return result.stdout or result.stderr


def collect() -> Dict[str, Any]:
    """Run both developer tools and return normalized diagnostics and versions."""
    ruff = _command("ruff", "RUFF_BIN")
    pyright = _command("pyright", "PYRIGHT_BIN")
    ruff_output = _run(
        ruff,
        [
            "check",
            *TARGETS,
            "--select",
            ",".join(RUFF_SELECT),
            "--output-format",
            "json",
        ],
    )
    pyright_output = _run(
        pyright,
        ["--project", str(PYRIGHT_CONFIG), "--outputjson"],
    )
    return {
        "ruff": {
            "version": _version(ruff),
            "diagnostics": _ruff_diagnostics(json.loads(ruff_output)),
        },
        "pyright": {
            "version": _version(pyright),
            "diagnostics": _pyright_diagnostics(json.loads(pyright_output)),
        },
    }


def _baseline_section(
    tool: str, version: str, diagnostics: Sequence[Mapping[str, Any]]
) -> Dict[str, Any]:
    return {
        "version": version,
        "count": len(diagnostics),
        "fingerprints": sorted(fingerprint(item) for item in diagnostics),
        "stable_fingerprints": sorted({stable_fingerprint(item) for item in diagnostics}),
    }


def make_baseline(collected: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "scope": list(TARGETS),
        "ruff": _baseline_section(
            "ruff", collected["ruff"]["version"], collected["ruff"]["diagnostics"]
        ),
        "pyright": _baseline_section(
            "pyright",
            collected["pyright"]["version"],
            collected["pyright"]["diagnostics"],
        ),
    }


def _load_baseline(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("cannot read baseline %s: %s" % (path, exc)) from exc
    if not isinstance(payload, Mapping) or payload.get("schema") != SCHEMA_VERSION:
        raise RuntimeError("unsupported or missing static-check baseline schema")
    if list(payload.get("scope") or ()) != list(TARGETS):
        raise RuntimeError("static-check baseline scope does not match checker scope")
    return payload


def _section_fingerprints(section: Mapping[str, Any]) -> Set[str]:
    values = section.get("fingerprints")
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise RuntimeError("invalid static-check baseline fingerprints")
    count = section.get("count")
    if not isinstance(count, int) or count != len(values) or count != len(set(values)):
        raise RuntimeError("invalid static-check baseline count")
    stable_values = section.get("stable_fingerprints")
    if stable_values is not None and (
        not isinstance(stable_values, list)
        or not all(isinstance(item, str) for item in stable_values)
        or len(stable_values) != len(set(stable_values))
    ):
        raise RuntimeError("invalid stable static-check baseline fingerprints")
    return set(values) | set(stable_values or ())


def _write_baseline(path: Path, baseline: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(baseline, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )


def _check(collected: Mapping[str, Any], baseline: Mapping[str, Any]) -> int:
    failures = []
    for tool in ("ruff", "pyright"):
        section = baseline.get(tool)
        if not isinstance(section, Mapping):
            raise RuntimeError("missing %s section in static-check baseline" % tool)
        expected_version = str(section.get("version") or "")
        actual_version = str(collected[tool]["version"])
        if expected_version != actual_version:
            raise RuntimeError(
                "%s version changed: baseline=%s current=%s; regenerate baseline"
                % (tool, expected_version, actual_version)
            )
        known = _section_fingerprints(section)
        fresh = new_diagnostics(collected[tool]["diagnostics"], known)
        if fresh:
            failures.append((tool, fresh))
        print(
            "%s: %d current, %d baseline, %d new"
            % (tool, len(collected[tool]["diagnostics"]), len(known), len(fresh))
        )
    for tool, diagnostics in failures:
        for item in diagnostics[:20]:
            print(
                "NEW %s %s:%s:%s %s"
                % (
                    tool,
                    item.get("file"),
                    item.get("line"),
                    item.get("column"),
                    item.get("message"),
                )
            )
        if len(diagnostics) > 20:
            print("NEW %s ... and %d more" % (tool, len(diagnostics) - 20))
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="record the current diagnostics as the accepted baseline",
    )
    parser.add_argument(
        "--baseline", default=str(BASELINE), help="baseline JSON path"
    )
    args = parser.parse_args(argv)
    path = Path(args.baseline)
    if not path.is_absolute():
        path = ROOT / path
    try:
        collected = collect()
        if args.update_baseline:
            baseline = make_baseline(collected)
            _write_baseline(path, baseline)
            print("wrote static-check baseline: %s" % path)
            return 0
        if not path.is_file():
            raise RuntimeError(
                "baseline is missing: %s; run with --update-baseline" % path
            )
        return _check(collected, _load_baseline(path))
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print("static-check: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
