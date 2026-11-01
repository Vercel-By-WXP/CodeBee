# -*- coding: utf-8 -*-
"""Discover repository-local AGENTS.md guidance for an execution context.

The format is intentionally compatible with the common AGENTS.md convention,
but the loader stays small and dependency-free.  Guidance is treated as input
data: it is bounded, scrubbed before prompt injection, and content-addressed so
the run can explain exactly which context was used.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from .redact import scrub_text

MAX_FILES = 8
MAX_FILE_CHARS = 8000
MAX_PROMPT_CHARS = 24000


def _lexical_path(value):
    """Return an absolute path without resolving symlink aliases.

    macOS exposes ``/var`` through ``/private/var``.  Security checks should
    compare canonical paths, while evidence returned to callers should retain
    the path spelling they supplied.
    """
    try:
        return Path(os.path.abspath(os.path.expanduser(os.fspath(value))))
    except (TypeError, OSError, ValueError):
        return None


def _roots(workdir, boundary=None):
    try:
        lexical_current = _lexical_path(workdir)
        if lexical_current is None:
            return []
        current = lexical_current.resolve()
    except (TypeError, OSError, ValueError):
        return []
    if not lexical_current.is_dir():
        return []
    # Trust instructions only through the nearest repository root. Outside a
    # repository, the selected workdir itself is the boundary; never walk into
    # an unrelated parent directory (or drive root).
    try:
        if boundary:
            lexical_stop = _lexical_path(boundary)
            if lexical_stop is None:
                return []
            stop = lexical_stop.resolve()
        else:
            lexical_stop = lexical_current
            stop = current
            for candidate in (lexical_current, *lexical_current.parents):
                if (candidate / ".git").exists():
                    lexical_stop = candidate
                    stop = candidate.resolve()
                    break
    except (TypeError, OSError, ValueError):
        lexical_stop = lexical_current
        stop = current
    if current != stop and stop not in current.parents:
        lexical_stop = lexical_current
        stop = current
    roots = []
    display = lexical_current
    while True:
        roots.append(display)
        if display.resolve() == stop:
            break
        parent = display.parent
        if parent == display:
            break
        display = parent
    return roots


def _read(path):
    try:
        if path.is_symlink() or not path.is_file():
            return ""
        raw = path.read_bytes()[:MAX_FILE_CHARS * 4]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("gbk", "replace")
        return scrub_text(text, limit=MAX_FILE_CHARS)
    except OSError:
        return ""


def discover(workdir, *, boundary=None, max_files=MAX_FILES, max_chars=MAX_PROMPT_CHARS,
             ui=False, task=None):
    """Return nearest-first AGENTS.md guidance and stable metadata.

    ui=True（UI 任务）时追加最近作用域的 DESIGN.md 设计宪法（2026-11-01，
    借研 awesome-design-md 落地）——改界面的任务必须先读设计语言再动手。
    task 传入时再追加 ReasoningBank 历史模式（2026-11-01 拍板注入，Ruflo
    借研落地）——同型任务的成功模式/失败教训供借鉴；库空/无命中=零注入
    （注意力守恒），检索零模型零额度。
    """
    try:
        file_limit = max(1, min(MAX_FILES, int(max_files)))
        char_limit = max(1000, min(MAX_PROMPT_CHARS, int(max_chars)))
    except (TypeError, ValueError):
        file_limit, char_limit = MAX_FILES, MAX_PROMPT_CHARS
    records = []
    chunks = []
    used = 0
    design = None
    for root in _roots(workdir, boundary=boundary):
        if len(records) >= file_limit:
            break
        path = root / "AGENTS.md"
        text = _read(path)
        if not text:
            continue
        remaining = char_limit - used
        if remaining <= 0:
            break
        text = text[:remaining]
        rel = str(path)
        records.append({
            "path": rel,
            "scope": str(root),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "chars": len(text),
        })
        chunks.append("## AGENTS.md（作用域：%s）\n%s" % (root, text))
        used += len(text)
    if ui:
        # DESIGN.md 设计宪法：最近作用域优先（工作目录的专属宪法压过仓库根），
        # 上限 12000 字符（现行 DESIGN.md ~9KB 全文可容；预算内追加在
        # AGENTS.md 之后——先懂规矩再看任务）。
        for root in _roots(workdir, boundary=boundary):
            path = root / "DESIGN.md"
            text = _read(path)
            if not text:
                continue
            remaining = char_limit - used
            if remaining <= 0:
                break
            text = text[:min(12000, remaining)]
            design = {
                "path": str(path),
                "scope": str(root),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "chars": len(text),
            }
            chunks.append("## DESIGN.md（设计宪法·UI 任务必读，冲突以它为准）\n%s"
                          % text)
            used += len(text)
            break
    # ReasoningBank 历史模式（拍板后注入，2026-11-01）：封顶 3 条×200 字符，
    # 预算内排在最后（先规矩、再参考）；失败教训也注入（标注「教训」），
    # 但整块装不进预算时宁可不注入——绝不挤掉 AGENTS.md。
    patterns = None
    try:
        from . import reasoning_bank
        t = task or {}
        query = " ".join(str(t.get(k) or "") for k in ("title", "goal"))
        query = " ".join(query.split())[:200]
        if query:
            hits = reasoning_bank.retrieve(
                query, task_type=str(t.get("type") or ""), k=3)
            hits = hits[:3]
            if hits:
                rows = []
                for h in hits:
                    tag = "成功模式" if h.get("outcome") == "success" else "教训"
                    text = " ".join(str(h.get("text") or "").split())[:200]
                    rows.append("- [%s] %s" % (tag, text))
                block = ("## 历史模式参考（ReasoningBank·同型任务，供借鉴勿盲从）\n"
                         + "\n".join(rows))
                remaining = char_limit - used
                if remaining >= 200:
                    block = block[:min(1600, remaining)]
                    patterns = {"count": len(hits), "chars": len(block)}
                    chunks.append(block)
                    used += len(block)
    except Exception:
        patterns = None          # 模式库故障绝不连累上下文装配
    prompt = "\n\n".join(chunks)
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return {"prompt": prompt, "files": records, "content_sha256": digest,
            "file_count": len(records), "chars": len(prompt),
            "design": design, "patterns": patterns}


# UI 任务启发：目标/标题命中任一关键词即视为界面工作（命中面宁宽勿窄——
# 多注入一份宪法比改坏界面的代价小得多）。
_UI_GOAL_RE = re.compile(
    r"界面|页面|样式|风格|主题|皮肤|配色|颜色|布局|按钮|图标|弹层|弹窗|"
    r"菜单|导航|侧栏|顶栏|抽屉|响应式|移动端|手机壳|适配|前端|UI|CSS|"
    r"设计稿|好看|观感|字体|圆角|投影|深色|暗色|浅色", re.I)


def is_ui_task(task):
    """任务是否涉及界面工作（goal/title/context 关键词启发）。"""
    t = task or {}
    text = " ".join(str(t.get(k) or "") for k in ("goal", "title", "context"))
    return bool(text) and bool(_UI_GOAL_RE.search(text))


def inject(task, workdir=None):
    """Append discovered guidance to a task copy and return metadata."""
    value = dict(task or {})
    result = discover(workdir or value.get("workdir"), ui=is_ui_task(value),
                      task=value)
    if result["prompt"]:
        existing = str(value.get("context") or "").strip()
        value["context"] = (existing + "\n\n" if existing else "") + result["prompt"]
    meta = {"content_sha256": result["content_sha256"],
            "files": result["files"],
            "file_count": result["file_count"],
            "chars": result["chars"]}
    if result.get("design"):
        meta["design"] = result["design"]
    if result.get("patterns"):
        meta["patterns"] = result["patterns"]
    value["agent_context"] = meta
    return value, result
