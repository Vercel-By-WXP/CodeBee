#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Release gate for the npm package.

The gate is deliberately deterministic and runs before ``npm publish``:

* the complete unittest suite must pass;
* the checkout must be clean both before and after the suite (parallel work in
  progress is therefore a hard stop);
* the exact npm archive is created in a temporary directory and its Python
  entrypoints are imported from the unpacked archive.

Use ``python scripts/release_gate.py`` for a dry gate.  ``--publish`` performs
``npm publish`` only after every check succeeds.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
NPM = "npm.cmd" if os.name == "nt" else "npm"
TEST_COMMAND = (sys.executable, "-m", "unittest", "discover", "-s", "tests",
                "-p", "test_*.py")


class GateError(RuntimeError):
    """A release prerequisite was not met."""


def _run(argv: Sequence[str], *, cwd: Path = ROOT, timeout: int = 1800,
         capture: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(argv), cwd=str(cwd), timeout=timeout, check=False,
            text=True, encoding="utf-8", errors="replace",
            capture_output=capture)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GateError("命令执行失败：%s" % exc) from exc


def git_status() -> str:
    """Return all tracked and untracked changes in porcelain form."""
    result = _run(("git", "status", "--porcelain=v1", "--untracked-files=all"),
                  timeout=60)
    if result.returncode != 0:
        raise GateError("无法读取 git status：%s" % (result.stderr or result.stdout).strip())
    return (result.stdout or "").strip()


def require_clean(label: str = "工作区") -> None:
    status = git_status()
    if status:
        raise GateError("%s不干净，禁止发版；请先处理并行在制品：\n%s" %
                        (label, status))


def run_tests() -> None:
    result = _run(TEST_COMMAND, timeout=3600)
    if result.returncode != 0:
        output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
        raise GateError("全量测试未通过（退出码 %s）\n%s" %
                        (result.returncode, output[-8000:]))


def _pack_json(raw: str) -> dict:
    """Parse npm's ``--json`` output while tolerating npm log noise."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("[")
        end = raw.rfind("]")
        if start < 0 or end <= start:
            raise GateError("npm pack 未返回可解析的 JSON：%s" % raw[-2000:])
        try:
            value = json.loads(raw[start:end + 1])
        except json.JSONDecodeError as exc:
            raise GateError("npm pack JSON 无效：%s" % raw[-2000:]) from exc
    if isinstance(value, list):
        value = value[0] if value else {}
    if not isinstance(value, dict) or not value.get("filename"):
        raise GateError("npm pack JSON 缺少 filename：%r" % value)
    return value


def _archive_members(archive: Path) -> list[str]:
    try:
        with tarfile.open(archive, "r:gz") as tf:
            return [m.name for m in tf.getmembers()]
    except (OSError, tarfile.TarError) as exc:
        raise GateError("无法读取 npm 包：%s" % exc) from exc


def validate_package_members(members: Iterable[str]) -> None:
    """Reject package content that should never reach the registry."""
    forbidden_parts = ("/.git/", "/tests/", "/data/", "/.mimosa/",
                       "/__pycache__/", "/.pytest_cache/")
    bad = []
    for name in members:
        normalized = "/" + name.replace("\\", "/").lstrip("/") + "/"
        if any(part in normalized for part in forbidden_parts):
            bad.append(name)
    if bad:
        raise GateError("npm 包含禁止发布内容：\n%s" % "\n".join(bad[:50]))


def pack_and_smoke() -> Path:
    """Pack exactly what npm would publish and import it from the archive."""
    with tempfile.TemporaryDirectory(prefix="codebee-release-") as td:
        dest = Path(td)
        result = _run((NPM, "pack", "--json", "--ignore-scripts",
                       "--pack-destination", str(dest)), timeout=300)
        if result.returncode != 0:
            raise GateError("npm pack 失败：%s" % (result.stderr or result.stdout).strip())
        info = _pack_json(result.stdout or "")
        archive = dest / str(info["filename"])
        if not archive.is_file():
            raise GateError("npm pack 未生成归档：%s" % archive)
        members = _archive_members(archive)
        validate_package_members(members)
        extract = dest / "unpacked"
        extract.mkdir()
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(extract)
        package_root = extract / "package"
        if not (package_root / "app" / "main.py").is_file():
            raise GateError("npm 包缺少 app/main.py 入口")
        smoke = _run((sys.executable, "-c",
                      "import sys; sys.path.insert(0, 'app'); "
                      "import main; import core.error_codes; "
                      "assert main.__file__"),
                     cwd=package_root, timeout=60)
        if smoke.returncode != 0:
            raise GateError("npm 包 import 冒烟失败：%s" %
                            ((smoke.stdout or "") + "\n" +
                             (smoke.stderr or "")).strip()[-4000:])
        # Copying the path out of TemporaryDirectory is intentionally not
        # supported; callers only need the checks, never the transient archive.
        return archive


def run_gate(*, publish: bool = False) -> None:
    require_clean("发版前工作区")
    run_tests()
    require_clean("全量测试后的工作区")
    pack_and_smoke()
    require_clean("打包后的工作区")
    if publish:
        result = _run((NPM, "publish", "--ignore-scripts"), timeout=900)
        if result.returncode != 0:
            raise GateError("npm publish 失败：%s" %
                            ((result.stdout or "") + "\n" +
                             (result.stderr or "")).strip()[-4000:])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CodeBee npm release gate")
    parser.add_argument("--publish", action="store_true",
                        help="gate 通过后执行 npm publish")
    args = parser.parse_args(argv)
    try:
        run_gate(publish=args.publish)
    except GateError as exc:
        print("[release-gate] BLOCKED: %s" % exc, file=sys.stderr)
        return 1
    print("[release-gate] PASS: tests, clean worktree, npm archive import smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
