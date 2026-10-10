# -*- coding: utf-8 -*-
"""Append-only execution checkpoints with conservative replay semantics."""
from __future__ import annotations

import hashlib
import json
import base64
import difflib
import os
import re
import secrets
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import paths
from .redact import scrub_text

_DIR = paths.DATA_DIR / "checkpoints"
_LOCK = threading.RLock()
_TERMINAL = {"done", "failed", "cancelled", "timeout", "unknown"}
_SNAPSHOT_MAX_BYTES = 1024 * 1024      # ≤此值内嵌快照 JSON；超限进 git 对象库
_SNAPSHOT_TOTAL_MAX_BYTES = 16 * 1024 * 1024
_SUPPORTS_DIR_FD_RENAME = os.rename in getattr(os, "supports_dir_fd", set())


def _file():
    return _DIR / ("checkpoints-%s.jsonl" % time.strftime("%Y%m"))


def _hash(value):
    if isinstance(value, (dict, list, tuple)):
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str)
    else:
        raw = str(value or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _append(row):
    _DIR.mkdir(parents=True, exist_ok=True)
    with _file().open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def start(run_id, step, role, prompt="", *, input_data=None, attempt=1,
          idempotency_key=""):
    """Record a step start without persisting prompt contents."""
    now = time.time()
    try:
        attempt = max(1, min(100, int(attempt)))
    except (TypeError, ValueError):
        attempt = 1
    row = {"event_id": "cp-" + secrets.token_hex(10), "event": "start",
           "run_id": str(run_id or "")[:80], "step": int(step),
           "role": str(role or "")[:80], "status": "running",
           "attempt": attempt, "idempotency_key": str(idempotency_key or "")[:160],
           "prompt_sha256": _hash(prompt), "input_sha256": _hash(input_data),
           "created_at": now}
    with _LOCK:
        try:
            _append(row)
        except OSError:
            pass
    return row


def finish(run_id, step, status, *, error="", output=None, metadata=None):
    status = str(status or "unknown").strip().lower()
    if status not in _TERMINAL:
        status = "unknown"
    row = {"event_id": "cp-" + secrets.token_hex(10), "event": "finish",
           "run_id": str(run_id or "")[:80], "step": int(step),
           "status": status, "error": scrub_text(error, limit=500),
           "output_sha256": _hash(output), "metadata": {
               str(k)[:40]: v for k, v in (metadata or {}).items()
               if isinstance(v, (str, int, float, bool))
           }, "created_at": time.time()}
    with _LOCK:
        try:
            _append(row)
        except OSError:
            pass
    return row


def _read(run_id=""):
    rows = []
    try:
        files = sorted(_DIR.glob("checkpoints-*.jsonl"), reverse=True)
    except OSError:
        files = []
    for path in files[:24]:
        try:
            if path.stat().st_size > 4 * 1024 * 1024:
                continue
            for line in path.read_text(encoding="utf-8").splitlines()[-50000:]:
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if isinstance(row, dict) and (not run_id or row.get("run_id") == run_id):
                    rows.append(row)
        except OSError:
            continue
    return rows


def replay_preview(run_id):
    """Return latest checkpoint per step and whether automatic replay is safe."""
    with _LOCK:
        rows = _read(str(run_id or "")[:80])
    latest = {}
    starts = {}
    finishes = {}
    for row in rows:
        try:
            key = int(row.get("step"))
        except (TypeError, ValueError):
            continue
        if key < 0:
            continue
        latest[key] = dict(latest.get(key) or {}, **row)
        if row.get("event") == "start":
            starts[key] = row
        elif row.get("event") == "finish":
            finishes[key] = row
    steps = []
    for key in sorted(latest):
        item = dict(latest[key])
        if key in starts:
            item.setdefault("prompt_sha256", starts[key].get("prompt_sha256"))
            item.setdefault("attempt", starts[key].get("attempt", 1))
        steps.append(item)
    unknown = next((x for x in steps if x.get("status") in ("unknown", "running")
                    or x.get("event") != "finish"
                    or x.get("step") not in finishes
                    or x.get("step") not in starts), None)
    if not steps:
        unknown = {"reason": "checkpoint_not_found"}
    total_steps = len(steps)
    max_preview_steps = 200
    steps = steps[-max_preview_steps:]
    return {"run_id": str(run_id or ""), "steps": steps,
            "total_steps": total_steps,
            "truncated": total_steps > max_preview_steps,
            "replayable": unknown is None,
            "blocked_reason": ("checkpoint_not_found" if not steps else
                               "requires_reconciliation" if unknown else "")}


def replay(run_id):
    """Alias used by integrations; intentionally returns a preview only."""
    return replay_preview(run_id)


def _snapshot_dir(run_id):
    key = hashlib.sha256(str(run_id or "").encode("utf-8")).hexdigest()
    return _DIR / "files" / key


def _safe_snapshot_path(workdir, path):
    try:
        if not workdir:
            return None, None
        root = Path(workdir).expanduser().resolve(strict=True)
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = root / target
        target = target.resolve(strict=False)
        if target == root or root not in target.parents:
            return None, None
        rel = target.relative_to(root).as_posix()
        if not rel or any(part in ("", ".", "..") for part in Path(rel).parts):
            return None, None
        # Reject symlink traversal, including dangling links.
        cursor = root
        for part in Path(rel).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                return None, None
        return root, rel
    except (OSError, TypeError, ValueError):
        return None, None


def _snapshot_file(run_id, rel):
    name = hashlib.sha256(rel.encode("utf-8")).hexdigest() + ".json"
    return _snapshot_dir(run_id) / name


def _load_snapshot(run_id, rel):
    try:
        obj = json.loads(_snapshot_file(run_id, rel).read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) and obj.get("path") == rel else None
    except (OSError, ValueError, TypeError):
        return None


def capture_file(run_id, workdir, path):
    """Persist the first pre-edit bytes for a workspace-relative file.

    ≤1MB 内嵌进快照 JSON（b64）；更大的文件（连载全书稿这类持续增长的
    tracked 脏文件）前态进工作区 git 对象库，不再顶着 1MB 上限把整步拒死
    （2026-10-10 实案：manuscript.md 涨到 1.14MB 后全部评审步被「已有改动
    无法完整快照」拒起 CLI）。对象库也存不进（非 git 目录）才维持拒绝：
    起跑闸宁拒不假装可恢复。
    """
    root, rel = _safe_snapshot_path(workdir, path)
    if root is None:
        return {"ok": False, "reason": "path_outside_workspace"}
    with _LOCK:
        if _load_snapshot(run_id, rel):
            return {"ok": True, "existing": True, "path": rel}
        snap_dir = _snapshot_dir(run_id)
        try:
            snap_dir.mkdir(parents=True, exist_ok=True)
            total = sum(p.stat().st_size for p in snap_dir.glob("*.json"))
            target = root / rel
            existed = target.exists()
            raw = target.read_bytes() if existed else b""
            if len(raw) > _SNAPSHOT_MAX_BYTES:
                return _write_oversize_snapshot(run_id, root, rel, existed,
                                                file_path=target)
            if total + len(raw) > _SNAPSHOT_TOTAL_MAX_BYTES:
                # b64 预算满：大内容照样可以走对象库，别在这里误杀
                return _write_oversize_snapshot(run_id, root, rel, existed,
                                                file_path=target) if raw else \
                    {"ok": False, "reason": "snapshot_size_limit", "path": rel}
            return _write_snapshot(run_id, rel, existed, raw=raw)
        except OSError as exc:
            return {"ok": False, "reason": "snapshot_io_error", "path": rel,
                    "detail": str(exc)[:200]}


def capture_bytes(run_id, workdir, path, raw, *, existed=True):
    """Persist an explicit pre-step byte image (used for clean Git baseline files).

    超限字节流同样进 git 对象库兜底：finish 阶段从 HEAD 取回的 clean 基线
    （如归档后的整本 manuscript.md）此前只能给 warning，现在也能完整留底。
    """
    root, rel = _safe_snapshot_path(workdir, path)
    if root is None:
        return {"ok": False, "reason": "path_outside_workspace"}
    raw = bytes(raw or b"") if existed else b""
    if len(raw) > _SNAPSHOT_MAX_BYTES:
        return _write_oversize_snapshot(run_id, root, rel, existed, raw=raw)
    with _LOCK:
        if _load_snapshot(run_id, rel):
            return {"ok": True, "existing": True, "path": rel}
        snap_dir = _snapshot_dir(run_id)
        try:
            snap_dir.mkdir(parents=True, exist_ok=True)
            total = sum(p.stat().st_size for p in snap_dir.glob("*.json") if p.is_file())
            if total + len(raw) > _SNAPSHOT_TOTAL_MAX_BYTES:
                return {"ok": False, "reason": "snapshot_size_limit", "path": rel}
            return _write_snapshot(run_id, rel, existed, raw=raw)
        except OSError as exc:
            return {"ok": False, "reason": "snapshot_io_error", "path": rel,
                    "detail": str(exc)[:200]}


def _git_bytes(workdir, *args, data=None):
    try:
        return subprocess.run(["git", "-C", str(workdir), *args],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              input=data,
                              timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def _git_blob_sha(workdir, *, file_path=None, raw=None):
    """把内容存进工作区 git 仓库的对象库，返回 blob sha（空串=失败）。

    超限文件的前态走这里：blob 内容寻址、批间归档提交后自然转为被引用；
    未被引用期间也只在 git gc 的两周宽限后才可能被清，与检查点生命周期同量级。
    """
    if file_path is not None:
        res = _git_bytes(workdir, "hash-object", "-w", "--", str(file_path))
    else:
        res = _git_bytes(workdir, "hash-object", "-w", "--stdin", data=bytes(raw or b""))
    if not res or res.returncode:
        return ""
    sha = os.fsdecode(res.stdout).strip()
    return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else ""


def _snapshot_before_bytes(item, root):
    """快照的前态字节：小文件取 JSON 内嵌 b64，超限文件回对象库取 blob。

    返回 None = 前态不可得（对象库不可用或已被清理），调用方按不可恢复处理。
    """
    b64 = item.get("before_b64")
    if b64:
        return base64.b64decode(b64, validate=True)
    sha = item.get("before_git_blob") or ""
    if sha:
        res = _git_bytes(root, "cat-file", "blob", sha)
        if res is not None and not res.returncode:
            return res.stdout
    return None


def _write_snapshot(run_id, rel, existed, raw=None, blob_sha=""):
    """落一份前态快照记录：小文件内嵌 b64，超限文件只记 blob 引用。"""
    item = {"path": rel, "existed": bool(existed),
            "before_sha256": hashlib.sha256(raw or b"").hexdigest() if existed else "",
            "after_sha256": None, "captured_at": time.time()}
    if blob_sha:
        item["before_git_blob"] = blob_sha
    else:
        item["before_b64"] = base64.b64encode(raw or b"").decode("ascii")
    _snapshot_file(run_id, rel).write_text(
        json.dumps(item, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return {"ok": True, "existing": False, "path": rel}


def _write_oversize_snapshot(run_id, root, rel, existed, *, file_path=None, raw=None):
    """超限前态的落盘：存进 git 对象库；存不进（非 git 目录）才维持拒绝。"""
    if file_path is not None:
        sha = _git_blob_sha(root, file_path=file_path)
    else:
        sha = _git_blob_sha(root, raw=raw)
    if not sha:
        return {"ok": False, "reason": "snapshot_size_limit", "path": rel}
    with _LOCK:
        if _load_snapshot(run_id, rel):
            return {"ok": True, "existing": True, "path": rel}
        try:
            _snapshot_dir(run_id).mkdir(parents=True, exist_ok=True)
            return _write_snapshot(run_id, rel, existed, blob_sha=sha)
        except OSError as exc:
            return {"ok": False, "reason": "snapshot_io_error", "path": rel,
                    "detail": str(exc)[:200]}


def _git_changed_paths(workdir, gitmod):
    """Return every changed path, including both sides of renames/copies."""
    result = _git_bytes(workdir, "status", "--porcelain", "-z", "--untracked-files=all")
    if not result or result.returncode:
        return None
    fields = result.stdout.split(b"\0")
    paths = []
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if len(field) < 4:
            continue
        status = os.fsdecode(field[:2])
        rel = os.fsdecode(field[3:])
        if not rel:
            continue
        # The destination of a Git rename/copy did not exist at the pinned HEAD;
        # the following NUL field is its source path and is captured separately.
        paths.append(("??" if "R" in status or "C" in status else status, rel))
        if "R" in status or "C" in status:
            if index < len(fields) and fields[index]:
                old_rel = os.fsdecode(fields[index])
                paths.append(("D", old_rel))
            index += 1
    return [(gitmod._status_code(status), rel) for status, rel in paths
            if not (status == "?" and gitmod._attach_path(rel))
            and rel != ".codebee" and not rel.startswith(".codebee/")]


def begin_git_step(run_id, workdir):
    """Capture pre-existing dirty Git files before an external CLI step starts.

    Clean tracked files can be reconstructed from the pinned HEAD at finish time;
    dirty/untracked files must be captured now or the step is refused.
    """
    root = Path(workdir or "").expanduser().resolve()
    probe = _git_bytes(root, "rev-parse", "--show-toplevel")
    if not probe or probe.returncode:
        return {"ok": True, "supported": False,
                "warning": "非 Git 工作目录：外部 CLI 文件改动无法生成可恢复快照"}
    repo_root = Path(os.fsdecode(probe.stdout).strip()).resolve()
    if repo_root != root:
        return {"ok": True, "supported": False,
                "warning": "工作目录不是 Git 仓库根目录：外部 CLI 文件改动无法安全快照"}
    head = _git_bytes(root, "rev-parse", "HEAD")
    if not head or head.returncode:
        return {"ok": True, "supported": False,
                "warning": "Git 仓库没有可用 HEAD：外部 CLI 文件改动无法安全快照"}
    try:
        from . import gitmod
        entries = _git_changed_paths(root, gitmod)
        if entries is None:
            return {"ok": False, "error": "无法读取 Git 工作区状态，未启动外部 CLI"}
    except Exception:
        return {"ok": False, "error": "无法读取 Git 工作区状态，未启动外部 CLI"}
    changes = []
    for status, rel in entries:
        rel = str(rel or "")
        if not rel:
            continue
        changes.append({"status": gitmod._status_code(status), "path": rel})
    before_paths = []
    for row in changes:
        rel = str(row.get("path") or "")
        if not rel:
            continue
        saved = capture_file(run_id, root, root / rel)
        if not saved.get("ok"):
            return {"ok": False, "error": "已有改动无法完整快照（%s），未启动外部 CLI" % rel}
        before_paths.append(rel)
    return {"ok": True, "supported": True,
            "head": os.fsdecode(head.stdout).strip(),
            "before_paths": before_paths}


def finish_git_step(run_id, workdir, head, before_paths=()):
    """Reconcile Git-visible CLI changes against pre-step state and mark restore hashes."""
    root = Path(workdir or "").expanduser().resolve()
    if not head:
        return {"ok": False, "captured": 0, "warning": "缺少 Git 基线，未生成外部 CLI 文件快照"}
    current_head = _git_bytes(root, "rev-parse", "HEAD")
    if not current_head or current_head.returncode or os.fsdecode(current_head.stdout).strip() != head:
        return {"ok": False, "captured": 0,
                "warning": "步骤期间 Git HEAD 发生变化，快照无法安全对账"}
    try:
        from . import gitmod
        entries = _git_changed_paths(root, gitmod)
        if entries is None:
            return {"ok": False, "captured": 0, "warning": "无法读取 Git 变更清单"}
    except Exception:
        return {"ok": False, "captured": 0, "warning": "无法读取 Git 变更清单"}
    changes = []
    for status, rel in entries:
        rel = str(rel or "")
        if not rel:
            continue
        changes.append({"status": gitmod._status_code(status), "path": rel})
    by_path = {str(row.get("path") or ""): row for row in changes if row.get("path")}
    for rel in before_paths or ():
        by_path.setdefault(str(rel), {"path": str(rel), "status": ""})
    captured = 0
    failed = []
    for row in by_path.values():
        rel = str(row.get("path") or "")
        if not rel:
            continue
        item = _load_snapshot(run_id, rel)
        if item is None:
            if row.get("status") == "?":
                saved = capture_bytes(run_id, root, root / rel, b"", existed=False)
            else:
                # -- literal path suffix prevents pathspec interpretation; bytes
                # preserve binary files and exact line endings.
                base = _git_bytes(root, "show", "%s:%s" % (head, rel))
                if not base or base.returncode:
                    failed.append(rel)
                    continue
                saved = capture_bytes(run_id, root, root / rel, base.stdout, existed=True)
            if not saved.get("ok"):
                failed.append(rel)
                continue
            captured += 1
        if not mark_file_after(run_id, root, root / rel):
            failed.append(rel)
    warning = "仅覆盖 Git 可见文件改动；忽略文件、shell/MCP 与外部服务副作用不保证可回滚。"
    if failed:
        warning += "以下路径未能完整快照，不能保证恢复：%s。" % "、".join(failed[:20])
        if len(failed) > 20:
            warning += "另有 %d 项。" % (len(failed) - 20)
    return {"ok": not failed, "captured": captured, "failed_paths": failed,
            "warning": warning}


def mark_file_after(run_id, workdir, path):
    """Record the post-edit fingerprint used to detect restore conflicts."""
    root, rel = _safe_snapshot_path(workdir, path)
    if root is None:
        return False
    with _LOCK:
        item = _load_snapshot(run_id, rel)
        if not item:
            return False
        target = root / rel
        exists = target.is_file() and not target.is_symlink()
        if exists:
            raw = target.read_bytes()
            item["after_sha256"] = hashlib.sha256(raw).hexdigest()
        else:
            item["after_sha256"] = None
        item["after_exists"] = exists
        try:
            _snapshot_file(run_id, rel).write_text(
                json.dumps(item, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            return True
        except OSError:
            return False


def snapshot_preview(run_id, workdir):
    """Return bounded snapshot status and text diffs; shell side effects excluded."""
    if not workdir:
        return {"run_id": str(run_id or ""), "files": [], "warning": "工作目录不可用"}
    root, _ = _safe_snapshot_path(workdir, Path(workdir) / "sentinel")
    if root is None:
        try:
            root = Path(workdir).expanduser().resolve(strict=True)
        except (OSError, TypeError, ValueError):
            return {"run_id": str(run_id or ""), "files": [], "warning": "工作目录不可用"}
    files = []
    with _LOCK:
        for file in sorted(_snapshot_dir(run_id).glob("*.json")):
            try:
                item = json.loads(file.read_text(encoding="utf-8"))
                rel = item.get("path")
                safe_root, safe_rel = _safe_snapshot_path(root, root / rel)
                if safe_root is None or safe_rel != rel:
                    if isinstance(rel, str) and rel and not Path(rel).is_absolute() and ".." not in Path(rel).parts:
                        files.append({"path": rel, "status": "unsafe_path",
                                      "before_sha256": item.get("before_sha256"),
                                      "current_sha256": None, "restorable": False,
                                      "diff": "路径已变为符号链接或超出工作区，拒绝恢复"})
                    continue
                target = root / rel
                current = target.read_bytes() if target.is_file() and not target.is_symlink() else None
                current_hash = hashlib.sha256(current).hexdigest() if current is not None else None
                before = _snapshot_before_bytes(item, root)
                status = "unchanged" if current_hash == item.get("before_sha256") else "changed"
                if "after_exists" in item:
                    can_restore = (bool(item.get("after_exists")) == (current is not None)
                                   and item.get("after_sha256") == current_hash)
                elif item.get("after_sha256") is None and status == "changed":
                    # Old snapshot records did not encode post-state existence.
                    can_restore = not item.get("existed") and current is not None
                else:
                    can_restore = item.get("after_sha256") == current_hash
                diff = ""
                if before is None and item.get("before_git_blob"):
                    diff = "（大文件前态存于 git 对象库且当前不可读取，差异省略）"
                elif current is not None and item.get("existed") and before is not None:
                    try:
                        old_text = before.decode("utf-8").splitlines(True)
                        new_text = current.decode("utf-8").splitlines(True)
                        diff = "".join(difflib.unified_diff(old_text, new_text,
                                                           fromfile="before/" + rel,
                                                           tofile="current/" + rel))[:12000]
                    except UnicodeDecodeError:
                        diff = "二进制文件，无法生成文本差异"
                files.append({"path": rel, "status": status,
                              "before_sha256": item.get("before_sha256"),
                              "current_sha256": current_hash,
                              "restorable": bool(can_restore), "diff": diff})
            except (OSError, ValueError, TypeError):
                continue
    return {"run_id": str(run_id or ""), "files": files,
            "warning": "仅覆盖已记录的工作区文件；shell 命令、MCP 与外部服务副作用不保证可回滚。恢复会检查指纹并拒绝符号链接，但不防范恶意本地进程在校验与写入之间替换父目录。"}


def _restore_file_posix(root, rel, item, expected_exists, expected_hash):
    """Restore using directory handles so parent-path swaps cannot escape root."""
    required = (os.open in getattr(os, "supports_dir_fd", set())
                and os.mkdir in getattr(os, "supports_dir_fd", set())
                and _SUPPORTS_DIR_FD_RENAME
                and os.unlink in getattr(os, "supports_dir_fd", set()))
    if not required:
        return False
    parts = Path(rel).parts
    if not parts:
        return False
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    root_fd = os.open(str(root), directory_flags)
    parent_fd = root_fd
    owned_fds = []
    missing_parent = False
    try:
        for part in parts[:-1]:
            try:
                child_fd = os.open(part, directory_flags, dir_fd=parent_fd)
            except FileNotFoundError:
                missing_parent = True
                break
            owned_fds.append(child_fd)
            parent_fd = child_fd

        leaf = parts[-1]
        current = None
        if not missing_parent:
            try:
                file_fd = os.open(leaf, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                                  dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            else:
                try:
                    if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                        return False
                    # 全量流式哈希：截断读会让超限文件（前态走对象库的那种）
                    # 的指纹恒不匹配、永远拒恢复。
                    digest = hashlib.sha256()
                    while True:
                        chunk = os.read(file_fd, 1 << 20)
                        if not chunk:
                            break
                        digest.update(chunk)
                    current = digest.hexdigest()
                finally:
                    os.close(file_fd)
        current_exists = current is not None
        current_hash = current if current_exists else None
        if current_exists != bool(expected_exists) or current_hash != expected_hash:
            return False

        if item.get("existed"):
            raw = _snapshot_before_bytes(item, root)
            if raw is None:
                return False
            if missing_parent:
                parent_fd = root_fd
                for part in parts[:-1]:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=parent_fd)
                    except FileExistsError:
                        pass
                    child_fd = os.open(part, directory_flags, dir_fd=parent_fd)
                    owned_fds.append(child_fd)
                    parent_fd = child_fd
            temp_name = ".codebee-restore-%s.tmp" % secrets.token_hex(12)
            temp_fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                              | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent_fd)
            try:
                with os.fdopen(temp_fd, "wb", closefd=False) as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(temp_fd)
                os.close(temp_fd)
                temp_fd = None
                os.rename(temp_name, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            except Exception:
                if temp_fd is not None:
                    os.close(temp_fd)
                try:
                    os.unlink(temp_name, dir_fd=parent_fd)
                except OSError:
                    pass
                raise
        elif current_exists:
            os.unlink(leaf, dir_fd=parent_fd)
        return True
    finally:
        for fd in reversed(owned_fds):
            os.close(fd)
        os.close(root_fd)


def restore_files(run_id, workdir):
    """Restore only files whose current fingerprint still matches our post-edit state."""
    if not workdir:
        return {"restored": 0, "conflicts": [], "error": "工作目录不可用"}
    preview = snapshot_preview(run_id, workdir)
    if not preview.get("files") and preview.get("warning") == "工作目录不可用":
        return {"restored": 0, "conflicts": [], "error": "工作目录不可用"}
    restored, conflicts = 0, []
    root = Path(workdir).expanduser().resolve()
    with _LOCK:
        for view in preview.get("files") or []:
            rel = view.get("path")
            item = _load_snapshot(run_id, rel)
            safe_root, safe_rel = _safe_snapshot_path(root, root / rel)
            if not item or safe_root is None or safe_rel != rel or not view.get("restorable"):
                conflicts.append(rel)
                continue
            if os.name == "posix":
                try:
                    did_restore = _restore_file_posix(
                        root, rel, item, view.get("current_sha256") is not None,
                        view.get("current_sha256"))
                except (OSError, ValueError):
                    did_restore = False
                if did_restore:
                    restored += 1
                else:
                    conflicts.append(rel)
                continue
            target = root / rel
            try:
                # Re-check after preview to avoid overwriting edits made while the
                # restore request was being prepared.
                current_exists = target.is_file() and not target.is_symlink()
                current_raw = target.read_bytes() if current_exists else None
                current_hash = hashlib.sha256(current_raw).hexdigest() if current_raw is not None else None
                if (current_exists != bool(item.get("after_exists"))
                        or current_hash != item.get("after_sha256")):
                    conflicts.append(rel)
                    continue
                if item.get("existed"):
                    raw = _snapshot_before_bytes(item, root)
                    if raw is None:
                        conflicts.append(rel)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    safe_root, safe_rel = _safe_snapshot_path(root, root / rel)
                    if safe_root is None or safe_rel != rel:
                        conflicts.append(rel)
                        continue
                    temp_path = None
                    try:
                        with tempfile.NamedTemporaryFile(mode="wb", dir=str(target.parent),
                                                         prefix=".codebee-restore-", delete=False) as handle:
                            temp_path = Path(handle.name)
                            handle.write(raw)
                            handle.flush()
                            os.fsync(handle.fileno())
                        # Revalidate immediately before replacement. os.replace replaces a
                        # raced-in leaf symlink itself instead of following its destination.
                        safe_root, safe_rel = _safe_snapshot_path(root, root / rel)
                        if safe_root is None or safe_rel != rel:
                            conflicts.append(rel)
                            continue
                        os.replace(str(temp_path), str(target))
                        temp_path = None
                    finally:
                        if temp_path is not None:
                            try:
                                temp_path.unlink()
                            except OSError:
                                pass
                elif target.exists() and target.is_file() and not target.is_symlink():
                    target.unlink()
                restored += 1
            except OSError:
                conflicts.append(rel)
    return {"restored": restored, "conflicts": conflicts,
            "warning": "仅恢复已记录的工作区文件；shell 命令、MCP 与外部服务副作用不保证可回滚。恢复会检查指纹并拒绝符号链接，但不防范恶意本地进程在校验与写入之间替换父目录。"}
