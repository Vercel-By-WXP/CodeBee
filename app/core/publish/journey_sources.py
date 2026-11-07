# -*- coding: utf-8 -*-
"""把**真实落盘状态**只读地映射成 ``journey`` 事件流。

为什么需要这一层
----------------
``journey.py`` 的指标口径是对的，但上一版 ``journey_report.py`` 只吃手写
jsonl——没有事件采集时报告只能显示 unknown，指标等于没上线。本模块不造
事件、不推测、不写盘，只把仓库里本来就在落的三类事实翻译过来：

===========================  ==========================================
来源                          能提供什么
===========================  ==========================================
``tasks/*.json``              ``task_created``（created_at）、取消/失败终态
``publish/outcomes.json``     作者登记的审核/签约结论（唯一可信的「过签」）
``publish/publish-*.jsonl``   发布动作、``quality_forced`` 强制放行
``publish/books.json``        作品绑定，只用于给孤儿回执找**精确**归属候选
===========================  ==========================================

三条刻意的保守口径（改之前先看守测）
------------------------------------
1. **``status=done`` 不等于过签**。``pipeline`` 的 done 只表示流程收尾，
   质量结论看 verdict / 平台回执。所以 done 的书若没有 ``outcomes.json``
   的 ``stage``，一律判 unknown，绝不生成 ``accepted``。
2. **``quality_forced`` 缺 ``source`` 时不可判定**。``ledger.py`` 明确写过：
   早先那批 forced 全是前端自动重试代填的，把它算成「人明知有风险还投」
   等于把 bug 记成人性；反过来算「没救火」同样没有证据。只有
   ``source == "manual"`` 才生成 ``force_publish``；``auto`` 是程序代填，
   单列计数但不进救火口径；缺失/未知来源一律哨兵 → unknown。
3. **埋点只能记录启用之后的动作**：历史 run 里 ``retry_task`` 不写来源、
   解除暂停还会把 ``pause_requested_at`` 清成 None，只读回溯不出人工痕迹。
   契约真源在 ``telemetry_contract.py``：四类救火里三类有埋点、
   ``manual_revise`` 无产品入口（不适用）。但**启用时刻未声明**
   （``telemetry_since=None``，默认）或建单早于启用时刻的书，一律带
   ``legacy_no_telemetry`` 哨兵被判 unknown。宁可真被判过 0 本，也不许
   把「查不到历史记录」当成「没救火」。

哨兵 ``UNOBSERVABLE_KIND`` 不是真实事件，是用来阻止「把没埋点当成没救火」
的显式标记；守测锁死它**不在** ``journey.VALID_KINDS`` 里。将来补齐埋点，
只需把对应 kind 从 ``UNOBSERVED_RESCUE_KINDS`` 移进 ``OBSERVED_RESCUE_KINDS``
并同步守测，指标会自动开始产出真实数字——不用改 journey 的判据。

dev / prod
----------
纯只读：不 import ``paths``（那个模块绑定全局 DATA_DIR），不连 8765，
不写任何台账。``--data-dir`` 指到哪就读哪；读生产库请显式传
``%APPDATA%/CodeBee``（或 ``--prod``），报告会写明来源。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import journey, telemetry_contract as contract

# 与 outcome.is_novel_task / stats._LIVE_ACTIONS 对齐；守测锁一致性，防口径漂移。
NOVEL_TYPES = frozenset(("novel", "serial_novel", "short_story"))
LIVE_ACTIONS = frozenset(("upload_chapter", "upload_chapter_scheduled",
                          "publish_short_story", "create_book"))

# 只有签约成功算 accepted；审核/签约被拒算 rejected。
# live / submitted / in_review 是「已发布 / 待审」，不是过签终态。
STAGE_TERMINALS = {
    "signed": "accepted",
    "rejected": "rejected",
    "signing_rejected": "rejected",
}
# 任务库里的中途夭折状态（不是过签终态，journey 单列 aborted 不进分母）。
# timeout 没有对应 kind，不硬映射。
TASK_STATUS_TERMINALS = {"cancelled": "cancelled", "failed": "failed"}
RUNNING_STATUSES = frozenset(("running", "queued", "pending"))

# 契约真源在 telemetry_contract（写侧 rescue_telemetry 与读侧共用）；
# 这里只留别名，兼容已有守测与调用方。
OBSERVED_RESCUE_KINDS = contract.OBSERVED_RESCUE_KINDS
NOT_APPLICABLE_RESCUE_KINDS = contract.NOT_APPLICABLE_RESCUE_KINDS
UNOBSERVED_RESCUE_KINDS = contract.UNOBSERVED_RESCUE_KINDS

UNOBSERVABLE_KIND = "__telemetry_unobservable__"

TASK_EVENT = "task_created"
FORCED_MANUAL = "manual"
FORCED_AUTO = "auto"


def _read_json(path):
    """返回 (对象, 问题说明)。坏文件不吞：问题进 coverage.problems。"""
    try:
        with open(str(path), "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except (OSError, ValueError, TypeError) as exc:
        return None, "%s: %s" % (path.name, exc)


def _read_jsonl(path):
    """返回 (记录列表, 坏行数)。坏行不静默丢弃，只计数上报。"""
    rows, bad = [], 0
    try:
        with open(str(path), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (ValueError, TypeError):
                    bad += 1
                    continue
                if not isinstance(rec, dict):
                    bad += 1
                    continue
                rows.append(rec)
    except OSError as exc:
        return [], "%s: %s" % (path.name, exc)
    return rows, bad


def _sentinel(task_id, reason, source):
    """不可判定标记：classify_journey 会因不认识的 kind 判 unknown。"""
    return {"task_id": task_id, "kind": UNOBSERVABLE_KIND,
            "reason": reason, "source": source}


def load_tasks(data_dir):
    """只读 tasks/*.json，返回 (task 列表, 统计, 问题列表)。"""
    folder = Path(data_dir) / "tasks"
    tasks, problems, stat = [], [], {"exists": folder.is_dir(), "files": 0,
                                     "bad_files": 0}
    if not folder.is_dir():
        problems.append("tasks/: 目录不存在")
        return tasks, stat, problems
    for path in sorted(folder.glob("*.json")):
        stat["files"] += 1
        obj, err = _read_json(path)
        if err or not isinstance(obj, dict):
            stat["bad_files"] += 1
            problems.append("tasks/ " + (err or "不是 JSON 对象"))
            # 坏文件不能当成「这本书没救火」，用文件名占位造一条不可判定记录。
            tasks.append({"id": path.stem, "__bad_file__": True,
                          "created_at": ""})
            continue
        obj.setdefault("id", path.stem)
        obj["__file__"] = path.name
        tasks.append(obj)
    return tasks, stat, problems


def load_outcomes(data_dir):
    """只读 publish/outcomes.json；不存在不是错误，但要如实报存在性。"""
    path = Path(data_dir) / "publish" / "outcomes.json"
    stat = {"exists": path.exists(), "task_keys": 0, "orphan_keys": 0,
            "with_stage": 0, "terminal_on_task": 0, "progress_only": 0,
            "bad_stage": 0}
    problems = []
    if not path.exists():
        return {}, stat, problems
    obj, err = _read_json(path)
    if err or not isinstance(obj, dict):
        problems.append("outcomes.json " + (err or "不是 JSON 对象"))
        return {}, stat, problems
    stat["task_keys"] = len(obj)
    for key, book in obj.items():
        if not isinstance(book, dict):
            problems.append("outcomes.json %s: 条目不是对象" % key)
            continue
        entries = [v for v in book.values() if isinstance(v, dict)]
        stages = [str(e.get("stage") or "").strip() for e in entries]
        stages = [s for s in stages if s]
        if stages:
            stat["with_stage"] += 1
            for s in stages:
                if s in STAGE_TERMINALS:
                    break
            else:
                # 有 stage 但都不是终态（如 live / in_review）：不算结论。
                stat["with_stage"] -= 1
                stat["progress_only"] += 1
            for s in stages:
                if s not in STAGE_TERMINALS and s not in ("live", "submitted", "in_review"):
                    stat["bad_stage"] += 1
                    problems.append("outcomes.json %s: 不认识的 stage=%s" % (key, s))
        else:
            stat["progress_only"] += 1
    return obj, stat, problems


def load_publish_records(data_dir):
    """只读 publish/publish-*.jsonl，返回 (记录列表, 统计, 问题列表)。"""
    folder = Path(data_dir) / "publish"
    stat = {"exists": folder.is_dir(), "files": 0, "rows": 0, "bad_lines": 0,
            "forced": 0, "forced_manual": 0, "forced_auto": 0,
            "forced_unknown_source": 0, "published_ok": 0}
    problems, records = [], []
    if not folder.is_dir():
        problems.append("publish/: 目录不存在")
        return records, stat, problems
    paths = sorted(folder.glob("publish-*.jsonl"))
    for path in paths:
        stat["files"] += 1
        rows, bad = _read_jsonl(path)
        if isinstance(bad, str):
            problems.append("publish/ " + bad)
            continue
        stat["bad_lines"] += bad
        if bad:
            problems.append("%s: %d 行无法解析（不静默丢弃）" % (path.name, bad))
        for rec in rows:
            stat["rows"] += 1
            records.append(rec)
            if rec.get("quality_forced"):
                stat["forced"] += 1
                src = str(rec.get("quality_forced_source") or "").strip().lower()
                if src == FORCED_MANUAL:
                    stat["forced_manual"] += 1
                elif src == FORCED_AUTO:
                    stat["forced_auto"] += 1
                else:
                    stat["forced_unknown_source"] += 1
            if rec.get("ok") and str(rec.get("action") or "") in LIVE_ACTIONS:
                stat["published_ok"] += 1
    return records, stat, problems


def load_rescue_records(data_dir):
    """只读 publish/rescue-*.jsonl（人工救火台账），返回 (记录, 统计, 问题)。"""
    folder = Path(data_dir) / "publish"
    stat = {"exists": folder.is_dir(), "files": 0, "rows": 0,
            "bad_lines": 0, "invalid_rows": 0, "by_kind": {}}
    problems, rows = [], []
    if not folder.is_dir():
        return rows, stat, problems
    for path in sorted(folder.glob(contract.RESCUE_FILE_GLOB)):
        stat["files"] += 1
        data, bad = _read_jsonl(path)
        if isinstance(bad, str):
            problems.append("rescue/ " + bad)
            continue
        stat["bad_lines"] += bad
        if bad:
            problems.append("%s: %d 行无法解析（不静默丢弃）" % (path.name, bad))
        rows.extend(data)
        stat["rows"] += len(data)
    return rows, stat, problems


def _norm_title(value):
    """标题规范化：只去书名号与空白并转小写，**绝不做模糊/相似度匹配**。

    孤儿回执归属一旦用模糊匹配，就可能把 A 书的驳回挂到 B 书头上——
    那比「判不了」更糟。宁可报 unknown 让人手工确认。
    """
    return str(value or "").strip().casefold().strip(
        "《》〈〉『』「」\"' \t\r\n")


def _orphan_work(book):
    """从孤儿回执条目里取**显式**书名 ``work``；没有就空串，绝不猜键名。"""
    if not isinstance(book, dict):
        return ""
    for entry in book.values():
        if isinstance(entry, dict):
            work = _norm_title(entry.get("work"))
            if work:
                return work
    return ""


def load_book_bindings(data_dir):
    """只读 ``publish/books.json``（task ↔ 平台作品绑定）。

    只产出「精确标题匹配」的原料，**不做归属推断、不改任何文件**。
    文件不存在/损坏不是错误也不是绿灯：如实记进 stat / problems，
    让孤儿回执一律保持 unknown。
    """
    path = Path(data_dir) / "publish" / "books.json"
    stat = {"exists": path.exists(), "task_keys": 0, "titles": 0,
            "bad_entries": 0}
    problems = []
    if not path.exists():
        return {}, stat, problems
    obj, err = _read_json(path)
    if err or not isinstance(obj, dict):
        problems.append("books.json " + (err or "不是 JSON 对象"))
        return {}, stat, problems
    bindings = {}
    for tid, plats in obj.items():
        if not isinstance(plats, dict):
            stat["bad_entries"] += 1
            continue
        stat["task_keys"] += 1
        titles = []
        for _plat, entry in plats.items():
            if not isinstance(entry, dict):
                stat["bad_entries"] += 1
                continue
            title = _norm_title(entry.get("title"))
            if title:
                titles.append(title)
                stat["titles"] += 1
        bindings[str(tid)] = frozenset(titles)
    return bindings, stat, problems


def _outcome_terminal(book, task_id, problems):
    """从回执条目取终态 kind；返回 (kind 或 None, 是否有结论证据)。"""
    if not isinstance(book, dict):
        return None, False
    found_stage = False
    for platform, entry in sorted(book.items()):
        if not isinstance(entry, dict):
            continue
        stage = str(entry.get("stage") or "").strip()
        if not stage:
            continue
        found_stage = True
        if stage in STAGE_TERMINALS:
            return STAGE_TERMINALS[stage], True
        if stage not in ("live", "submitted", "in_review"):
            problems.append("outcomes.json %s/%s: 不认识的 stage=%s"
                            % (task_id, platform, stage))
    return None, found_stage


def _ledger_for_task(records, task_id):
    """按 task_id 聚合台账证据：发布成功、force 来源分类。"""
    has_published = False
    forced = []
    for rec in records:
        if str(rec.get("task_id") or "") != task_id:
            continue
        if rec.get("ok") and str(rec.get("action") or "") in LIVE_ACTIONS:
            has_published = True
        if rec.get("quality_forced"):
            forced.append(str(rec.get("quality_forced_source") or "").strip().lower())
    return has_published, forced


def outcome_gaps(tasks, outcomes, bindings):
    """回执登记覆盖率契约（纯函数、只读、零推断）。

    **这不是交付完成率**：它只回答「平台结论有没有被作者登记到对应的书」。
    上线前置 ⓒ 要的就是这份可核对清单——之前只有一句「80 本 done 无结论」
    的文本提醒，人根本不知道该去补哪几本。

    返回结构化 dict：
      -     ``total`` / ``registered`` / ``registration_rate``（**无可判样本时**
        ``None``，显示 unknown；有样本而一本没登记才真的是 0.0）。
        **分母只含「已登记终态结论」+「``status=done`` 但没有结论」**：
        在跑 / 夭折 / 其它状态的书本来就不该有平台结论，放进分母只会
        把覆盖率稀释成一个没有行动含义的数字，单列进 ``out_of_scope``；
      - ``missing``：待登记 task 清单（``done`` ≠ 已投平台，只是流程收尾，
        清单是「去核对」而不是「都欠一条结论」）；
      - ``orphans``：挂在非 task 键上的回执 + 精确匹配候选。
        只有 ``books.json`` 里标题**恰好命中一个** task 才给候选，且
        ``verdict="exact"``；0 个或多个都 ``unknown``。脚本绝不改数据，
        归属必须由人确认后走 ``outcome.record`` 补登。
    """
    novel = []
    task_ids = set()
    for task in tasks:
        tid = str(task.get("id") or "").strip()
        if not tid:
            continue
        task_ids.add(tid)
        if str(task.get("type") or "") in NOVEL_TYPES:
            novel.append(task)

    missing, registered = [], 0
    out_of_scope = {"running": 0, "aborted": 0, "other": 0}
    for task in novel:
        tid = str(task.get("id") or "").strip()
        book = outcomes.get(tid)
        terminal, has_stage = _outcome_terminal(book, tid, [])
        if terminal:
            registered += 1
            continue
        status = str(task.get("status") or "").strip().lower()
        if status != "done":
            key = ("running" if status in ("running", "queued", "pending")
                   else "aborted" if status in ("cancelled", "failed", "timeout")
                   else "other")
            out_of_scope[key] += 1
            continue
        missing.append({
            "task_id": tid,
            "status": status,
            "created_at": str(task.get("created_at") or ""),
            "reason": "stage_not_terminal" if has_stage else "no_entry",
        })

    orphans = []
    for key in sorted(k for k in outcomes if k not in task_ids):
        book = outcomes[key] or {}
        work = _orphan_work(book)
        hits = ([tid for tid, titles in (bindings or {}).items()
                 if work and work in titles] if work else [])
        if len(hits) == 1:
            verdict, candidates = "exact", sorted(hits)
        else:
            verdict, candidates = "unknown", []
        orphans.append({
            "key": str(key),
            "work": work,
            "candidate_task_ids": candidates,
            "verdict": verdict,
            "action": ("需人工确认后由 outcome.record 补登；脚本绝不改数据"
                       if verdict == "exact" else
                       "books.json 无唯一精确匹配 → 保持 unknown，不自动归属"),
        })

    total = registered + len(missing)
    return {
        "novel_tasks": len(novel),
        "total": total,
        "registered": registered,
        "registration_rate": (round(registered / total, 4)
                              if total else None),
        "out_of_scope": out_of_scope,
        "missing": sorted(missing, key=lambda x: x["task_id"]),
        "orphans": orphans,
        "matching": "books.json title 精确匹配（casefold + 去书名号）",
    }


def derive_events(data_dir=".", telemetry_since=None):
    """只读派生：返回 ``{events, coverage}``。

    ``events`` 可直接喂 ``journey.from_events``；``coverage`` 记录每个事件源
    的真实条数与不可判定原因，供报告打印「真被判过样本数」之外的上线缺口。

    ``telemetry_since`` 是埋点启用时刻（``YYYY-MM-DD HH:MM[:SS]``）。
    **None 表示未声明 → 所有书都判不了**；只有建单晚于该时刻的书才可能
    进分母。别拿历史书假装「没救火」。
    """
    root = Path(data_dir)
    tasks, task_stat, problems = load_tasks(root)
    outcomes, outcome_stat, outcome_problems = load_outcomes(root)
    records, ledger_stat, ledger_problems = load_publish_records(root)
    rescue_rows, rescue_stat, rescue_problems = load_rescue_records(root)
    bindings, binding_stat, binding_problems = load_book_bindings(root)
    problems += (outcome_problems + ledger_problems + rescue_problems
                 + binding_problems)
    since = contract.normalize_since(telemetry_since)

    # 按 task 聚合救火台账；坏行 / 无 source / 不认识的 kind 单列，不静默计入。
    rescue_by_task = {}
    for rec in rescue_rows:
        kind = str(rec.get("kind") or "").strip()
        tid = str(rec.get("task_id") or "").strip()
        src = str(rec.get("source") or "").strip()
        if kind not in contract.RESCUE_LOG_KINDS or not tid or not src:
            rescue_stat["invalid_rows"] += 1
            problems.append("rescue-*.jsonl: kind=%s task=%s 缺字段或不认识，不进分母"
                            % (kind or "?", tid or "?"))
            continue
        rescue_by_task.setdefault(tid, set()).add(kind)
        rescue_stat["by_kind"][kind] = rescue_stat["by_kind"].get(kind, 0) + 1

    events = []
    counts = {"novel_tasks": 0, "skipped_non_novel": 0,
              "terminal_evidence": 0, "aborted": 0, "running": 0,
              "done_without_outcome": 0, "forced_unknown_source_tasks": 0,
              "sentinel_unobserved": 0, "sentinel_legacy": 0,
              "post_telemetry": 0, "legacy_without_telemetry": 0,
              "rescue_events": 0, "bad_task_files": 0}

    for task in tasks:
        tid = str(task.get("id") or "").strip()
        if not tid:
            problems.append("tasks/: 存在无 id 的任务文件")
            continue
        if task.get("__bad_file__"):
            counts["bad_task_files"] += 1
            events.append(_sentinel(tid, "task_file_unreadable", "tasks/"))
            continue
        if str(task.get("type") or "") not in NOVEL_TYPES:
            counts["skipped_non_novel"] += 1
            continue
        counts["novel_tasks"] += 1

        created = str(task.get("created_at") or "").strip()
        if created:
            events.append({"task_id": tid, "kind": TASK_EVENT,
                           "ts": created, "source": "tasks/" + str(task.get("__file__") or "")})
        else:
            events.append(_sentinel(tid, "missing_created_at", "tasks/"))
        post = contract.is_post_telemetry(created, since)
        counts["post_telemetry" if post else "legacy_without_telemetry"] += 1

        status = str(task.get("status") or "").strip().lower()
        aborted_kind = TASK_STATUS_TERMINALS.get(status)
        book = outcomes.get(tid)
        outcome_terminal, has_stage_evidence = _outcome_terminal(book, tid, problems)
        terminal = outcome_terminal  # 平台结论优先于任务状态（done ≠ 过签）
        if terminal:
            events.append({"task_id": tid, "kind": "outcome_recorded",
                           "source": "publish/outcomes.json"})
        if terminal:
            counts["terminal_evidence"] += 1
            events.append({"task_id": tid, "kind": terminal, "source": "publish/outcomes.json"})
        elif aborted_kind:
            # 中途夭折：journey 判 aborted，不进分母；不需要救火埋点也能判，
            # 所以**不**给它注入不可观测哨兵（否则会把 aborted 误报成 unknown）。
            counts["aborted"] += 1
            events.append({"task_id": tid, "kind": aborted_kind, "source": "tasks/"})
        elif status in RUNNING_STATUSES:
            counts["running"] += 1
        elif status == "done":
            # 流程收尾但没有平台结论：不是「在跑」，是判不了。
            counts["done_without_outcome"] += 1
            events.append(_sentinel(tid, "done_without_outcome_stage",
                                    "publish/outcomes.json"))
        elif has_stage_evidence:
            # 有 stage 但只是 live / in_review：还没到终态，也不许当成功。
            events.append(_sentinel(tid, "stage_not_terminal", "publish/outcomes.json"))
        elif status == "timeout":
            events.append(_sentinel(tid, "task_status_timeout_unmapped", "tasks/"))
        else:
            events.append(_sentinel(tid, "no_terminal_evidence", "tasks/"))

        for kind in sorted(rescue_by_task.get(tid, ())):
            counts["rescue_events"] += 1
            events.append({"task_id": tid, "kind": kind,
                           "source": "publish/rescue-*.jsonl"})

        has_published, forced = _ledger_for_task(records, tid)
        if has_published:
            events.append({"task_id": tid, "kind": "published",
                           "source": "publish/publish-*.jsonl"})
        if FORCED_MANUAL in forced:
            events.append({"task_id": tid, "kind": "force_publish",
                           "source": "publish/publish-*.jsonl"})
        if any(src not in (FORCED_MANUAL, FORCED_AUTO) for src in forced):
            counts["forced_unknown_source_tasks"] += 1
            # 只有「有终态证据、本来能进分母」的书才需要因来源不明判 unknown。
            # aborted 的书不进分母，救火判不了对它没有意义，不该被哨兵改成
            # unknown —— 否则 coverage 的 aborted 数与指标行会对不上。
            if terminal:
                events.append(_sentinel(tid, "quality_forced_source_unknown",
                                        "publish/publish-*.jsonl"))

        # 埋点契约：仍有救火类型不可观测、或这本书建单早于埋点启用时刻，
        # 有终态证据也不许进分母（没记录 ≠ 没发生）。aborted 的书本来就不
        # 进分母，不注入哨兵，免得被误报成 unknown。
        if terminal and UNOBSERVED_RESCUE_KINDS:
            counts["sentinel_unobserved"] += 1
            events.append(_sentinel(tid, "unobserved:" + ",".join(
                sorted(UNOBSERVED_RESCUE_KINDS)), "telemetry_contract"))
        elif terminal and not post:
            counts["sentinel_legacy"] += 1
            events.append(_sentinel(tid, "legacy_no_telemetry",
                                    "telemetry_contract"))

    # 回执挂在非 task 键上：单列，绝不硬塞进某本书。
    task_ids = set(str(t.get("id") or "") for t in tasks)
    orphans = [k for k in outcomes if k not in task_ids]
    outcome_stat["orphan_keys"] = len(orphans)
    outcome_stat["terminal_on_task"] = len([
        k for k, v in outcomes.items()
        if k in task_ids and _outcome_terminal(v, k, [])[0]])
    for key in orphans:
        problems.append("outcomes.json %s: 没有对应 task 文件，不进分母" % key)

    coverage = {
        "data_dir": str(root.resolve()),
        "sources": {"tasks": task_stat, "outcomes": outcome_stat,
                    "publish_ledger": ledger_stat,
                    "rescue_ledger": rescue_stat,
                    "book_bindings": binding_stat},
        "counts": counts,
        # 回执登记覆盖率（不是交付完成率）；清单供人补登记，脚本零写入。
        "outcome_gaps": outcome_gaps(tasks, outcomes, bindings),
        "telemetry_since": since,
        "observed_rescues": sorted(OBSERVED_RESCUE_KINDS),
        "not_applicable_rescues": sorted(NOT_APPLICABLE_RESCUE_KINDS),
        "unobserved_rescues": sorted(UNOBSERVED_RESCUE_KINDS),
        "problems": problems,
    }
    return {"events": events, "coverage": coverage}


def human_coverage(coverage):
    """把事件源覆盖率说成人话，重点说「还差哪几个埋点」。"""
    s, c = coverage["sources"], coverage["counts"]
    o, l, t, r = (s["outcomes"], s["publish_ledger"], s["tasks"],
                  s["rescue_ledger"])
    lines = [
        "数据来源：%s（只读）" % coverage["data_dir"],
        "真实落盘：task %d 个（可入指标 %d / 非小说跳过 %d / 坏文件 %d）；"
        "回执 %d 键（终态结论 %d，其中能挂 task %d / 非终态或仅进度 %d / 孤儿 %d）；"
        "发布台账 %d 文件 %d 行（坏行 %d）"
        % (t["files"], c["novel_tasks"], c["skipped_non_novel"], t["bad_files"],
           o["task_keys"], o["with_stage"], o["terminal_on_task"],
           o["progress_only"], o["orphan_keys"],
           l["files"], l["rows"], l["bad_lines"]),
        "强制放行证据：共 %d 条（manual %d / auto %d / 来源不明 %d）；"
        "来源不明涉及 %d 本（只有有终态证据的那些才影响分母）"
        % (l["forced"], l["forced_manual"], l["forced_auto"],
           l["forced_unknown_source"], c["forced_unknown_source_tasks"]),
        "救火埋点：已观测 [%s]；不适用 [%s]；未观测 [%s]"
        % (", ".join(coverage["observed_rescues"]) or "无",
           ", ".join(coverage["not_applicable_rescues"]) or "无",
           ", ".join(coverage["unobserved_rescues"]) or "无"),
        "救火台账：%d 文件 %d 行（坏行 %d / 无效行 %d）；启用时刻 %s；"
        "之后新建 %d 本 / 之前 %d 本"
        % (r["files"], r["rows"], r["bad_lines"], r["invalid_rows"],
           coverage["telemetry_since"] or "未声明",
           c["post_telemetry"], c["legacy_without_telemetry"]),
    ]
    if coverage["unobserved_rescues"]:
        lines.append("严格口径：%d 类救火不可观测 → %d 本有终态证据的书一律 unknown，"
                     "不上绿标（没记录 ≠ 没发生）"
                     % (len(coverage["unobserved_rescues"]), c["sentinel_unobserved"]))
    elif not coverage["telemetry_since"]:
        lines.append("严格口径：埋点启用时刻未声明 → %d 本历史书一律 unknown，"
                     "不上绿标（没记录 ≠ 没发生）"
                     % c["legacy_without_telemetry"])
    else:
        lines.append("严格口径：启用时刻 %s 之前的 %d 本单列不进分母，"
                     "之后的 %d 本才可能进分母"
                     % (coverage["telemetry_since"],
                        c["legacy_without_telemetry"], c["post_telemetry"]))
    if c["aborted"]:
        lines.append("中途夭折 %d 本（cancelled/failed）单列，不算完成也不算「无需救火」"
                     % c["aborted"])
    if c["done_without_outcome"]:
        lines.append("提醒：%d 本 status=done 但没有平台结论，done ≠ 过签，判不了"
                     % c["done_without_outcome"])
    g = coverage.get("outcome_gaps") or {}
    if g:
        exact = len([o for o in g["orphans"] if o["verdict"] == "exact"])
        lines.append("回执登记覆盖（≠交付完成率；分母只含 done / 已登记结论）："
                     "真被判过 %d/%d 本，待登记 %d 本；孤儿回执 %d 键"
                     "（books.json 唯一精确匹配 %d / 保持 unknown %d）；"
                     "在跑/夭折未纳分母 %d 本"
                     % (g["registered"], g["total"], len(g["missing"]),
                        len(g["orphans"]), exact, len(g["orphans"]) - exact,
                        sum(g.get("out_of_scope", {}).values())))
    if coverage["problems"]:
        lines.append("读取异常 %d 条（前 3 条）：" % len(coverage["problems"]))
        for p in coverage["problems"][:3]:
            lines.append("  - " + p)
    return "\n".join(lines)
