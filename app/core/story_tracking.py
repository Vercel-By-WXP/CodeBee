"""Structured, deterministic long-form fiction tracking.

The JSON state is the source of truth. Markdown files under ``derived`` are
regenerated from that state and are checked for drift before a run consumes
them. This keeps long serial projects resumable without requiring a vector DB.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

_MAX_CHAPTERS = 5000
_STATE_REL = Path(".codebee") / "story_tracking.json"
_DERIVED = Path(".codebee") / "derived"
_CHAPTERS = Path(".codebee") / "chapters"


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _root(workdir):
    root = Path(workdir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("工作目录不存在")
    return root


def _state_path(root):
    return root / _STATE_REL


def _digest(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _norm_lf(text):
    """换行归一到 LF。章稿经 _read_chapter 保真读回时带 CRLF（Windows 上
    _write_chapter 文本模式落盘所致），不归一会让同一份稿在不同路径下
    摘要不一致。"""
    return str(text).replace("\r\n", "\n").replace("\r", "\n")


def _empty(task_id, title, premise=""):
    return {"version": 1, "task_id": str(task_id), "title": str(title or "")[:120],
            "premise": str(premise or "")[:2000], "state_revision": 0,
            "current_chapter": 0, "facts": [], "characters": [], "timeline": [],
            "foreshadowing": [], "next_promises": [], "author_truth": [],
            "reader_known": [], "chapters": [], "derived_digests": {},
            "created_at": _now(), "updated_at": _now()}


def _save(root, state):
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load(root):
    path = _state_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ValueError("故事追踪状态不可读: %s" % exc)
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("不支持的故事追踪状态版本")
    for name in ("facts", "characters", "timeline", "foreshadowing",
                 "next_promises", "author_truth", "reader_known", "chapters"):
        if not isinstance(data.get(name), list):
            raise ValueError("故事追踪状态字段 %s 必须是列表" % name)
    if not isinstance(data.get("derived_digests"), dict):
        raise ValueError("故事追踪状态字段 derived_digests 必须是对象")
    if any(not isinstance(row, dict) for row in data.get("chapters") or []):
        raise ValueError("故事追踪状态 chapters 含无效记录")
    return data


def init(workdir, task_id, title, premise=""):
    root = _root(workdir)
    state = _load(root)
    if state is None:
        state = _empty(task_id, title, premise)
        _save(root, state)
    _render(root, state)
    return state


def load(workdir):
    return _load(_root(workdir))


def _clean_lines(values, limit=100):
    result = []
    for value in values if isinstance(values, list) else []:
        text = str(value or "").strip()[:1000]
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _clean_foreshadowing(values):
    result = []
    for raw in values if isinstance(values, list) else []:
        if not isinstance(raw, dict):
            continue
        item = {"id": str(raw.get("id") or "").strip()[:40],
                "text": str(raw.get("text") or "").strip()[:1000],
                "status": str(raw.get("status") or "active").strip()[:30],
                "planted_chapter": raw.get("planted_chapter"),
                "payoff_chapter": raw.get("payoff_chapter")}
        if item["id"] and item["text"]:
            result.append(item)
    return result[-500:]


def commit_chapter(workdir, chapter_no, outline, prose, *, facts=None, characters=None,
                   timeline=None, foreshadowing=None, next_promises=None,
                   author_truth=None, reader_known=None):
    root = _root(workdir)
    try:
        number = int(chapter_no)
    except (TypeError, ValueError):
        raise ValueError("章节号无效")
    if not 1 <= number <= _MAX_CHAPTERS:
        raise ValueError("章节号必须在 1-%d 之间" % _MAX_CHAPTERS)
    outline = str(outline or "").strip()
    prose = _norm_lf(str(prose or "").strip())
    if not outline:
        raise ValueError("提交章节前必须有细纲")
    if not prose:
        raise ValueError("正文不能为空")
    state = _load(root)
    if state is None:
        state = _empty("", root.name)
    # A shared workdir can be used by continuation tasks.  The structured
    # tracking file is book-level state, so accepting a newer task id here
    # would make the next run look like a foreign-state drift.  Keep the
    # original owner and append chapters to the existing book timeline.
    record = {"chapter": number, "outline": outline[:12000], "prose_digest": _digest(prose),
              "word_count": len(prose), "committed_at": _now(),
              "facts": _clean_lines(facts), "characters": _clean_lines(characters),
              "timeline": _clean_lines(timeline), "foreshadowing": _clean_foreshadowing(foreshadowing),
              "next_promises": _clean_lines(next_promises, 20),
              "author_truth": _clean_lines(author_truth, 100), "reader_known": _clean_lines(reader_known, 100)}
    existing = next((x for x in state["chapters"] if x.get("chapter") == number), None)
    if existing and existing.get("prose_digest") == record["prose_digest"] and existing.get("outline") == outline:
        return state
    if existing:
        state["chapters"] = [x for x in state["chapters"] if x.get("chapter") != number]
    state["chapters"].append(record)
    state["chapters"].sort(key=lambda x: int(x.get("chapter") or 0))
    state["current_chapter"] = max(int(state.get("current_chapter") or 0), number)
    state["facts"] = _clean_lines([x for c in state["chapters"] for x in c.get("facts", [])], 500)
    state["characters"] = _clean_lines([x for c in state["chapters"] for x in c.get("characters", [])], 300)
    state["timeline"] = _clean_lines([x for c in state["chapters"] for x in c.get("timeline", [])], 500)
    state["foreshadowing"] = _clean_foreshadowing([x for c in state["chapters"] for x in c.get("foreshadowing", [])])
    state["next_promises"] = record["next_promises"]
    state["author_truth"] = record["author_truth"]
    state["reader_known"] = record["reader_known"]
    state["state_revision"] = int(state.get("state_revision") or 0) + 1
    state["updated_at"] = _now()
    chapter_dir = _CHAPTERS / ("%04d" % number)
    (root / chapter_dir).mkdir(parents=True, exist_ok=True)
    # 字节落盘钉死 LF：文本模式写会把已含 CRLF 的章稿二次翻译成 \r\r\n，
    # 磁盘稿与状态摘要永久错位，续写批开场漂移闸必拦（2026-10-03 实案）
    (root / chapter_dir / "outline.md").write_bytes((outline + "\n").encode("utf-8"))
    (root / chapter_dir / "prose.md").write_bytes((prose + "\n").encode("utf-8"))
    _save(root, state)
    _render(root, state)
    return state


def _render(root, state):
    derived = root / _DERIVED
    derived.mkdir(parents=True, exist_ok=True)
    lines = ["# 故事上下文", "", "- 书名：%s" % state.get("title", ""),
             "- 当前章：第%s章" % state.get("current_chapter", 0),
             "- 状态修订：%s" % state.get("state_revision", 0), ""]
    if state.get("premise"):
        lines += ["## 核心 premise", "", state["premise"], ""]
    lines += ["## 长期事实", ""] + ["- " + x for x in state.get("facts", [])] + [""]
    lines += ["## 活跃伏笔", ""]
    for item in state.get("foreshadowing", []):
        lines.append("- %s｜%s｜%s" % (item.get("id"), item.get("text"), item.get("status")))
    lines += ["", "## 下一章承诺", ""] + ["- " + x for x in state.get("next_promises", [])] + [""]
    lines += ["## 作者真相", ""] + ["- " + x for x in state.get("author_truth", [])] + [""]
    lines += ["## 读者已知", ""] + ["- " + x for x in state.get("reader_known", [])] + [""]
    context = "\n".join(lines).rstrip() + "\n"
    (derived / "context.md").write_text(context, encoding="utf-8")
    (derived / "foreshadowing.md").write_text("# 伏笔\n\n" + "\n".join(
        "- %s｜%s｜%s" % (x.get("id"), x.get("text"), x.get("status"))
        for x in state.get("foreshadowing", [])) + "\n", encoding="utf-8")
    (derived / "timeline.md").write_text("# 时间线\n\n" + "\n".join(
        "- " + x for x in state.get("timeline", [])) + "\n", encoding="utf-8")
    state["derived_digests"] = {}
    for name in ("context.md", "foreshadowing.md", "timeline.md"):
        state["derived_digests"][name] = _digest((derived / name).read_text(encoding="utf-8"))
    _save(root, state)
    try:
        from . import retrieval
        for name in ("context.md", "foreshadowing.md", "timeline.md"):
            path = derived / name
            retrieval.upsert(str(path), path.read_text(encoding="utf-8"),
                             {"task_id": state.get("task_id"), "kind": "story_tracking"})
    except Exception:
        pass


def _prose_matches(path, digest):
    """章稿摘要比对，兼容三种历史形态。

    - 新写：文件纯 LF，状态摘要按 LF 文本计算；
    - 旧写（文本模式二次翻译）：磁盘 \\r\\r\\n，状态摘要按 CRLF 原文计算——
      CRCRLF 是写出路径独有的字节形态，先按字节愈合回 CRLF 再比对即可认定
      同源，不必要求用户手工修复历史状态（2026-10-03 续写批漂移闸误杀实案）；
    - 旧写（未二次翻译）：磁盘普通 CRLF，摘要按 CRLF 原文计算。
    注意愈合必须在字节层做：read_text 的通用换行翻译会把 \\r\\r\\n 折成 \\n\\n，
      事后无从区分「真空行」与「二次翻译」，就再也对不上了。
    """
    if not digest:
        return False
    try:
        data = path.read_bytes()
    except OSError:
        return False
    candidates = set()
    for raw in (data, data.replace(b"\r\r\n", b"\r\n")):
        text = raw.decode("utf-8", "replace")
        candidates.add(_digest(text.strip()))
        candidates.add(_digest(_norm_lf(text).strip()))
    return digest in candidates


def check(workdir):
    root = _root(workdir)
    state = _load(root)
    if not state:
        return {"ok": False, "errors": ["缺少故事追踪状态"]}
    errors = []
    for name, digest in (state.get("derived_digests") or {}).items():
        path = root / _DERIVED / name
        if not path.is_file():
            errors.append("缺少派生文件 %s" % name)
            continue
        actual = _digest(path.read_text(encoding="utf-8"))
        if actual != digest:
            errors.append("派生文件已被手工修改: %s" % name)
    for chapter in state.get("chapters") or []:
        number = int(chapter.get("chapter") or 0)
        outline = root / _CHAPTERS / ("%04d" % number) / "outline.md"
        prose = root / _CHAPTERS / ("%04d" % number) / "prose.md"
        if not outline.is_file() or not prose.is_file():
            errors.append("章节文件缺失: %04d" % number)
        elif outline.read_text(encoding="utf-8").strip() != str(chapter.get("outline") or "").strip():
            # A continuation run may legitimately replace the shared outline
            # with a book-level outline containing additional future chapters.
            # Only reject a mismatch when the persisted chapter's own outline
            # is no longer represented in the current file.
            persisted = str(chapter.get("outline") or "").strip()
            current = outline.read_text(encoding="utf-8").strip()
            if persisted and persisted not in current:
                errors.append("章节细纲不一致: %04d" % number)
        elif not _prose_matches(prose, chapter.get("prose_digest")):
            errors.append("章节正文不一致: %04d" % number)
    return {"ok": not errors, "errors": errors, "state_revision": state.get("state_revision", 0),
            "current_chapter": state.get("current_chapter", 0)}
