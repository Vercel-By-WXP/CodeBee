"""定位：把「一段 bug 描述」变成「几个可疑的文件:行」。

三种信号叠加打分：
  1. 堆栈帧 —— 最硬的证据，直接命中文件与行；
  2. 符号   —— 标题/堆栈里的函数名、类名、字段名，全仓搜索；
  3. 关键词 —— 中文 2~4 字滑窗 + 英文标识符，命中计分。
不追求精准，只要求「把人该看的 5 个地方排到最前面」。
"""

from __future__ import annotations

import os
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import util
from .knowledge import _top_keywords
from .models import Bug, Location

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "dist", "build", "target", "out", "vendor",
    "__pycache__", ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "coverage", ".next", ".nuxt", ".idea", ".vscode", ".zfix", "site-packages",
}
TEXT_EXT = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".vue", ".java", ".kt", ".go", ".rs", ".rb",
    ".php", ".cs", ".c", ".cc", ".cpp", ".h", ".hpp", ".swift", ".m", ".scala", ".sql",
    ".sh", ".css", ".scss", ".less", ".html", ".json", ".toml", ".yaml", ".yml", ".md",
}
MAX_FILE_BYTES = 1_200_000

_FRAME_RES = (
    re.compile(r'File "([^"]+)", line (\d+)(?:, in ([\w.<>]+))?'),
    re.compile(r"at\s+([\w.$]+)\(([\w$.\-/\\]+\.\w+):(\d+)\)"),
    re.compile(r"([\w./\\\-]+\.(?:py|js|ts|tsx|jsx|vue|java|go|rb|php|cs|kt)):(\d+)(?::\d+)?"),
)


def parse_frames(text: str) -> List[Tuple[str, int, str]]:
    """返回 [(相对或绝对路径, 行号, 符号)]，按出现顺序去重。"""
    out: List[Tuple[str, int, str]] = []
    seen = set()
    for regex in _FRAME_RES:
        for match in regex.finditer(text or ""):
            groups = match.groups()
            if regex is _FRAME_RES[0]:
                path, line, symbol = groups[0], groups[1], groups[2] or ""
            elif regex is _FRAME_RES[1]:
                symbol, path, line = groups[0], groups[1], groups[2]
            else:
                path, line, symbol = groups[0], groups[1], ""
            try:
                lineno = int(line)
            except (TypeError, ValueError):
                lineno = 0
            key = (path, lineno)
            if key in seen:
                continue
            seen.add(key)
            out.append((path, lineno, symbol))
    return out


# 语言关键字/内置名：全仓搜索它们只会淹掉真正的信号
_KEYWORD_NOISE = {
    "self", "this", "return", "import", "export", "from", "class", "def", "function",
    "const", "let", "var", "async", "await", "true", "false", "none", "null", "undefined",
    "except", "raise", "throw", "error", "exception", "traceback", "print", "string",
    "value", "data", "result", "response", "request", "payload", "params", "args", "kwargs",
    "http", "https", "json", "load", "dump", "init", "main", "test", "assert",
}


def symbols_of(bug: Bug) -> List[str]:
    """从堆栈与标题里挑出值得全仓搜索的符号。"""
    found: List[str] = []
    for _, _, symbol in parse_frames(bug.text):
        if symbol and len(symbol) >= 3:
            found.append(symbol.split(".")[-1].split("<")[0])
    for token in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]{3,})\b", bug.text):
        found.append(token)
    out: List[str] = []
    seen = set()
    for item in found:
        low = item.lower()
        if low in seen or low in _KEYWORD_NOISE:
            continue
        seen.add(low)
        out.append(item)
        if len(out) >= 12:
            break
    return out


class Locator:
    def __init__(self, root: str, use_ripgrep: Optional[bool] = None):
        self.root = os.path.abspath(root)
        self.use_rg = util.has_bin("rg") if use_ripgrep is None else use_ripgrep

    # ------------------------------------------------------------ 搜索
    def search(self, pattern: str, limit: int = 40) -> List[Tuple[str, int]]:
        if self.use_rg:
            hits = self._rg(pattern, limit)
            if hits is not None:
                return hits
        return self._py(pattern, limit)

    def _rg(self, pattern: str, limit: int) -> Optional[List[Tuple[str, int]]]:
        globs = " ".join(f"--glob '!{d}/**'" for d in sorted(SKIP_DIRS) if d != ".zfix")
        res = util.run_cmd(
            f"rg -n --no-heading --color never -F -m {limit} -- {util.q(pattern)} . {globs}",
            cwd=self.root,
            timeout=45,
        )
        if res["exit_code"] == 127 or "not found" in res["output"].lower():
            return None
        if res["exit_code"] not in (0, 1):
            return None
        out: List[Tuple[str, int]] = []
        for line in res["output"].splitlines():
            parts = line.split(":", 2)
            if len(parts) < 2:
                continue
            path = parts[0].replace("\\", "/")
            if path.startswith("./"):
                path = path[2:]
            try:
                out.append((path, int(parts[1])))
            except ValueError:
                continue
        return out

    def _py(self, pattern: str, limit: int) -> List[Tuple[str, int]]:
        out: List[Tuple[str, int]] = []
        needle = pattern.lower()
        budget = 4000
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            if budget <= 0:
                break
            for name in filenames:
                if not name.lower().endswith(tuple(TEXT_EXT)):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    if os.path.getsize(path) > MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                budget -= 1
                if budget <= 0:
                    break
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                        for lineno, line in enumerate(fh, 1):
                            if needle in line.lower():
                                out.append((os.path.relpath(path, self.root).replace("\\", "/"), lineno))
                                if len(out) >= limit:
                                    return out
                except OSError:
                    continue
        return out

    # ------------------------------------------------------------ 打分
    def locate(self, bug: Bug, limit: int = 12) -> List[Location]:
        scores: Dict[str, Location] = {}

        def bump(path: str, score: int, reason: str, line: int = 0, symbol: str = "") -> None:
            path = path.replace("\\", "/")
            if path.startswith("./"):
                path = path[2:]
            item = scores.get(path)
            if item is None:
                item = Location(path=path)
                scores[path] = item
            item.score += score
            if line and not item.line:
                item.line = line
            if symbol and not item.symbol:
                item.symbol = symbol
            if reason not in item.reasons:
                item.reasons.append(reason)

        # 1) 堆栈帧：先试绝对/相对路径存在性，不存在就按文件名在仓内找
        for raw_path, line, symbol in parse_frames(bug.text):
            resolved = self._resolve(raw_path)
            if resolved:
                bump(resolved, 100, f"堆栈落在第 {line} 行", line, symbol)
                continue
            basename = os.path.basename(raw_path.replace("\\", "/"))
            for hit in self.search(basename, limit=5):
                bump(hit[0], 55, f"堆栈文件 {basename} 的同名文件", line, symbol)

        # 2) 符号搜索
        for symbol in symbols_of(bug):
            hits = self.search(symbol, limit=12)
            if not hits:
                continue
            weight = 40 if symbol[:1].isupper() or "_" in symbol or symbol[:1].islower() else 25
            for path, line in hits[:8]:
                bump(path, max(weight - hits.index((path, line)) * 2, 8), f"命中符号 {symbol}", line)

        # 3) 关键词（中文场景主信号）
        for keyword in _top_keywords(bug.text, 8):
            for path, line in self.search(keyword, limit=10)[:6]:
                bump(path, 10, f"命中关键词 {keyword}", line)

        # 4) 命中多个信号的文件额外加权，单一关键词命中要压下去
        for item in scores.values():
            if len(item.reasons) >= 3:
                item.score += 20
            if len(item.reasons) == 1 and item.reasons[0].startswith("命中关键词"):
                item.score -= 6

        ranked = sorted(scores.values(), key=lambda loc: (-loc.score, loc.path))
        out = [loc for loc in ranked if loc.score > 0][:limit]
        for loc in out:
            loc.snippet = self._snippet(loc.path, loc.line)
        return out

    # ------------------------------------------------------------ 辅助
    def _resolve(self, raw_path: str) -> str:
        candidates = [raw_path]
        normalized = raw_path.replace("\\", "/")
        if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
            parts = normalized.split("/")
            for idx in range(len(parts)):
                candidates.append("/".join(parts[idx:]))
        else:
            candidates.append(normalized)
        for cand in candidates:
            full = cand if os.path.isabs(cand) else os.path.join(self.root, cand)
            if os.path.isfile(full):
                return os.path.relpath(full, self.root).replace("\\", "/")
        return ""

    def _snippet(self, path: str, line: int, radius: int = 3) -> str:
        full = os.path.join(self.root, path)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            return ""
        if not lines:
            return ""
        center = line - 1 if line else 0
        start = max(center - radius, 0)
        end = min(center + radius + 1, len(lines))
        out = []
        for idx in range(start, end):
            mark = ">" if idx == center else " "
            out.append(f"{mark} {idx + 1:>5} | {lines[idx].rstrip()[:200]}")
        return "\n".join(out)
