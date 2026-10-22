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
DESKTOP_LOCK = ROOT / "desktop" / "package-lock.json"
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
    # lock 文件两处 version（根字段 + packages[""]）必须同版：否则升版后
    # 闸门查不出、直到有人在 desktop/ 跑 npm install 才收敛
    lock = load(DESKTOP_LOCK)
    for label, actual in (("desktop/package-lock.json", lock.get("version")),
                          ('desktop/package-lock.json packages[""]',
                           (lock.get("packages") or {}).get("", {}).get("version"))):
        if actual != version:
            mismatches.append(f"{label}={actual!r}")
    if mismatches:
        raise SystemExit("version mismatch: " + ", ".join(mismatches))
    print(f"version check passed: {version}")


def sync(version: str) -> None:
    for path in PACKAGE_FILES:
        package = load(path)
        package["version"] = version
        save(path, package)
    lock = load(DESKTOP_LOCK)
    lock["version"] = version
    (lock.get("packages") or {}).setdefault("", {})["version"] = version
    save(DESKTOP_LOCK, lock)
    print(f"synced root and desktop packages (incl. lock) to {version}")


parser = argparse.ArgumentParser()
parser.add_argument("--sync", action="store_true", help="write package versions from VERSION")
args = parser.parse_args()
version = read_version()
if args.sync:
    sync(version)
else:
    check(version)
