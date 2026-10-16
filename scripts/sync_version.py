#!/usr/bin/env python3
"""Keep the public CodeBee package and desktop shell on one version source.

The relay worker is intentionally excluded: it is a separately deployed
private service and has its own compatibility/release cadence.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"
PACKAGE_FILES = (ROOT / "package.json", ROOT / "desktop" / "package.json")
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def read_version() -> str:
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not SEMVER.fullmatch(version):
        raise SystemExit(f"VERSION is not semver: {version!r}")
    return version


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def check(version: str) -> None:
    mismatches = []
    for path in PACKAGE_FILES:
        actual = load(path).get("version")
        if actual != version:
            mismatches.append(f"{path.relative_to(ROOT)}={actual!r}")
    if mismatches:
        raise SystemExit("version mismatch: " + ", ".join(mismatches))
    print(f"version check passed: {version}")


def sync(version: str) -> None:
    for path in PACKAGE_FILES:
        package = load(path)
        package["version"] = version
        save(path, package)
    print(f"synced root and desktop packages to {version}")


parser = argparse.ArgumentParser()
parser.add_argument("--sync", action="store_true", help="write package versions from VERSION")
args = parser.parse_args()
version = read_version()
if args.sync:
    sync(version)
else:
    check(version)
