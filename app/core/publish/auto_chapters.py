# -*- coding: utf-8 -*-
"""自动发布的章节挑选、复核状态与进度汇总。

从 publish.auto 拆出（单文件行数治理），原模块再导出保持旧调用面；
跨模块引用经 _auto() 解析，保持运行时状态与测试打桩语义。
"""
from __future__ import annotations


def _auto():
    from . import auto
    return auto


_DOC_DIR_MARKS = ("章纲/", "大纲/", "设定/", "参考资料/", "已成稿/",
                  "作废稿", "docs/", "outline/")
_WALK_PRUNE = {"node_modules", "target", "build", "dist", "__pycache__"}


def _workdir_chapter_files(wd):
    """任务工作目录的**全量**章节稿（不看时间窗，2026-10-08 实案）。

    run 成品口径的时间窗起点是「各自任务的首跑」——续写链上每个批次任务
    只能看到自己出生以后新写的章节：今天的批次任务待发只剩 73-78，昨天
    的 65-78，根任务才有全量 31-78。发布待发的正确口径是「工作目录里
    还没发过的章节稿」，与哪个任务/何时跑无关，所以这里直接全树扫。"""
    import os as _os
    out = []
    def fail_walk(error):
        raise error

    for dirpath, dirnames, filenames in _os.walk(wd, onerror=fail_walk):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d not in _WALK_PRUNE]
        for name in filenames:
            if not name.lower().endswith((".md", ".txt")):
                continue
            fp = _os.path.join(dirpath, name)
            rel = _os.path.relpath(fp, wd).replace("\\", "/")
            if rel.startswith(_DOC_DIR_MARKS):
                continue
            try:
                out.append({"name": rel, "size": _os.path.getsize(fp)})
            except OSError:
                # Do not silently turn a partial filesystem read into an empty
                # or incomplete publish queue; the durable batch can retry.
                raise
    return out


def pending(task_id, platform):
    """待发章节清单：任务工作目录里的章节文件 − 已发章号，按章号升序。

    已发口径连载链合并（含校准所得平台实况），所以任何一个批次任务上发
    起发布，看到的都是整本书的待发清单。已存草稿的章同样剔除（内容已在
    平台草稿箱，再跑只会造重复稿；提交发布走平台草稿箱由用户手工完成）。
    返回 (list, err)；list 项
    {chapter_no, file, size}，file 为工作目录相对路径。章号解析不出的
    文件不进自动发布（防同章多文件误发），API 单章发（manager 直调）
    不受此限。
    """
    from .. import store
    task = store.get_task(task_id)
    if not task:
        return [], "任务不存在"
    wd = str(task.get("workdir") or "").strip()
    files = _auto()._workdir_chapter_files(wd) if wd and _auto()._isdir(wd) else []
    if not files:
        # workdir 缺失/异常：退回 run 成品口径（最新 run 优先）
        for r in store.task_runs(task_id):
            _wd, fs = store.run_artifacts(r.get("id") or "", limit=800)
            if fs:
                files = fs           # 任一 run 的成品口径都从任务首跑起，取到即够
                break
    # A scheduled chapter has been accepted by the platform queue, but is not
    # published yet.  Keep it out of the next batch so a retry cannot enqueue
    # a duplicate while preserving the published count for the UI and limits.
    done = (_auto().ledger.published_chapters(task_id, platform)
            | _auto().ledger.drafted_chapters(task_id, platform)
            | _auto().ledger.scheduled_chapters(task_id, platform)
            | _auto().ledger.review_chapters(task_id, platform))
    # 分卷标注（2026-10-08 用户需求：待发清单按卷展示）：卷计划解析不出时
    # volume 为空串，前端不分组平铺。
    plan = []
    try:
        from . import volumes as _volumes
        max_chapter = max((_auto().ledger.parse_chapter_no(str(f.get("name") or ""))
                           for f in files), default=0)
        plan = _volumes.plan_for_task(task, upto=max_chapter)
    except Exception:
        plan = []
    early_target = None
    try:
        early = store.serial_early_finish_plan(task)
        if early:
            early_target = int(early.get("target_chapter") or 0) or None
    except (AttributeError, TypeError, ValueError):
        early_target = None
    out, seen = [], set()
    for f in files:
        name = str(f.get("name") or "")
        if not name.lower().endswith((".md", ".txt")):
            continue
        if name.startswith(_DOC_DIR_MARKS):
            continue
        n = _auto().ledger.parse_chapter_no(name)
        if n <= 0 or (early_target is not None and n > early_target) or n in done or n in seen:
            continue
        seen.add(n)
        vol = _volumes.volume_for(n, plan) if plan else None
        out.append({"chapter_no": n, "file": name, "size": f.get("size") or 0,
                    "volume": (vol.get("name") or "") if vol else ""})
    out.sort(key=lambda x: x["chapter_no"])
    return out, ""


def _append_selected_unlisted_file(items, task, platform, chapter_no, filename):
    """Add one explicitly selected chapter whose number is only in its heading.

    The regular batch manifest intentionally requires a chapter number in the
    filename to avoid guessing among duplicate chapters. A one-file API request
    is unambiguous, so it may use the validated document heading and still go
    through the same reconciliation, retry, and idempotency path as every
    other automatic upload.
    """
    from pathlib import Path
    from . import manager

    if not filename or not chapter_no:
        return items
    filename = str(filename).replace("\\", "/")
    if any(str(item.get("file") or "").replace("\\", "/") == filename
           for item in items):
        return items
    # A parseable filename that is absent from pending was excluded for a
    # reason (already published, duplicate number, or release boundary).
    if _auto().ledger.parse_chapter_no(Path(filename).name) > 0:
        return items

    wd = str((task or {}).get("workdir") or "").strip()
    if not wd:
        return items
    root = Path(wd).resolve()
    path = (root / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return items
    if not path.is_file() or path.suffix.lower() not in (".md", ".txt"):
        return items
    parsed_no, _title, _body, error = manager.read_chapter(str(path))
    if error or int(parsed_no or 0) != int(chapter_no):
        return items

    try:
        from .. import store
        early = store.serial_early_finish_plan(task)
        early_target = int((early or {}).get("target_chapter") or 0)
    except (AttributeError, TypeError, ValueError):
        early_target = 0
    if early_target and int(chapter_no) > early_target:
        return items

    done = (_auto().ledger.published_chapters(task.get("id"), platform)
            | _auto().ledger.drafted_chapters(task.get("id"), platform)
            | _auto().ledger.scheduled_chapters(task.get("id"), platform)
            | _auto().ledger.review_chapters(task.get("id"), platform))
    if int(chapter_no) in done:
        return items
    rel = path.relative_to(root).as_posix()
    items = list(items)
    items.append({"chapter_no": int(chapter_no), "file": rel,
                  "size": path.stat().st_size, "volume": ""})
    items.sort(key=lambda item: int(item.get("chapter_no") or 0))
    return items


def chapter_review_states(task, items, quality, hard_ok, hard_reason="", direct_ok=True):
    """逐章最新评审证据与实际发布门禁同时展示，不把单章通过当作放行。"""
    from .. import store, quality_gate
    try:
        reviewed_through = int(quality.get("reviewed_through") or 0)
    except (ValueError, TypeError):
        reviewed_through = 0
    runs = []
    for tid in store.serial_chain_ids(task.get("id")):
        runs.extend(store.task_runs(tid) or [])
    evidence = {}
    for run in sorted(runs, key=lambda r: str(r.get("id") or ""), reverse=True):
        verdict = run.get("verdict") if run.get("status") == "done" else None
        if not isinstance(verdict, dict):
            continue
        for score in verdict.get("chapter_scores") or []:
            if not isinstance(score, dict):
                continue
            try:
                n = int(score.get("chapter") or 0)
            except (ValueError, TypeError):
                continue
            if n > 0 and n not in evidence:
                evidence[n] = (score, run)
    for item in items:
        score, run = evidence.get(item["chapter_no"], ({}, {}))
        state, reason = "pending", "尚无有效单章评审"
        try:
            valid = (score.get("passed") is True
                     and int(score.get("reviewer_count") or 0) >= quality_gate.MIN_REVIEWERS
                     and score.get("reviewer_consensus") is True)
        except (ValueError, TypeError):
            valid = False
        if score.get("passed") is False:
            state, reason = "failed", "单章质量评审未通过"
        elif score and not valid:
            reason = "有效评审人数不足或未形成共识"
        elif valid:
            state, reason = "passed_blocked", "单章已过审，发布门禁未过"
            if not hard_ok:
                reason = hard_reason
            elif not quality.get("allowed"):
                reason = "；".join(quality.get("blockers") or []) or reason
            elif item["chapter_no"] > reviewed_through:
                reason = "目标章节超出当前发布评审版本"
            elif not direct_ok:
                reason = "平台发布流程尚未校准，可人工确认发布，暂不可自动直发"
            else:
                state, reason = "ready", "单章及发布门禁均已通过"
            try:
                reviewed_at = _auto().time.mktime(_auto().time.strptime(run.get("ended_at") or "", "%Y-%m-%d %H:%M:%S"))
                if _auto().os.path.getmtime(_auto().os.path.join(task.get("workdir") or "", item["file"])) > reviewed_at + 1:
                    state, reason = "stale", "正文在评审后修改，需要重新评审"
            except (OSError, ValueError, TypeError):
                pass
        item.update(review_status=state, review_reason=reason,
                    review_passed=valid and state != "stale",
                    publish_ready=state == "ready", review_run_id=run.get("id") or "")
    return items


def _automatic_retry_status(task_id):
    """Return the small, UI-safe projection of a durable batch retry."""
    with _auto()._BATCH_JOB_LOCK:
        record = _auto()._load_batch_jobs().get(str(task_id))
        status = str((record or {}).get("status") or "")
        if not isinstance(record, dict) or status not in (
                "retry_wait", "blocked_verdict"):
            return None
        try:
            attempt = max(0, int(record.get("attempt") or 0))
        except (TypeError, ValueError):
            attempt = 0
        try:
            retry_at = float(record.get("next_retry_at") or 0)
        except (TypeError, ValueError):
            retry_at = 0
        return {"platform": str(record.get("platform") or ""),
                "status": status, "error": str(record.get("error") or ""),
                "attempt": attempt, "next_retry_at": retry_at}


def status(task_id):
    """前端视图：待发清单 + 护栏状态 + 自动发布进度。

    护栏分两层给前端：guard_ok/guard_reason=自动发布路径的发布判定（硬护栏+
    自动质量决策），
    管「发布」类按钮；draft_ok/draft_reason=硬护栏（每日上限/建书
    确认），管「存草稿」按钮；自动质量决策产生的警告单独返回，避免把其
    显示成仍会阻断自动发布的错误。"""
    from .. import store
    from . import manager
    ent = _auto().ledger.books_for(str(task_id))   # 沿连载链继承：续写批次同书同账
    task = store.get_task(task_id)
    books = []
    for plat, info in ent.items():
        pend, err = _auto().pending(task_id, plat)
        hard_ok, hard_why = _auto().guards(task_id, plat)
        if not _auto()._book_ready(info):
            hard_ok, hard_why = False, "该平台建书结果未确认（缺少作品 ID），请重试创建作品或登记已有作品"
        blockers = []
        quality_warnings = []
        quality = {"allowed": False, "blockers": []}
        if task and _auto()._book_ready(info):
            release_flags = manager.automatic_release_kwargs(
                auto_submit=True, as_draft=False)
            quality = manager._quality_release_guard(
                task, plat, action="publish", **release_flags)
            if not quality.get("allowed"):
                blockers = [str(b) for b in (quality.get("blockers") or [])]
            quality_warnings = [str(w) for w in (quality.get("warnings") or [])]
        ok, why = hard_ok, hard_why
        if ok and blockers:
            ok, why = False, "质量门禁拦截：%s" % "；".join(blockers)
        direct_ok = _auto().calibrated(plat)
        _auto().chapter_review_states(task or {}, pend, quality, hard_ok, hard_why, direct_ok)
        published = _auto().ledger.published_chapters(task_id, plat)
        drafted = _auto().ledger.drafted_chapters(task_id, plat) - published
        scheduled = _auto().ledger.scheduled_chapters(task_id, plat) - published
        books.append({"platform": plat, "bound": True,
                      "title": info.get("title") or "",
                      "pending": len(pend),
                      "items": pend,
                      "published_latest": max(published, default=0),
                      "next_chapter": pend[0]["chapter_no"] if pend else None,
                      "ready_count": sum(p["publish_ready"] for p in pend),
                      "review_passed_count": sum(p["review_passed"] for p in pend),
                      "drafted_nos": sorted(drafted),
                      "scheduled_nos": sorted(scheduled),
                      "last_publish_at": info.get("remote_last_publish_at") or "",
                      "last_schedule_at": info.get("remote_last_schedule_at") or "",
                      "guard_ok": ok, "guard_reason": why,
                      "draft_ok": hard_ok, "draft_reason": hard_why,
                      "quality_blockers": blockers,
                      "quality_warnings": quality_warnings,
                      "quality_forced": bool(quality.get("forced")),
                      "drafted": len(drafted),
                      "scheduled": len(scheduled),
                      "calibrated": direct_ok})
    run = _auto()._running.get(str(task_id)) or None
    if run:
        run = dict(run)
    active = run and run.get("status") in ("running", "preparing", "reclaiming")
    retry = None if active else _auto()._automatic_retry_status(task_id)
    return {"books": books, "running": run, "retry": retry}