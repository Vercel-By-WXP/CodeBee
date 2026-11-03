"""通用工具：IO、指纹、命令执行、轻量 TOML 解析。仅标准库。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

# ---------------------------------------------------------------- 时间 / IO


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def read_text(path: str, default: str = "") -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return default


def write_text(path: str, content: str) -> None:
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    os.replace(tmp, path)


def read_json(path: str, default: Any = None) -> Any:
    raw = read_text(path, "")
    if not raw.strip():
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def write_json(path: str, data: Any) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def append_jsonl(path: str, record: Dict[str, Any]) -> None:
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for line in read_text(path, "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


# ---------------------------------------------------------------- 指纹

_NORM_RULES: Sequence[tuple] = (
    (re.compile(r"0x[0-9a-fA-F]{4,}"), "0xADDR"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*"), "<ts>"),
    # 引号里的绝对/相对路径：只留文件名，抹掉目录前缀（保留文件名才能区分不同模块）
    (re.compile(r'"(?:[A-Za-z]:)?(?:[^"\s]*[/\\])+([^"\\/]+)"'), r'"<dir>/\1"'),
    (re.compile(r"[A-Za-z]:\\[^:\s\"]+"), "<path>"),
    (re.compile(r"\bline\s+\d+\b", re.I), "line N"),
    (re.compile(r":\d+(?::\d+)?\b"), ":N"),
    (re.compile(r"\b\d+\b"), "N"),
    (re.compile(r"\s+"), " "),
)


def normalize_message(text: str, limit: int = 400) -> str:
    """抹掉行号/地址/绝对路径/数字，让同类错误收敛到同一指纹。"""
    if not text:
        return ""
    out = text.strip()
    for pattern, repl in _NORM_RULES:
        out = pattern.sub(repl, out)
    return out[:limit].strip().lower()


def fingerprint(*parts: str) -> str:
    basis = "\u0001".join(normalize_message(p) for p in parts if p)
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- 命令


def has_bin(name: str) -> bool:
    return shutil.which(name) is not None


def q(text: str) -> str:
    """给 shell 命令拼一个安全引号（单引号包裹 + 转义内部单引号）。"""
    return "'" + str(text).replace("'", "'\\''") + "'"


def split_cmd(cmd: str) -> List[str]:
    import shlex

    try:
        tokens = shlex.split(cmd, posix=(os.name != "nt"))
    except ValueError:
        tokens = cmd.split()
    return [t.strip('"').strip("'") for t in tokens]


def decode_output(raw: bytes) -> str:
    """子进程输出解码：先按 UTF-8，失败退 GB18030。

    中文 Windows 的 cmd.exe / 部分工具输出是 GBK，直接按 UTF-8 解会得到
    一串问号，日志里看不出任何有用信息。
    """
    if not raw:
        return ""
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def run_cmd(
    cmd: str,
    cwd: str = ".",
    timeout: int = 600,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """执行命令并返回结构化结果。不做 shell 转义猜测：字符串直接交给 shell。"""
    merged = dict(os.environ)
    merged.setdefault("PYTHONIOENCODING", "utf-8")
    if env:
        merged.update(env)
    started = datetime.now()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env=merged,
        )
        code = proc.returncode
        output = decode_output(proc.stdout or b"")
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        code = 124
        raw = exc.stdout or b""
        output = decode_output(raw) if isinstance(raw, bytes) else str(raw)
        output += f"\n[zfix] 超时 {timeout}s，已终止"
        timed_out = True
    except OSError as exc:
        code = 127
        output = f"[zfix] 无法执行命令：{exc}"
        timed_out = False
    elapsed = int((datetime.now() - started).total_seconds())
    return {
        "cmd": cmd,
        "cwd": os.path.abspath(cwd),
        "exit_code": code,
        "ok": code == 0,
        "timed_out": timed_out,
        "duration_s": elapsed,
        "output": output,
        "tail": "\n".join(output.splitlines()[-40:]),
    }


def git(args: str, cwd: str = ".") -> Dict[str, Any]:
    return run_cmd("git " + args, cwd=cwd, timeout=120)


def git_branch(cwd: str = ".") -> str:
    res = git("rev-parse --abbrev-ref HEAD", cwd=cwd)
    return res["output"].strip() if res["ok"] else ""


def git_diff(cwd: str = ".", staged: bool = False) -> str:
    flag = "--cached" if staged else ""
    res = git(f"diff {flag} --no-color --unified=0", cwd=cwd)
    return res["output"] if res["ok"] else ""


# ---------------------------------------------------------------- TOML

_MINI_HEADER = re.compile(r"^\[([A-Za-z0-9_.\-]+)\]\s*(?:#.*)?$")
_MINI_KV = re.compile(r"^([A-Za-z0-9_.\-]+)\s*=\s*(.+?)\s*(?:#\s*[^\"']*)?$")


def _coerce(raw: str) -> Any:
    val = raw.strip()
    if not val:
        return ""
    if val.startswith('"') and val.endswith('"') and len(val) >= 2:
        return val[1:-1].replace('\\"', '"').replace("\\n", "\n")
    if val.startswith("'") and val.endswith("'") and len(val) >= 2:
        return val[1:-1]
    low = val.lower()
    if low in ("true", "false"):
        return low == "true"
    if val.startswith("[") and val.endswith("]"):
        body = val[1:-1].strip()
        if not body:
            return []
        parts: List[str] = []
        buf = ""
        in_str = False
        for ch in body:
            if ch in "\"'":
                in_str = not in_str
                buf += ch
            elif ch == "," and not in_str:
                parts.append(buf)
                buf = ""
            else:
                buf += ch
        parts.append(buf)
        return [_coerce(p) for p in parts if p.strip()]
    try:
        return int(val)
    except ValueError:
        try:
            return float(val)
        except ValueError:
            return val


def _mini_toml(text: str) -> Dict[str, Any]:
    """极简 TOML（节 + 标量 + 数组），供 Python < 3.11 兜底。"""
    data: Dict[str, Any] = {}
    cursor = data
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        head = _MINI_HEADER.match(stripped)
        if head:
            cursor = data
            for part in head.group(1).split("."):
                cursor = cursor.setdefault(part, {})
            continue
        kv = _MINI_KV.match(stripped)
        if kv:
            cursor[kv.group(1)] = _coerce(kv.group(2))
    return data


def load_toml(path: str) -> Dict[str, Any]:
    raw = read_text(path, "")
    if not raw.strip():
        return {}
    try:
        import tomllib  # py>=3.11
    except ModuleNotFoundError:  # pragma: no cover
        try:
            import tomli as tomllib  # type: ignore
        except ModuleNotFoundError:
            return _mini_toml(raw)
    try:
        return tomllib.loads(raw)
    except Exception:
        return _mini_toml(raw)


def py_ok() -> bool:
    return sys.version_info >= (3, 8)


def slug(text: str) -> str:
    out = re.sub(r"[^A-Za-z0-9._-]+", "-", (text or "").strip()).strip("-")
    return out.lower() or "unnamed"
