# -*- coding: utf-8 -*-
"""训练对归集器（writer dataset）：把连载产出的「章纲+上下文 → 成稿 → 评审结论」
收成结构化数据集，供 SFT 导出与写手迭代。

数据真源（全部只读）：
- run.json（data/runs/<rid>/run.json）：outline（逐章要点/文风锚）、
  chapter_scores（逐章分数/轮次/字数）、verdict.major_issues、writer_version；
- 工作目录：章文件走 quality.chapters.discover 唯一真源（含已成稿/合稿补前缀、
  排除作废稿目录），逐章锁定章纲走 planner.locked_chapter_outline_text；
- 任务文件（data/tasks/<id>.json）：workdir、书名、题材。

零网络、零模型调用。坏 run / 坏文件跳过并计数，绝不中断整个归集。
输出（data/writer_dataset/）：pairs-<ts>.jsonl + 配套 manifest.json +
latest.json 指针；export_sft 另出 sft-<ts>.jsonl（ChatML 三消息格式，
LLaMA-Factory / axolotl 直接可吃）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import paths

OUT_DIR = paths.DATA_DIR / "writer_dataset"
_NOVEL_TYPES = ("novel", "serial_novel")
_REVIEW_CAP = 20000   # 单章数据集字段里评审意见的最大字符
_LOCKED_CAP = 6000    # 逐章锁定章纲截断
_TAIL_CAP = 600       # 前文结尾截断


def _read_json(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _mean(means):
    vals = [float(v) for v in (means or {}).values()
            if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 2) if vals else None


def _fmt_issues(entries, chapter):
    out = []
    for x in entries or []:
        try:
            if int(x.get("chapter") or -1) != int(chapter):
                continue
        except (TypeError, ValueError):
            continue
        out.append("[{}] {}".format(x.get("severity") or "major",
                                    str(x.get("note") or x.get("issue") or "")[:400]))
    return "\n".join(out)[:_REVIEW_CAP]


def collect(task_ids=None, runs_limit=200, include_rejected=False,
            min_mean=None, extra_dirs=None, out_dir=None):
    """归集训练对。返回 summary dict（含输出文件路径与各项计数）。

    默认只收**过线章**（chapter_scores.passed=True）；include_rejected=True
    连未过线章一起收（带评审意见，供负样例/DPO）；min_mean 是可选均分下限。
    同一（工作目录，章号）只留均分最高的一份（重写/复用只留最好稿）。
    """
    out = Path(out_dir) if out_dir else OUT_DIR
    tasks_dir = paths.TASKS_DIR
    runs_dir = paths.RUNS_DIR
    wanted = {}
    for p in sorted(tasks_dir.glob("*.json")):
        t = _read_json(p) or {}
        tid = t.get("id") or p.stem
        if task_ids and tid not in task_ids:
            continue
        wd = str(t.get("workdir") or "").strip()
        if not wd:
            continue
        if (t.get("type") in _NOVEL_TYPES or t.get("serial")):
            wanted[tid] = t
    summary = {"created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "params": {"task_ids": list(task_ids or []) or None,
                          "runs_limit": runs_limit,
                          "include_rejected": bool(include_rejected),
                          "min_mean": min_mean,
                          "extra_dirs": list(extra_dirs or [])},
               "tasks": 0, "runs": 0, "runs_no_scores": 0, "skipped_no_text": 0,
               "dedup_dropped": 0, "pairs": 0, "workdirs": 0,
               "workdir_no_chapters": 0}
    best = {}   # (normcase(workdir), chapter) -> pair

    def add_pair(pair):
        key = (os.path.normcase(pair["meta"]["workdir"]),
               pair["meta"]["chapter"])
        old = best.get(key)
        if old is not None:
            om = old["meta"].get("mean")
            nm = pair["meta"].get("mean")
            if nm is None or (om is not None and om >= nm):
                summary["dedup_dropped"] += 1
                return
            summary["dedup_dropped"] += 1
        best[key] = pair

    run_files = sorted(runs_dir.glob("*/run.json"),
                       key=lambda p: p.parent.name, reverse=True)
    processed = 0
    for rp in run_files:
        if processed >= max(1, int(runs_limit)):
            break
        run = _read_json(rp)
        if not isinstance(run, dict):
            continue
        task = wanted.get(str(run.get("task_id") or ""))
        if not task:
            continue
        processed += 1
        scores = run.get("chapter_scores")
        if not isinstance(scores, list) or not scores:
            summary["runs_no_scores"] += 1
            continue
        summary["runs"] += 1
        wd = str(task.get("workdir") or "").strip()
        outline = run.get("outline") if isinstance(run.get("outline"), dict) else {}
        och = {}
        for c in outline.get("chapters") or []:
            try:
                och[int(c.get("chapter") or 0)] = c
            except (TypeError, ValueError):
                continue
        rows, _info = _discover_cached(wd)
        if not rows:
            summary["workdir_no_chapters"] += 1
            continue
        by_ch = {r["chapter"]: r for r in rows}
        issues = ((run.get("verdict") or {}).get("major_issues") or [])
        for sc in scores:
            if not isinstance(sc, dict):
                continue
            try:
                ch = int(sc.get("chapter") or 0)
            except (TypeError, ValueError):
                continue
            row = by_ch.get(ch)
            if not row or not (row.get("text") or "").strip():
                summary["skipped_no_text"] += 1
                continue
            mean = _mean(sc.get("means"))
            passed = bool(sc.get("passed"))
            if not include_rejected and not passed:
                continue
            if min_mean is not None and (mean is None or mean < float(min_mean)):
                continue
            add_pair({
                "id": "%s-ch%03d" % (task.get("id") or "?", ch),
                "meta": {
                    "task_id": task.get("id") or "", "run_id": run.get("id") or "",
                    "workdir": wd, "chapter": ch,
                    "book_title": task.get("title") or "",
                    "title": sc.get("title") or (och.get(ch) or {}).get("title") or "",
                    "words": sc.get("words"), "rounds": sc.get("rounds"),
                    "passed": passed, "mean": mean,
                    "scored": True,
                    "version": run.get("writer_version") or "",
                    "issues": _fmt_issues(issues, ch),
                },
                "outline_locked": _locked_outline(wd, ch),
                "outline_run": och.get(ch) or {},
                "style_anchor": str(outline.get("style_anchor") or ""),
                "prev_tail": _prev_tail(by_ch, ch, _TAIL_CAP),
                "text": row["text"],
                "from": row.get("from") or "file",
            })
    # 跑分体系之外的书（如已完结归档目录）：纯文本对，scored=False
    for wd in (extra_dirs or []):
        wd = str(wd or "").strip()
        if not wd:
            continue
        rows, _info = _discover_cached(wd)
        for row in rows:
            add_pair({
                "id": "extra-ch%03d" % row["chapter"],
                "meta": {"task_id": "", "run_id": "", "workdir": wd,
                         "chapter": row["chapter"], "title": "", "words": None,
                         "rounds": None, "passed": None, "mean": None,
                         "scored": False, "version": "", "issues": ""},
                "outline_locked": _locked_outline(wd, row["chapter"]),
                "outline_run": {}, "style_anchor": "",
                "prev_tail": _prev_tail({r["chapter"]: r for r in rows},
                                        row["chapter"], _TAIL_CAP),
                "text": row["text"], "from": row.get("from") or "file",
            })
    summary["tasks"] = len(wanted)
    summary["workdirs"] = len({os.path.normcase(k[0]) for k in best})
    summary["pairs"] = len(best)
    out.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    pairs_path = out / ("pairs-%s.jsonl" % ts)
    with pairs_path.open("w", encoding="utf-8") as f:
        for key in sorted(best, key=lambda k: (k[0], k[1])):
            f.write(json.dumps(best[key], ensure_ascii=False) + "\n")
    manifest = dict(summary)
    manifest["pairs_file"] = pairs_path.name
    (out / ("pairs-%s.manifest.json" % ts)).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "latest.json").write_text(json.dumps(
        {"pairs_file": pairs_path.name, "pairs": len(best),
         "created_at": manifest["created_at"]}, ensure_ascii=False),
        encoding="utf-8")
    manifest["out_dir"] = str(out)
    return manifest


_DISCOVER_CACHE = {}


def _discover_cached(workdir):
    """同一工作目录一章只扫一遍（200 章 × 50+ run 的归集不能重复读盘）。"""
    key = os.path.normcase(str(workdir or ""))
    if key not in _DISCOVER_CACHE:
        try:
            from .quality import chapters as q_chapters
            rows, info = q_chapters.discover(workdir, limit=100000)
        except Exception:
            rows, info = [], {}
        if len(_DISCOVER_CACHE) > 8:
            _DISCOVER_CACHE.clear()
        _DISCOVER_CACHE[key] = (rows, info)
    return _DISCOVER_CACHE[key]


def _locked_outline(workdir, chapter):
    try:
        from . import planner
        return planner.locked_chapter_outline_text(workdir, chapter,
                                                   cap=_LOCKED_CAP)
    except Exception:
        return ""


def _prev_tail(by_ch, chapter, cap):
    row = by_ch.get(chapter - 1)
    text = str((row or {}).get("text") or "").strip()
    return text[-cap:] if text else ""


# ------------------------------------------------------------------ SFT 导出

def _default_system(version_prompt):
    base = "你是中文网文写手，按给定章纲与全书上下文直接写出本章正文，不解释、不写提纲。"
    if version_prompt:
        base += "\n写作纪律：\n" + version_prompt
    return base


def _build_user(pair):
    meta = pair.get("meta") or {}
    oc = pair.get("outline_run") or {}
    locked = str(pair.get("outline_locked") or "").strip()
    lines = []
    lines.append("书名《%s》第 %d 章%s。" % (
        meta.get("book_title") or "本书", meta.get("chapter") or "",
        ("《%s》" % meta["title"]) if meta.get("title") else ""))
    if locked:
        lines.append("【作者已确认的本章章纲（最高优先级）】\n%s" % locked)
    if oc.get("beats"):
        lines.append("【本章要点】%s" % oc["beats"])
    if oc.get("hook"):
        lines.append("【章末钩子】%s" % oc["hook"])
    if pair.get("style_anchor"):
        lines.append("【文风锚】%s" % pair["style_anchor"])
    if pair.get("prev_tail"):
        lines.append("【前一章结尾（必须自然衔接）】\n%s" % pair["prev_tail"])
    lines.append("请直接写出本章正文。")
    return "\n\n".join(lines)


def export_sft(pairs_file=None, out_dir=None, min_mean=None, max_pairs=None,
               system_prompt=None, book_titles=None):
    """把训练对导成 ChatML SFT jsonl。默认读 latest.json 指针；
    默认只导过线章（include 不含未过线章的指针本就只含过线章，
    min_mean 是可选再收紧）。返回 summary。"""
    out = Path(out_dir) if out_dir else OUT_DIR
    src = str(pairs_file or "")
    if not src:
        pointer = _read_json(out / "latest.json") or {}
        src = str(pointer.get("pairs_file") or "")
    if not src:
        return {"ok": False, "error": "没有可导出的训练对（先 collect）"}
    p = Path(src)
    if not p.is_absolute():
        p = out / src
    pairs = []
    with p.open("r", encoding="utf-8") as f:
        for ln in f:
            try:
                pairs.append(json.loads(ln))
            except Exception:
                continue
    try:
        from . import writer_slot
        sys_default = _default_system(
            str((writer_slot.state()["versions"].get(
                writer_slot.state()["active_version"]) or {}).get("prompt") or ""))
    except Exception:
        sys_default = _default_system("")
    system_prompt = str(system_prompt or sys_default)
    rows, dropped = [], 0
    for pair in pairs:
        meta = pair.get("meta") or {}
        if meta.get("scored") and meta.get("passed") is not True:
            dropped += 1
            continue
        if min_mean is not None:
            mean = meta.get("mean")
            if mean is None or float(mean) < float(min_mean):
                dropped += 1
                continue
        if book_titles and str(meta.get("book_title") or "") not in book_titles:
            dropped += 1
            continue
        rows.append({"messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _build_user(pair)},
            {"role": "assistant", "content": str(pair.get("text") or "").strip()},
        ]})
    if max_pairs:
        rows = rows[:max(1, int(max_pairs))]
    out.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    sft_path = out / ("sft-%s.jsonl" % ts)
    with sft_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = {"created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "pairs_file": p.name, "sft_file": sft_path.name,
               "exported": len(rows), "filtered_out": dropped,
               "min_mean": min_mean, "max_pairs": max_pairs,
               "system_from": "writer_slot" if not system_prompt else "custom"}
    (out / ("sft-%s.manifest.json" % ts)).write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    summary["ok"] = True
    return summary


def manifests(out_dir=None, limit=30):
    """归集/导出的历史清单（新的在前），UI 列表用。"""
    out = Path(out_dir) if out_dir else OUT_DIR
    items = []
    for p in sorted(out.glob("*.manifest.json"), reverse=True):
        d = _read_json(p) or {}
        d["manifest_file"] = p.name
        items.append(d)
        if len(items) >= limit:
            break
    return items
