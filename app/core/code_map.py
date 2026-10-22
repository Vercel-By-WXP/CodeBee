# -*- coding: utf-8 -*-
"""Git-versioned code cognition map for task prompts and built-in tools.

One line per file: ``path[tags]: F:职责 | R:关联文件 | A:对外接口 | S:关键约束``.
The map lives in ``<workspace>/.codebee/code-map.txt`` so it can be reviewed
and committed with the code.  Generation is deterministic and standard-library
only; model-written entries are not required for the structural baseline.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import posixpath
import re
import subprocess
import time
from pathlib import Path

from . import atomic

SCHEMA = 1
MAP_DIR = ".codebee"
MAP_NAME = "code-map.txt"
META_NAME = "code-map.json"
MAX_FILES = 20000
MAX_PARSE_BYTES = 512 * 1024
MAX_FIELD_CHARS = 220
MAX_RELATIONS = 8
MAX_API = 10
PROMPT_BUDGET = 2600
PROMPT_MAX_ENTRIES = 30

EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", ".codebee", "_attachments", "node_modules",
    "__pycache__", ".venv", "venv", "env", ".tox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".cache", ".idea", ".vscode",
    "dist", "build", "out", "target", "coverage", ".next", ".nuxt",
    "vendor", "Pods", "DerivedData",
}
EXCLUDE_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock",
    "poetry.lock", "Pipfile.lock", "composer.lock", "go.sum",
}
EXCLUDE_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg",
    ".pdf", ".zip", ".gz", ".tar", ".7z", ".rar", ".jar", ".class",
    ".exe", ".dll", ".so", ".dylib", ".woff", ".woff2", ".ttf", ".otf",
    ".mp3", ".mp4", ".mov", ".avi", ".webm", ".sqlite", ".db", ".bin",
    ".map", ".min.js", ".min.css",
}
TEXT_SUFFIXES = {
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue",
    ".go", ".rs", ".java", ".kt", ".kts", ".swift", ".c", ".h", ".cc",
    ".cpp", ".hpp", ".cs", ".rb", ".php", ".sh", ".bash", ".ps1", ".bat",
    ".cmd", ".sql", ".html", ".htm", ".css", ".scss", ".less", ".md",
    ".rst", ".txt", ".yaml", ".yml", ".toml", ".json", ".ini", ".cfg",
    ".conf", ".xml", ".gradle", ".properties",
}
SPECIAL_NAMES = {"dockerfile", "makefile", "justfile", "procfile", "gemfile", "rakefile"}

_LANG = {
    ".py": "py", ".pyi": "py", ".js": "js", ".mjs": "js", ".cjs": "js",
    ".ts": "ts", ".tsx": "ts", ".jsx": "js", ".vue": "vue", ".go": "go",
    ".rs": "rs", ".java": "java", ".kt": "kt", ".swift": "swift",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp",
    ".cs": "cs", ".rb": "rb", ".php": "php", ".sh": "sh", ".bash": "sh",
    ".ps1": "ps", ".bat": "bat", ".cmd": "bat", ".sql": "sql",
    ".md": "md", ".rst": "md", ".html": "html", ".htm": "html",
    ".css": "css", ".scss": "css", ".less": "css", ".yaml": "cfg",
    ".yml": "cfg", ".toml": "cfg", ".json": "cfg", ".ini": "cfg",
    ".cfg": "cfg", ".conf": "cfg", ".xml": "cfg",
    ".gradle": "cfg", ".properties": "cfg",
}

_PY_IMPORT_RE = re.compile(r"^\s*(?:from\s+([.\w]+)\s+import|import\s+([.\w]+))", re.M)
_JS_IMPORT_RE = re.compile(
    r"""(?:import\s+(?:[\w*\s{},]+\s+from\s+)?|export\s+[\w*\s{},]+\s+from\s+|require\()"""
    r"""["']([^"']+)["']""")
_JS_EXPORT_RE = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)",
    re.M)
_GO_IMPORT_RE = re.compile(r'^\s*"([^"]+)"', re.M)
_GO_API_RE = re.compile(r"^\s*(?:func\s+(?:\([^)]*\)\s*)?|type\s+)([A-Z]\w*)", re.M)
_CONSTRAINT_RE = re.compile(
    r"^\s*(?:#|//|/\*|\*)?\s*(?:code-map:\s*S|S|Constraint|约束|NOTE|WARNING|注意|警告)\s*[:：]\s*(.+)$",
    re.I | re.M)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _norm_rel(value):
    rel = str(value or "").replace("\\", "/").strip("/")
    if not rel or rel.startswith("../") or rel == "..":
        return ""
    norm = posixpath.normpath(rel)
    if norm.startswith("../") or norm == ".." or os.path.isabs(norm):
        return ""
    return norm


def _excluded(rel):
    parts = rel.split("/")
    if any(p in EXCLUDE_DIRS for p in parts[:-1]):
        return True
    name = parts[-1].lower()
    if name in EXCLUDE_FILES:
        return True
    if name.startswith(".") and name not in {".env.example"}:
        return True
    if any(name.endswith(s) for s in EXCLUDE_SUFFIXES):
        return True
    return False


def _is_text_name(rel):
    name = rel.rsplit("/", 1)[-1].lower()
    if name in SPECIAL_NAMES or name.startswith("dockerfile."):
        return True
    return Path(name).suffix.lower() in TEXT_SUFFIXES


def _root(workdir):
    value = str(workdir or "").strip()
    if not value:
        raise ValueError("工作目录不能为空")
    root = Path(value).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("工作目录不可用")
    return root


def _git_files(root):
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    rows = []
    for raw in proc.stdout.decode("utf-8", "replace").split("\0"):
        rel = _norm_rel(raw)
        if rel:
            rows.append(rel)
    return rows


def _walk_files(root):
    rows = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in EXCLUDE_DIRS and not (Path(dirpath) / d).is_symlink())
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue
            rel = _norm_rel(path.relative_to(root))
            if rel:
                rows.append(rel)
    return rows


def _list_files(root):
    rows = _git_files(root)
    source = "git"
    if rows is None:
        rows = _walk_files(root)
        source = "walk"
    rows = [r for r in rows if not _excluded(r) and _is_text_name(r)]
    rows.sort()
    return rows[:MAX_FILES], source


def _read_text(path):
    try:
        raw = path.read_bytes()[:MAX_PARSE_BYTES]
    except OSError:
        return ""
    if b"\x00" in raw[:8192] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return ""
    for enc in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return ""


def _clean(value, limit=MAX_FIELD_CHARS):
    text = re.sub(r"\s+", " ", str(value or "")).strip(" \t|")
    return text[:limit]


def _humanize(rel):
    stem = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    words = re.sub(r"[_\-]+", " ", stem).strip()
    return words or rel


def _doc_first_line(doc):
    for line in str(doc or "").splitlines():
        line = _clean(line.strip().lstrip("#").strip())
        if line:
            return line
    return ""


def _leading_comment(text, marker="#"):
    for line in text.splitlines()[:40]:
        raw = line.strip()
        if not raw or raw.startswith(("\"\"\"", "'''", "import ", "from ", "package ")):
            if raw.startswith(("\"\"\"", "'''")):
                continue
            continue
        if marker == "#" and raw.startswith("#") and not raw.startswith(("#!", "# -*-")):
            return _clean(raw.lstrip("#"))
        if marker == "//" and raw.startswith("//"):
            return _clean(raw.lstrip("/"))
        if marker == "/*" and raw.startswith("/*"):
            return _clean(raw.lstrip("/*").rstrip("*/"))
        if marker == "#" and raw.startswith("#"):
            return _clean(raw.lstrip("#"))
    return ""


def _constraints(text, doc=""):
    found = []
    hay = (doc or "") + "\n" + (text or "")
    for match in _CONSTRAINT_RE.finditer(hay):
        value = _clean(match.group(1), 160)
        if value and value not in found:
            found.append(value)
        if len(found) >= 3:
            break
    return found


def _resolve_candidate(base_dir, target, known):
    target = str(target or "").replace("\\", "/")
    raw = posixpath.normpath(posixpath.join(base_dir, target)) if base_dir else posixpath.normpath(target)
    if raw.startswith("../") or raw == "..":
        return ""
    candidates = [raw]
    suffix = Path(raw).suffix.lower()
    if not suffix:
        candidates.extend([raw + ext for ext in (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".vue")])
        candidates.extend(posixpath.join(raw, "index" + ext)
                          for ext in (".ts", ".tsx", ".js", ".jsx", ".vue"))
    return next((c for c in candidates if c in known), "")


def _python_entry(rel, text, known):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return _regex_entry(rel, text, known)
    doc = ast.get_docstring(tree, clean=True) or ""
    api = []
    imports = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_") and node.name not in api:
                api.append(node.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            prefix = "." * int(node.level or 0) + module
            imports.append(prefix)
            if node.level:
                for alias in node.names:
                    if alias.name != "*":
                        imports.append(prefix + "." + alias.name)
    base = posixpath.dirname(rel)
    relations = []
    for raw in imports:
        if raw.startswith("."):
            dots = len(raw) - len(raw.lstrip("."))
            target = raw.lstrip(".")
            parent = base
            for _ in range(max(0, dots - 1)):
                parent = posixpath.dirname(parent)
            module_path = posixpath.join(parent, target.replace(".", "/")) if target else parent
            candidate = _resolve_candidate("", module_path, known)
            if not candidate and target:
                candidate = _resolve_candidate(parent, target.split(".")[0], known)
        else:
            candidate = _resolve_candidate("", raw.replace(".", "/"), known)
            if not candidate:
                candidate = _resolve_candidate("src", raw.replace(".", "/"), known)
        if candidate and candidate != rel and candidate not in relations:
            relations.append(candidate)
    return doc, api, relations, _constraints(text, doc)


def _regex_entry(rel, text, known):
    base = posixpath.dirname(rel)
    relations = []
    for raw in _PY_IMPORT_RE.findall(text):
        target = raw[0] or raw[1]
        if not target:
            continue
        if target.startswith("."):
            dots = len(target) - len(target.lstrip("."))
            target = target.lstrip(".")
            parent = base
            for _ in range(max(0, dots - 1)):
                parent = posixpath.dirname(parent)
            candidate = _resolve_candidate(parent, target.replace(".", "/"), known)
        else:
            candidate = _resolve_candidate("", target.replace(".", "/"), known)
        if candidate and candidate != rel and candidate not in relations:
            relations.append(candidate)
    api = re.findall(r"^(?:def|class)\s+([A-Za-z_]\w*)", text, re.M)
    api = [x for x in api if not x.startswith("_")]
    return "", api, relations, _constraints(text)


def _js_entry(rel, text, known):
    base = posixpath.dirname(rel)
    relations = []
    for raw in _JS_IMPORT_RE.findall(text):
        if not raw.startswith("."):
            continue
        candidate = _resolve_candidate(base, raw, known)
        if candidate and candidate != rel and candidate not in relations:
            relations.append(candidate)
    api = list(dict.fromkeys(_JS_EXPORT_RE.findall(text)))
    return "", api, relations, _constraints(text)


def _go_entry(rel, text, known):
    api = list(dict.fromkeys(_GO_API_RE.findall(text)))
    relations = []
    for raw in _GO_IMPORT_RE.findall(text):
        local = raw.rsplit("/", 1)[-1]
        candidate = _resolve_candidate("", local, known)
        if candidate and candidate != rel and candidate not in relations:
            relations.append(candidate)
    doc = ""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("package "):
            block = []
            j = i - 1
            while j >= 0 and lines[j].strip().startswith("//"):
                block.insert(0, lines[j].strip().lstrip("/").strip())
                j -= 1
            doc = " ".join(block)
            break
    return doc, api, relations, _constraints(text, doc)


def _markdown_entry(rel, text):
    for line in text.splitlines():
        if line.startswith("#"):
            return _clean(line.lstrip("#")), [], [], _constraints(text)
    return "", [], [], _constraints(text)


def _json_entry(rel, text):
    api = []
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            api = [str(k) for k in list(value)[:MAX_API]]
    except (ValueError, TypeError):
        pass
    return "", api, [], _constraints(text)


def _entry_line(rel, text, known):
    suffix = Path(rel).suffix.lower()
    if suffix in (".py", ".pyi"):
        doc, api, relations, constraints = _python_entry(rel, text, known)
        f = _doc_first_line(doc) or _leading_comment(text) or _humanize(rel)
    elif suffix in (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue"):
        _, api, relations, constraints = _js_entry(rel, text, known)
        f = _leading_comment(text, "//") or _leading_comment(text, "/*") or _humanize(rel)
    elif suffix == ".go":
        doc, api, relations, constraints = _go_entry(rel, text, known)
        f = _doc_first_line(doc) or _humanize(rel)
    elif suffix in (".md", ".rst"):
        f, api, relations, constraints = _markdown_entry(rel, text)
        f = f or _humanize(rel)
    elif suffix == ".json":
        _, api, relations, constraints = _json_entry(rel, text)
        f = _humanize(rel)
    else:
        api, relations, constraints = [], [], _constraints(text)
        f = _leading_comment(text) or _humanize(rel)

    tags = []
    lang = _LANG.get(suffix) or (rel.rsplit("/", 1)[-1].lower() if not suffix else suffix.lstrip("."))
    if lang:
        tags.append(lang)
    lower = rel.lower()
    if "/test" in lower or lower.startswith("test") or "/__tests__/" in lower or ".test." in lower or ".spec." in lower:
        tags.append("test")
    if ("/ui/" in lower or lower.startswith(("ui/", "web/", "frontend/"))
            or "/frontend/" in lower or "/web/" in lower):
        tags.append("ui")
    if any(x in lower for x in ("/migrations/", "/schema/", "openapi")):
        tags.append("contract")

    parts = ["F:" + _clean(f)]
    if relations:
        parts.append("R:" + _clean(",".join(relations[:MAX_RELATIONS]), 300))
    if api:
        parts.append("A:" + _clean(",".join(api[:MAX_API]), 220))
    if constraints:
        parts.append("S:" + _clean("; ".join(constraints), 260))
    line = "%s[%s]: %s" % (rel, ",".join(tags) or "file", " | ".join(parts))
    return line[:900]


def _entry_for(rel, root, known):
    path = root / rel
    try:
        if path.stat().st_size > MAX_PARSE_BYTES:
            return "%s[large]: F:大文件（未解析）" % rel
    except OSError:
        return ""
    text = _read_text(path)
    if not text:
        return "%s[file]: F:二进制或不可读" % rel
    return _entry_line(rel, text, known)


def _paths(root):
    directory = root / MAP_DIR
    return directory / MAP_NAME, directory / META_NAME


def _load_meta(root):
    _, meta_path = _paths(root)
    try:
        value = json.loads(meta_path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _load_lines(root):
    map_path, _ = _paths(root)
    try:
        text = map_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    rows = {}
    for line in text.splitlines():
        match = re.match(r"^(\S.*?)\[[^\]]*\]:\s", line)
        if match:
            rows[match.group(1)] = line
    return rows


def _hash_file(path, want_hash=True):
    h = hashlib.sha256()
    try:
        size = path.stat().st_size
        if want_hash:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    h.update(chunk)
    except OSError:
        return "", 0
    return (h.hexdigest() if want_hash else ""), size


def build(workdir, changed_paths=None):
    root = _root(workdir)
    files, source = _list_files(root)
    current = set(files)
    old_meta = _load_meta(root)
    old_files = old_meta.get("files") if isinstance(old_meta.get("files"), dict) else {}
    lines = _load_lines(root)
    hashes = {}
    stats = {"files": len(files), "generated": 0, "unchanged": 0,
             "removed": 0, "source": source, "root": str(root)}
    targets = set()
    if changed_paths is not None:
        for raw in changed_paths:
            rel = _norm_rel(raw)
            if rel in current:
                targets.add(rel)
    for rel in files:
        # Partial refresh keeps prior entries byte-for-byte.  Files without a
        # recorded hash are generated even then, so the next status cannot
        # report a fresh map with missing metadata.
        if changed_paths is not None and rel not in targets and rel in old_files:
            hashes[rel] = old_files[rel]
            stats["unchanged"] += 1
            continue
        line = _entry_for(rel, root, current)
        if line:
            lines[rel] = line
            ctx = _hash_file(root / rel, True)
            try:
                mtime = (root / rel).stat().st_mtime_ns
            except OSError:
                mtime = 0
            hashes[rel] = {"sha256": ctx[0], "size": ctx[1], "mtime_ns": mtime,
                           "lang": _LANG.get(Path(rel).suffix.lower(), "")}
            stats["generated"] += 1
        else:
            stats["unchanged"] += 1
    for rel in list(lines):
        if rel not in current:
            lines.pop(rel, None)
            hashes.pop(rel, None)
            stats["removed"] += 1
    map_path, meta_path = _paths(root)
    map_path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(lines)
    atomic.write_text(map_path, "\n".join(lines[k] for k in ordered) + ("\n" if ordered else ""))
    meta = {
        "schema": SCHEMA, "generated_at": _now(), "root": str(root),
        "source": source, "files": hashes,
        "stats": {"files": len(ordered), "by_lang": _by_lang(ordered)},
        "scope": {"max_files": MAX_FILES, "exclude_dirs": sorted(EXCLUDE_DIRS)},
    }
    atomic.write_json(meta_path, meta, indent=1)
    stats["entries"] = len(ordered)
    stats["map"] = str(map_path)
    return stats


def _by_lang(paths):
    counts = {}
    for rel in paths:
        lang = _LANG.get(Path(rel).suffix.lower(), "other")
        counts[lang] = counts.get(lang, 0) + 1
    return dict(sorted(counts.items()))


def status(workdir, deep=False):
    root = _root(workdir)
    lines = _load_lines(root)
    meta = _load_meta(root)
    files, source = _list_files(root)
    current = set(files)
    recorded = meta.get("files") or {}
    missing, orphan, stale, unbaselined = [], [], [], []
    for rel in sorted(lines):
        if rel not in current:
            if _excluded(rel) or not _is_text_name(rel):
                orphan.append(rel)
            else:
                missing.append(rel)
            continue
        row = recorded.get(rel) or {}
        try:
            stat = (root / rel).stat()
        except OSError:
            missing.append(rel)
            continue
        if not row:
            stale.append(rel)
            continue
        if stat.st_size != int(row.get("size") or -1):
            stale.append(rel)
        elif stat.st_mtime_ns != int(row.get("mtime_ns") or -1):
            if deep:
                digest, _ = _hash_file(root / rel, True)
                if digest != row.get("sha256"):
                    stale.append(rel)
            else:
                stale.append(rel)
    for rel in sorted(current):
        if rel not in lines:
            unbaselined.append(rel)
    result = {
        "schema": SCHEMA, "root": str(root), "source": source,
        "entries": len(lines), "files": len(files),
        "fresh": max(0, len(lines) - len(missing) - len(orphan) - len(stale)),
        "missing": {"count": len(missing), "paths": missing[:50]},
        "orphan": {"count": len(orphan), "paths": orphan[:50]},
        "stale": {"count": len(stale), "paths": stale[:50]},
        "unbaselined": {"count": len(unbaselined), "paths": unbaselined[:50]},
        "generated_at": meta.get("generated_at") or "",
        "map": str(_paths(root)[0]),
    }
    result["clean"] = not (missing or orphan or stale or unbaselined)
    return result


def _task_text(task):
    task = task or {}
    return " ".join(str(task.get(k) or "") for k in ("type", "title", "goal", "context")).lower()


def _score(rel, line, terms, text):
    hay = (rel + " " + line).lower()
    score = sum(2 for term in terms if len(term) >= 2 and term in hay)
    if rel in text or rel.replace("/", "\\") in text:
        score += 20
    depth = rel.count("/")
    if depth == 0:
        score += 2
    elif depth == 1:
        score += 1
    if "[test]" in line and "test" not in text:
        score -= 2
    return score


def select_block(task, budget=PROMPT_BUDGET, refresh_status=True):
    task = task or {}
    workdir = str(task.get("workdir") or "").strip()
    if not workdir:
        return {"block": "", "entries": 0, "drift": {}}
    try:
        root = _root(workdir)
    except (OSError, ValueError):
        return {"block": "", "entries": 0, "drift": {}}
    lines = _load_lines(root)
    if not lines:
        return {"block": "", "entries": 0, "drift": {}}
    drift = {}
    try:
        if refresh_status:
            st = status(root)
            drift = {k: st[k]["count"] for k in ("missing", "orphan", "stale", "unbaselined")}
    except (OSError, ValueError):
        pass
    text = _task_text(task)
    terms = set(re.findall(r"[a-z0-9_\-]{3,}", text))
    terms.update(re.findall(r"[\u4e00-\u9fff]{2,4}", text))
    ranked = sorted(
        ((_score(rel, line, terms, text), rel, line) for rel, line in lines.items()),
        key=lambda row: (-row[0], row[1]))
    if not ranked or ranked[0][0] <= 0:
        ranked = sorted(
            ((_score(rel, line, set(), ""), rel, line) for rel, line in lines.items()),
            key=lambda row: (-row[0], row[1]))
    header = ["## 代码认知图谱（自动生成；先读地图再展开搜索）",
              "地图文件：%s；条目 %d，漂移 missing=%d orphan=%d stale=%d unbaselined=%d" % (
                  _paths(root)[0], len(lines),
                  drift.get("missing", 0), drift.get("orphan", 0),
                  drift.get("stale", 0), drift.get("unbaselined", 0)),
              "条目格式：F=职责 R=需同读 A=对外接口 S=关键约束"]
    body = []
    used = sum(len(x) + 1 for x in header)
    for _, rel, line in ranked:
        if len(body) >= PROMPT_MAX_ENTRIES or used + len(line) + 1 > budget:
            break
        body.append(line)
        used += len(line) + 1
    if not body:
        return {"block": "", "entries": 0, "drift": drift}
    block = "\n".join(header + body)
    if len(ranked) > len(body):
        block += "\n…（其余 %d 条见地图文件）" % (len(ranked) - len(body))
    return {"block": block, "entries": len(body), "drift": drift,
            "total": len(lines)}


def read_entries(workdir, query="", prefix="", limit=40):
    """Return map lines matching a keyword query and/or a path prefix."""
    root = _root(workdir)
    lines = _load_lines(root)
    terms = [x for x in re.split(r"[\s,]+", str(query or "").strip().lower()) if x]
    pref = _norm_rel(prefix or "")
    try:
        limit = max(1, min(int(limit or 40), 200))
    except (TypeError, ValueError):
        limit = 40
    rows = []
    for rel in sorted(lines):
        if pref and not (rel == pref or rel.startswith(pref.rstrip("/") + "/")):
            continue
        line = lines[rel]
        if terms and not any(term in line.lower() for term in terms):
            continue
        rows.append(line)
        if len(rows) >= limit:
            break
    return rows


def tool_result(workdir, args):
    """Render a built-in ``code_map`` tool call as model-facing text.

    The caller owns sandbox checks; this function only reads or refreshes the
    map under the supplied workspace.
    """
    action = str((args or {}).get("action") or "status").strip().lower()
    root = str(workdir or "").strip()
    if not root:
        return "（缺少工作目录）"
    if action in ("build", "refresh", "update"):
        paths = [x for x in re.split(r"[\s,]+", str((args or {}).get("paths") or "").strip()) if x]
        try:
            result = build(root, changed_paths=paths or None)
        except (OSError, ValueError) as exc:
            return "（代码地图生成失败：%s）" % str(exc)[:300]
        return ("代码地图已更新：%s\n文件 %d，条目 %d（本次生成 %d、移除 %d，来源 %s）"
                % (result.get("map"), result.get("files", 0), result.get("entries", 0),
                   result.get("generated", 0), result.get("removed", 0),
                   result.get("source", "")))
    if action in ("read", "show", "search"):
        try:
            rows = read_entries(
                root, query=(args or {}).get("query") or "",
                prefix=(args or {}).get("path") or "",
                limit=(args or {}).get("limit") or 40)
        except (OSError, ValueError) as exc:
            return "（代码地图不可读：%s）" % str(exc)[:300]
        if not rows:
            return "（代码地图无匹配条目；若从未生成，请先用 action=build）"
        return "\n".join(rows)
    try:
        st = status(root, deep=bool((args or {}).get("deep")))
    except (OSError, ValueError) as exc:
        return "（代码地图状态不可读：%s）" % str(exc)[:300]
    if not st.get("entries"):
        return "（尚未生成代码地图；使用 action=build 生成 %s）" % st.get("map", "")
    drift = "missing=%d orphan=%d stale=%d unbaselined=%d" % (
        st["missing"]["count"], st["orphan"]["count"], st["stale"]["count"],
        st["unbaselined"]["count"])
    out = ["代码地图：%s" % st.get("map"),
           "条目 %d / 文件 %d；%s；%s" % (
               st.get("entries", 0), st.get("files", 0), drift,
               "干净" if st.get("clean") else "有漂移（改完代码请 action=build）")]
    for key in ("missing", "orphan", "stale", "unbaselined"):
        paths = st[key]["paths"][:10]
        if paths:
            out.append("%s: %s" % (key, ", ".join(paths)))
    return "\n".join(out)


def prompt_block(task, budget=PROMPT_BUDGET):
    try:
        return select_block(task, budget=budget).get("block") or ""
    except (OSError, ValueError, TypeError):
        return ""
