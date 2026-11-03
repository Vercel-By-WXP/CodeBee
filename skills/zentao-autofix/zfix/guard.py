"""防复发：把历史缺陷变成提交前拦截 + 开工前必读。

两条防线：
  preflight  往 AGENTS.md / 各平台规则文件里写受管区块（AI 编码前必读）；
  guard      扫 git diff 的新增行，命中已知反模式就报警（可配成 pre-commit / CI）。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from . import util
from .config import Config
from .knowledge import KnowledgeBase
from .models import Pattern

BEGIN = "<!-- zfix:rules:begin"
END = "<!-- zfix:rules:end -->"


def _strip_block(text: str) -> str:
    pattern = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\s*", re.S)
    return pattern.sub("", text)


def inject(path: str, block: str, dry_run: bool = False) -> Dict[str, Any]:
    """幂等写入受管区块：已存在则原位替换，不存在则追加。"""
    existed = os.path.isfile(path)
    original = util.read_text(path, "")
    cleaned = _strip_block(original)
    body = cleaned.rstrip() + "\n\n" + block if cleaned.strip() else block
    changed = body.strip() != original.strip()
    if changed and not dry_run:
        util.write_text(path, body if body.endswith("\n") else body + "\n")
    return {"path": path, "existed": existed, "changed": changed}


def target_files(cfg: Config, platforms: Optional[List[str]] = None) -> List[str]:
    """AGENTS.md 是所有平台共读的事实标准；其余平台文件按需创建。"""
    root = cfg.repo
    names = ["AGENTS.md"]
    platform_map = {
        "claude": "CLAUDE.md",
        "codebee": "AGENTS.md",
        "codex": "AGENTS.md",
        "opencode": "AGENTS.md",
        "cursor": ".cursor/rules/zfix.mdc",
    }
    if platforms:
        for platform in platforms:
            extra = platform_map.get(platform.strip().lower())
            if extra and extra not in names:
                names.append(extra)
    elif os.path.isfile(os.path.join(root, "CLAUDE.md")):
        # 没指定平台时只补已存在的平台文件，不乱造新文件
        names.append("CLAUDE.md")
    return [os.path.join(root, name) for name in names]


def preflight(cfg: Config, kb: KnowledgeBase, dry_run: bool = False, platforms: Optional[List[str]] = None) -> Dict[str, Any]:
    max_rules = int(cfg.get("guard.max_rules") or 20)
    block = kb.rules_markdown(project=cfg.name, max_rules=max_rules)
    results = [inject(path, block, dry_run=dry_run) for path in target_files(cfg, platforms)]
    return {"dry_run": dry_run, "rules": block.count("\n- "), "files": results, "block": block}


# ---------------------------------------------------------------- diff 扫描

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_FILE = re.compile(r"^\+\+\+ b/(.+)$")


def parse_diff(diff_text: str) -> List[Dict[str, Any]]:
    """返回 [{'path':.., 'added':[(lineno, text)]}]，只看新增行。"""
    files: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    lineno = 0
    for line in diff_text.splitlines():
        head = _FILE.match(line)
        if head:
            current = {"path": head.group(1), "added": []}
            files.append(current)
            continue
        if current is None:
            continue
        hunk = _HUNK.match(line)
        if hunk:
            lineno = int(hunk.group(1))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            current["added"].append((lineno, line[1:]))
            lineno += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
        else:
            lineno += 1
    return [f for f in files if f["added"]]


def _glob_hit(path: str, globs: List[str]) -> bool:
    if not globs:
        return True
    import fnmatch

    base = os.path.basename(path)
    for pattern in globs:
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(base, pattern):
            return True
        if fnmatch.fnmatch(path, pattern.replace("**/", "")):
            return True
    return False


def guard(
    cfg: Config,
    kb: KnowledgeBase,
    diff_text: str = "",
    staged: bool = False,
) -> Dict[str, Any]:
    text = diff_text or util.git_diff(cfg.repo, staged=staged)
    if not text.strip():
        return {"ok": True, "hits": [], "files": 0, "note": "无待检查改动"}
    entries = parse_diff(text)
    patterns = [p for p in kb.patterns if p.detect_regex]
    hits: List[Dict[str, Any]] = []
    for entry in entries:
        path = entry["path"]
        for pattern in patterns:
            if not _glob_hit(path, pattern.file_glob):
                continue
            for regex in pattern.detect_regex:
                try:
                    compiled = re.compile(regex)
                except re.error:
                    continue
                for lineno, code in entry["added"]:
                    if compiled.search(code):
                        hits.append(
                            {
                                "path": path,
                                "line": lineno,
                                "code": code.strip()[:200],
                                "pattern": pattern.id,
                                "title": pattern.title,
                                "rule": pattern.rule,
                                "cause": pattern.root_cause,
                            }
                        )
    block = bool(cfg.get("guard.block"))
    return {
        "ok": not hits,
        "blocked": block and bool(hits),
        "block": block,
        "files": len(entries),
        "patterns_checked": len(patterns),
        "hits": hits,
    }


def render_guard(result: Dict[str, Any]) -> str:
    if not result.get("hits"):
        return "zfix guard · 未命中已知反模式 ✓"
    lines = [f"zfix guard · 命中 {len(result['hits'])} 处已知反模式：", ""]
    for hit in result["hits"]:
        lines.append(f"  {hit['path']}:{hit['line']}  [{hit['pattern']}] {hit['title']}")
        lines.append(f"    {hit['code']}")
        lines.append(f"    → {hit['rule']}")
    if result.get("blocked"):
        lines += ["", "已按 guard.block=true 判定为阻断。"]
    else:
        lines += ["", "（仅提示，未阻断。要阻断请把 .zfix/config.toml 的 guard.block 设为 true）"]
    return "\n".join(lines)
