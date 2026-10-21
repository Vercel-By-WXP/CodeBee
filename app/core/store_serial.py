# -*- coding: utf-8 -*-
"""序列书与提前完本的存储逻辑。

从 store 拆出（单文件行数治理），原模块再导出保持旧调用面；
跨模块引用经 _store() 解析，保持运行时状态与测试打桩语义。
"""
from __future__ import annotations


def _store():
    from . import store
    return store


_CH_FILE_RE = _store().re.compile(r"^chapter-(\d{1,4})\.md$")


def serial_book_chapter_numbers(task):
    """Return chapter numbers currently present in the serial work directory."""
    wd = str((task or {}).get("workdir") or "")
    if not wd:
        return []
    try:
        return sorted({int(m.group(1)) for p in _store().Path(wd).glob("chapter-*.md")
                       if (m := _CH_FILE_RE.match(p.name))})
    except OSError:
        return []


def serial_book_progress(task):
    """这本书已经写到第几章：工作目录里的 chapter-*.md 是事实标准（续写批次
    共用同一目录，天然包含全部历史章）；文件缺失时沿 continues 链推算兜底。"""
    chapters = _store().serial_book_chapter_numbers(task)
    last = max(chapters, default=0)
    if last:
        return last
    seen = set()
    cur = task
    while cur and cur.get("serial") and cur["id"] not in seen and len(seen) < 20:
        seen.add(cur["id"])
        s = cur["serial"]
        try:
            start = int(s.get("start_chapter") or 1)
        except Exception:
            start = 1
        runs = _store().task_runs(cur["id"])
        batch = 0
        for r in runs:
            o = r.get("outline")
            if o and o.get("chapters") and not o.get("degraded"):
                batch = len(o["chapters"])
                break
        # 从没跑过的任务不计入进度：计划章数 ≠ 已写成章数
        if runs:
            batch = batch or int(s.get("chapters") or 0)
        if batch:
            last = max(last, start + batch - 1)
        cur = _store().get_task(str(s.get("continues") or ""))
    return last


def serial_early_finish_plan(task):
    """Return the active shortened-ending plan for a serial chain, if any."""
    cur, seen = task, set()
    while cur and cur.get("id") not in seen and len(seen) < 50:
        seen.add(cur.get("id"))
        plan = _store()._norm_early_finish((cur.get("serial") or {}).get("early_finish"))
        if plan:
            return plan
        parent = str((cur.get("serial") or {}).get("continues") or "")
        cur = _store().get_task(parent) if parent else None
    return None


def early_finish_info(task_id):
    """Return the current ending-plan state for the early-finish UI."""
    info = _store().continue_info(task_id)
    if info is None:
        return None
    info["can"] = bool(info.get("can") and not info.get("early_finish"))
    if info.get("early_finish"):
        info["reason"] = "已有提前完结规划；后续批次会按最终章继续完成"
    return info


def early_finish_existing_task(task_id, target_chapter):
    """Return an existing ending batch for an idempotent repeated request."""
    task = _store().get_task(task_id)
    if not task:
        return None
    plan = _store().serial_early_finish_plan(task)
    if not plan:
        return None
    try:
        target = int(target_chapter)
    except (TypeError, ValueError):
        return None
    if target != int(plan.get("target_chapter") or 0):
        return None
    chain = _store().serial_chain_ids(task_id)
    return _store().get_task(chain[-1]) if chain else task


def early_finish_task(task_id, target_chapter, reason=""):
    """Create the first shortened-ending continuation batch.

    Existing chapter files remain the source of truth. The new batch carries a
    durable target so later continuation cannot accidentally grow the book past
    the replanned ending.
    """
    task = _store().get_task(task_id)
    if not task:
        return False, "任务不存在", None
    if not task.get("serial"):
        return False, "只有连载任务支持提前完结", None
    if _store().serial_early_finish_plan(task):
        return False, "该连载已经有提前完结规划", None
    info = _store().continue_info(task_id)
    if not info or not info.get("can"):
        return False, (info or {}).get("reason") or "当前不能提前完结", None
    try:
        target = int(target_chapter)
    except (TypeError, ValueError):
        return False, "最终章号必须是整数", None
    if target > 500:
        return False, "最终章号不能超过 500", None
    if target < 1:
        return False, "最终章号必须是正整数", None
    if target <= int(info.get("last_chapter") or 0):
        return False, "最终章号必须大于当前已完成章节", None
    plan = {"target_chapter": target, "source_task": task_id,
            "requested_at": _store().time.strftime("%Y-%m-%d %H:%M:%S")}
    if str(reason or "").strip():
        plan["reason"] = str(reason).strip()[:240]
    batch = min(20, target - int(info["last_chapter"]))
    # Pin the plan on the current task and chain root before creating the child
    # batch. This closes the bypass where an older batch's entry could start an
    # uncapped branch after early-finish was requested. Re-check under LOCK so
    # two rapid clicks cannot create competing ending plans.
    pinned_ids = []
    previous = {}
    with _store().LOCK:
        current = _store()._TASKS.get(task_id) or _store().get_task(task_id)
        if not current:
            return False, "任务不存在", None
        if _store().serial_early_finish_plan(current):
            return False, "该连载已经有提前完结规划", None
        root = current
        seen = set()
        while root and root.get("id") not in seen:
            seen.add(root.get("id"))
            parent_id = str((root.get("serial") or {}).get("continues") or "")
            parent = _store().get_task(parent_id) if parent_id else None
            if not parent:
                break
            root = parent
        for item in (root, current):
            if not item or item.get("id") in pinned_ids:
                continue
            tid = item["id"]
            pinned_ids.append(tid)
            previous[tid] = dict((item.get("serial") or {}).get("early_finish") or {})
            item["serial"] = dict(item.get("serial") or {})
            item["serial"]["early_finish"] = plan
            _store()._save_json(_store().paths.TASKS_DIR / (tid + ".json"), item)
    ok, err, new = _store().continue_task(task_id, chapters=batch)
    if not ok:
        with _store().LOCK:
            for tid in pinned_ids:
                item = _store()._TASKS.get(tid) or _store().get_task(tid)
                if not item:
                    continue
                if previous.get(tid):
                    item["serial"]["early_finish"] = previous[tid]
                else:
                    item["serial"].pop("early_finish", None)
                _store()._save_json(_store().paths.TASKS_DIR / (tid + ".json"), item)
        return False, err, None
    return True, "", new


def update_early_finish_task(task_id, target_chapter=None, reason=None, cancel=False):
    """Adjust or cancel an existing early-ending plan before it is complete."""
    task = _store().get_task(task_id)
    if not task:
        return False, "任务不存在", None
    plan = _store().serial_early_finish_plan(task)
    if not plan:
        return False, "当前没有提前完结规划", None
    info = _store().continue_info(task_id)
    if not info:
        return False, "任务不存在", None
    latest_id = info.get("early_finish_latest_task_id") or task_id
    if latest_id != task_id:
        # Details for a serial chain are often opened on the first batch. Keep
        # those controls useful by resolving the operation to the current
        # latest batch instead of forcing the user to hunt through the chain.
        latest = _store().get_task(latest_id)
        if not latest:
            return False, "最新连载批次不存在，请刷新后重试", None
        task_id = latest_id
        task = latest
        info = _store().continue_info(task_id)
        plan = _store().serial_early_finish_plan(task)
        if not info or not plan:
            return False, "当前没有提前完结规划", None
    if info.get("early_finish_state") == "completed":
        return False, "提前完结规划已经完成，不能再修改", None
    if task.get("status") in ("queued", "running"):
        return False, "任务正在运行中，完成或取消后再调整", None
    for run in _store()._RUNS.values():
        if run.get("task_id") == task_id and run.get("status") in ("queued", "running"):
            return False, "有运行中的记录，请先取消", None
    chain = _store().serial_chain_ids(task_id) or [task_id]
    if cancel:
        with _store().LOCK:
            for tid in chain:
                item = _store()._TASKS.get(tid) or _store().get_task(tid)
                if not item:
                    continue
                serial_data = dict(item.get("serial") or {})
                serial_data.pop("early_finish", None)
                item["serial"] = serial_data
                _store()._save_json(_store().paths.TASKS_DIR / (tid + ".json"), item)
            _store().bump_state()
        return True, "", _store().get_task(task_id)
    if target_chapter is None:
        return False, "最终章号必须是整数", None
    try:
        target = int(target_chapter)
    except (TypeError, ValueError):
        return False, "最终章号必须是整数", None
    if target > 500:
        return False, "最终章号不能超过 500", None
    if target < 1:
        return False, "最终章号必须是正整数", None
    last = int(info.get("last_chapter") or 0)
    if target <= last:
        return False, "最终章号必须大于当前已完成章节", None
    current_target = int(plan.get("target_chapter") or 0)
    if target < current_target:
        try:
            from .publish import ledger, manager
            published = set()
            scheduled = set()
            for platform in manager.PLATFORMS:
                published.update(ledger.published_chapters(task_id, platform))
                scheduled.update(ledger.scheduled_chapters(task_id, platform))
            published_ahead = sorted(n for n in published if n > target)
            scheduled_ahead = sorted(n for n in scheduled if n > target)
        except Exception:
            # A target reduction must not silently cross publication state that
            # could not be verified. The user can retry after the ledger recovers.
            return False, "无法核对发布台账，暂不能缩短最终章", None
        if published_ahead:
            return False, ("最终章不能早于已发布的第 %d 章" % published_ahead[-1]), None
        if scheduled_ahead:
            return False, ("最终章不能早于已排期的第 %d 章" % scheduled_ahead[-1]), None
    revised = dict(plan)
    revised["target_chapter"] = target
    if reason is not None:
        text = str(reason).strip()
        if text:
            revised["reason"] = text[:240]
        else:
            revised.pop("reason", None)
    revised["updated_at"] = _store().time.strftime("%Y-%m-%d %H:%M:%S")
    with _store().LOCK:
        for tid in chain:
            item = _store()._TASKS.get(tid) or _store().get_task(tid)
            if not item:
                continue
            serial_data = dict(item.get("serial") or {})
            serial_data["early_finish"] = dict(revised)
            item["serial"] = serial_data
            _store()._save_json(_store().paths.TASKS_DIR / (tid + ".json"), item)
        _store().bump_state()
    return True, "", _store().get_task(task_id)