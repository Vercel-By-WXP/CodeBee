# -*- coding: utf-8 -*-
"""注意力面板数据源（2026-11-01，借鉴 DeepSeek dsh-superagent 的「注意力守恒」
与待办面板哲学）。

原则：**只在用户必须行动或能受益时才说话**——每一条都带「下一步动作」需要的
全部上下文（任务标题、失败原因、可执行的操作语义），前端渲染成待办条。
无事时返回空列表，界面就不出现这个面板（不是显示「一切正常」凑数）。

七类来源（全部只读，2026-11-01 起 4/5 借 Ruflo 的 testgaps 思路找缺口）：
  1. failed_runs     —— 近 N 天失败的运行（最痛的一类，带 reason 与 task 链接语义）
  2. paused_runs     —— 人工暂停的运行（run 级 paused 标志；恢复动作 =
                        POST /api/runs/<id>/pause {paused:false}，条目带 run_id）
  3. stuck_queued    —— 排队中但 resume_enqueue_at 已过期仍没起跑的 run
                        （60 秒兜底没接住的滞留，需要人看一眼）
  4. automation_late —— 启用中的定时任务 next_run 过期 >30 分钟没被触发
                        （调度器可能停了；动作 = POST /api/automation/<id>/run）
  5. serial_gap      —— 在更的连载 36/72 小时没有新产出（断更；动作 = 重试）
  6. dead_override   —— data/publish/flows-*.json 用户覆盖表有动作不过完成契约
                        （load_flow 会静默跳过=死表，2026-11-01 番茄死表考古；
                        动作 = 修正 sel/步骤或删除该文件）
  7. publish_rate    —— 发布成功率（滚动窗口）跌破 95% 目标线且失败数达下限
                        （2026-11-01 KPI 装牙；信息条无动作，处理入口=发布面板）

纪律：绝不抛错——注意力面板挂了不能连累任何主流程；数据残缺的条目直接丢弃。
"""
from __future__ import annotations

import time

from . import paths, store

_DEFAULT_DAYS = 7
_DEFAULT_LIMIT = 30


def _ts(s):
    """run 时间戳（ISO 字符串）→ epoch 秒；解析不了返回 0。"""
    try:
        import datetime
        return datetime.datetime.fromisoformat(str(s).replace("Z", "")).timestamp()
    except Exception:
        return 0.0


def _human_time(epoch):
    if not epoch:
        return ""
    return time.strftime("%m-%d %H:%M", time.localtime(epoch))


def _err_reason(run):
    err = str(run.get("error") or "").strip()
    return err[:160] if err else ""


def _human_dur(seconds):
    """秒 → 人话时长（"3 小时 12 分"）；不足 1 分钟返回空。"""
    m = int(float(seconds or 0) // 60)
    if m < 1:
        return ""
    if m < 60:
        return "%d 分钟" % m
    h, mi = divmod(m, 60)
    if h < 48:
        return "%d 小时 %d 分" % (h, mi)
    return "%d 天 %d 小时" % (h // 24, h % 24)


def collect(days=_DEFAULT_DAYS, limit=_DEFAULT_LIMIT, now=None):
    """返回 {"items": [...], "generated_at": ...}。每条：
    {"kind": "failed_run"|"paused_task"|"stuck_queued", "id", "task_id", "title",
     "time", "time_epoch", "reason", "actions": [...]}"""
    now = time.time() if now is None else float(now)
    items = []
    try:
        tasks = {t.get("id"): t for t in store.list_tasks(200)}
        # 1. 失败运行（按时间倒序，缺 ended_at 的用创建序兜底）
        cutoff = now - max(1, int(days or _DEFAULT_DAYS)) * 86400
        failed = []
        for r in store.list_runs(limit=400):
            status = str(r.get("status") or "")
            if status != "failed":
                continue
            ended = _ts(r.get("ended_at") or r.get("created_at") or "")
            if ended and ended < cutoff:
                continue
            failed.append((ended, r))
        failed.sort(key=lambda x: -x[0])
        for ended, r in failed[:limit]:
            tid = r.get("task_id") or ""
            t = tasks.get(tid) or {}
            title = str(t.get("title") or r.get("title") or tid or "未命名任务")
            items.append({
                "kind": "failed_run", "id": r.get("id") or "",
                "task_id": tid, "run_id": r.get("id") or "", "title": title,
                "time": _human_time(ended), "time_epoch": ended,
                "reason": _err_reason(r),
                "actions": [{"type": "open_run", "run_id": r.get("id") or ""},
                            {"type": "retry", "task_id": tid}]})
        # 2. 人工暂停的运行（暂停是 run 级 paused 标志，任务状态不变——
        # 恢复动作 = POST /api/runs/<id>/pause {paused:false}，需要 run_id）
        for r in store.list_runs(limit=200):
            if not r.get("paused") or str(r.get("status") or "") \
                    not in ("running", "queued"):
                continue
            tid = r.get("task_id") or ""
            t = tasks.get(tid) or {}
            phase = str(r.get("pause_phase") or "")
            items.append({
                "kind": "paused_task", "id": r.get("id") or "",
                "task_id": tid, "run_id": r.get("id") or "",
                "title": str(t.get("title") or r.get("title") or "未命名任务"),
                "time": _human_time(_ts(r.get("pause_requested_at") or "")),
                "time_epoch": _ts(r.get("pause_requested_at") or ""),
                "reason": "人工暂停" + ("（已到暂停闸门）" if phase == "paused"
                                       else "（等待当前步骤结束）"),
                "actions": [{"type": "resume", "run_id": r.get("id") or ""}]})
        # 3. 滞留排队（resume_enqueue_at 已过 5 分钟还没起跑）
        for r in store.list_runs(limit=200):
            if str(r.get("status") or "") != "queued":
                continue
            resume_at = _ts(r.get("resume_enqueue_at") or "")
            if not resume_at or now - resume_at < 300:
                continue
            tid = r.get("task_id") or ""
            t = tasks.get(tid) or {}
            items.append({
                "kind": "stuck_queued", "id": r.get("id") or "",
                "task_id": tid, "run_id": r.get("id") or "",
                "title": str(t.get("title") or r.get("title") or "未命名任务"),
                "time": _human_time(resume_at), "time_epoch": resume_at,
                "reason": "排队唤醒已过期 %d 分钟仍未起跑（应到时间：%s）"
                          % (int((now - resume_at) // 60),
                             str(r.get("resume_enqueue_at") or "")[:19]),
                "actions": [{"type": "open_run", "run_id": r.get("id") or ""}]})
        # 4. 定时失联（Ruflo 的 testgaps 思路：该发生没发生的才要人看——
        #    启用中的自动化 next_run 已过期 >30 分钟还没被 tick 触发，
        #    多半是调度循环死了或被长期占用，值得人看一眼）
        try:
            from . import automation
            for a in automation.list_tasks() or []:
                if not a.get("enabled"):
                    continue
                nr = _ts(a.get("next_run") or "")
                if not nr or now - nr < 1800:
                    continue
                items.append({
                    "kind": "automation_late", "id": a.get("id") or "",
                    "task_id": a.get("task_id") or "",
                    "automation_id": a.get("id") or "",
                    "title": str(a.get("name") or a.get("id") or "定时任务"),
                    "time": _human_time(nr), "time_epoch": nr,
                    "reason": "应于 %s 触发，已迟到 %s 未执行（调度器可能停了）"
                              % (str(a.get("next_run") or "")[:19],
                                 _human_dur(now - nr)),
                    "actions": [{"type": "run_now",
                                 "automation_id": a.get("id") or ""}]})
        except Exception:
            pass
        # 5. 连载断更（testgaps 同款找缺口：在更的连载 N 天没有新产出——
        #    挂自动发布的用 36h 严窗口，纯手动的用 72h 宽窗口且要求近 30 天
        #    还有活动；完结/停更的老书不扰，正在跑/排队的也不扰）
        try:
            latest_end = {}
            active_tasks = set()
            for r in store.list_runs(limit=400):
                tid = r.get("task_id") or ""
                if str(r.get("status") or "") in ("running", "queued"):
                    active_tasks.add(tid)
                e = _ts(r.get("ended_at") or r.get("created_at") or "")
                if e and e > latest_end.get(tid, 0):
                    latest_end[tid] = e
            for tid, t in tasks.items():
                if tid in active_tasks or not t.get("serial"):
                    continue
                last = latest_end.get(tid, 0)
                if not last or last < now - 30 * 86400:
                    continue
                ap = t.get("auto_publish") or {}
                gate_h = 36 if ap.get("enabled") else 72
                if now - last < gate_h * 3600:
                    continue
                items.append({
                    "kind": "serial_gap", "id": "gap-" + str(tid),
                    "task_id": tid, "run_id": "",
                    "title": str(t.get("title") or tid),
                    "time": _human_time(last), "time_epoch": last,
                    "reason": "连载已 %s 没有新产出%s"
                              % (_human_dur(now - last),
                                 "（挂着自动发布，窗口已错过）"
                                 if ap.get("enabled") else ""),
                    "actions": [{"type": "retry", "task_id": tid}]})
        except Exception:
            pass
        # 6. 死覆盖表（2026-11-01 番茄死表考古的告警闭环：契约不过的覆盖表
        #    会被 load_flow 静默跳过——用户以为改了行为，其实从未生效）
        try:
            from pathlib import Path as _Path
            from .publish.publish_browser import _flow_contract_ok
            for f in sorted(_Path(paths.PUBLISH_DIR).glob("flows-*.json")):
                plat = f.stem.replace("flows-", "")
                try:
                    import json as _json
                    d = _json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                for act, steps in (d.items() if isinstance(d, dict) else ()):
                    if act == "_note" or not isinstance(steps, list):
                        continue
                    try:
                        if _flow_contract_ok(plat, act, steps):
                            continue
                    except Exception:
                        continue
                    items.append({
                        "kind": "dead_override", "id": "dead-%s-%s" % (plat, act),
                        "task_id": "", "run_id": "",
                        "title": "覆盖表 %s 的「%s」未生效" % (f.name, act),
                        "time": _human_time(f.stat().st_mtime),
                        "time_epoch": f.stat().st_mtime,
                        "reason": "该动作不过完成契约（缺证明发布被接受的步骤），"
                                  "load_flow 会静默跳过此表——改了也白改",
                        "actions": [{"type": "fix_override",
                                     "file": f.name, "action": act}]})
        except Exception:
            pass
        # 7. 发布成功率告警（2026-11-01，KPI 装牙：滚动窗口跌破 95% 目标线
        #    且失败数达下限才说话——单次失败可能是平台瞬断，不值得打扰人）。
        #    信息条不带动作按钮：事由在最近失败摘要里，处理入口走发布面板。
        try:
            from .publish import stats as _pub_stats
            a = _pub_stats.alert(days=7)
            if a:
                brief = ""
                s = _pub_stats.summary(days=int(a.get("days") or 7))
                if s.get("failures"):
                    f0 = s["failures"][0]
                    brief = "最近失败：%s%s %s" % (
                        ("《%s》" % f0.get("title")) if f0.get("title") else "",
                        f0.get("platform") or "",
                        str(f0.get("error") or "")[:80])
                items.append({
                    "kind": "publish_rate", "id": "pub-rate",
                    "task_id": "", "run_id": "",
                    "title": "发布成功率跌破目标线",
                    "time": _human_time(now), "time_epoch": now,
                    "reason": "近 %d 天成功率 %d%%（%d 次尝试 %d 失败，目标 %d%%）%s"
                              % (int(a.get("days") or 7),
                                 round(float(a.get("rate") or 0) * 100),
                                 int(a.get("attempts") or 0),
                                 int(a.get("failed") or 0),
                                 round(float(a.get("goal") or 0.95) * 100),
                                 ("——" + brief) if brief else ""),
                    "actions": []})
        except Exception:
            pass
    except Exception:
        return {"items": [], "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "degraded": True}
    items.sort(key=lambda x: -float(x.get("time_epoch") or 0))
    return {"items": items[:max(1, int(limit or _DEFAULT_LIMIT))],
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "degraded": False}
