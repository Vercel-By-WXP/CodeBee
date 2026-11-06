# -*- coding: utf-8 -*-
"""额度见底期 → 恢复日（2026-11-09 14:09）的检查与恢复脚本。

把交接单第 3 节第 3 条那段 curl 变成**可校验**的动作，同时把「续跑验收看
verdict」代码化，避免 11-09 靠人眼比对字段。

    py -3.13 scripts/quota_recovery.py --check                      # 只读，随时可跑
    py -3.13 scripts/quota_recovery.py --verify-run <run_id>       # 只读，出人话结论
    py -3.13 scripts/quota_recovery.py --resume-tasks --dry-run    # 只看会做什么
    py -3.13 scripts/quota_recovery.py --resume-tasks --confirm-quota-restored

判据的字段陷阱（11-06 15:00 / 15:2x 从生产详情量出来的，别再踩）：

1. `/api/state` 的 `verdict` 是 `main.py:5034 _state_run_summary` 的**压缩白名单**
   （`pass/publishable/verify_pass/review_pass/end_chapter/quality_status` 六键），
   真实 run 只带 `{"publishable": False}`；判质量必须走 `/api/runs/<id>` 详情。
2. 429 写在 step 的 `summary` / `note`（`error` 常为空）；而且 request id 里
   会巧合出现 "429" → 只认 `429 Too Many Requests` / `last status: 429`。
3. `completed_seats` 在 `verdict.review_resources.completed_seats`，不是顶层；
   `review_fallbacks` 在真实 run 里**根本没有这个键**。`local-heuristic` 是
   `app/core/local_fallback.py` 的常量、只出现在守测期望值里，不是落盘基线。
4. 真调模型成功步按 `cost_usd > 0` 数：断点续跑复用成稿是 `cost=0/model=''`。
5. **verdict 有两种落盘 schema，按字段形状分，不能只看 `type`**：
   `type=doc`（短稿）带 `review_resources.completed_seats` / 数值 `overall`；
   `type=serial_novel`（连载，11-09 要续跑的就是它）带 `review_status` /
   `global_reviewer_count` / `review_degradation_reasons`，**没有 `review_resources`、
   `overall` 是 null**。旧版 `judge_run` 对 serial 详情一律报「这是 /api/state
   压缩版，请走详情」——它本来就是详情。所以「评审席位 0→1」这条 A/B 硬基线
   **只适用 doc 链**；serial 链要看 `review_status: not_evaluated → evaluated`。
6. 真实样本 `r-20261106-002051-8753` 是 `global_reviewer_count=2`、
   `review_independence=cross-vendor`，却仍 `review_status=not_evaluated`、
   `review_quality=degraded`、`publishable=False` → **排了 2 个评审席位
   不等于评审跑完了**，别拿 reviewer_count 当恢复成功的证据。
7. serial 的 `review_degraded` 会被 `review_config.assess`（`review_config.py:174`）
   按「评分低于最低门槛」重写；`review_status`（`pipeline.py:6164`）才是评估前的
   原始信号。判「评审有没有真跑」看 `review_status` 与降级理由，不看这个布尔。

8. **429 字面不等于「额度没恢复」的全部信号**：生产 step 里真实存在
   `[1310][已达到 7 天使用上限，2026-11-09 14:09:00 后可继续使用]` 与
   `litellm.RateLimitError`（含 exit 0 假完成的 Aider 转录），两者都不含
   `429 Too Many Requests` / `last status: 429`，旧 `RATE_LIMIT_RE` 检不出。
   `judge_recovery` 另认一组零歧义额度原话，避免 11-09 假绿。

9. **`pause_control.paused=0` 不等于「可以 retry」**：`store.retry_task:1986-1988`
   拒的是「该 task 下还有 `queued/running` run」，与暂停标志无关。只把
   `paused` 解成 false 时 `pause_control` 立刻归零，但 run 仍 `running`，
   `POST /api/tasks/<id>/retry` 仍被拒；四本同时解除暂停更会直接齐跑抢额度。
   `judge_retry_readiness` 按真实前置判，`--check` 不再拿 paused 数当续跑信号。

脚本**不替人判断额度是否恢复**：`--resume-tasks` 必须显式带
`--confirm-quota-restored`，否则拒绝任何写操作。写之前还会用 `plan_resume`
核对真实 `/api/automation` 清单：待恢复 id 缺失、或白名单外出现被停用任务，
一律拒绝写（清单已变，盲 POST 会开错任务）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request

BASE = "http://127.0.0.1:8765"
QUOTA_RECOVERY_AT = "2026-11-09 14:09"
# 14:19 停用的四个任务（第五个本来就是 disabled，不用恢复）
TASK_IDS = (
    "auto-20261008-154934-6259",
    "auto-20261008-154914-2446",
    "auto-20260921-140044-8443",
    "auto-20260921-135806-8411",
)
# 交接单第 3 节第 1 条：14:19 停用的是「4 个 enabled 定时任务」，
# 第 5 个本来就是 disabled；白名单外再出现停用任务 = 清单变了，别盲写。
KNOWN_DISABLED_IDS = frozenset(("auto-20260921-135622-8720",))
# 16:3x 生产 /api/state 只读取证：11:38 暂停的 4 条 09:48 批次 run 对应的 task。
# 11-09 要 retry 的就是这 4 个；别按 run id 猜，retry 的入口是 task id。
RESUME_TASK_IDS = (
    "t-20261022-104907-1000",
    "t-20261029-102103-5910",
    "t-20261104-170153-4815",
    "t-20261105-152926-2324",
)
# 对齐 store.retry_task:1986-1988 的拒写条件与 store.py:1085 的终态集合。
ACTIVE_RUN_STATUSES = ("queued", "running")
TASK_TERMINAL_STATUSES = ("done", "failed", "cancelled", "timeout")
RATE_LIMIT_RE = re.compile(r"429\s+Too\s+Many\s+Requests|last\s+status:\s*429", re.I)
# 额度耗尽 / 上游限流原话（15:5x 从生产 26 条 run 的 step 只读取证）：
#   [1310][已达到 7 天使用上限，2026-11-09 14:09:00 后可继续使用]
#   litellm.RateLimitError（含 exit 0 假完成的 Aider 转录）
# 这两种最关键的额度信号都不带 "429 Too Many Requests"，旧正则整条漏判。
# 词表与 app/core/error_codes.py:_QUOTA_STRONG 逐字对齐（守测用 ast 锁防漂移）；
# 绝不放裸 "429" / 裸 "quota" / 裸「上限」：生产里有
# "CLI 输出超过捕获上限" 与 request id 里的 429 数字巧合。
QUOTA_STRONG_WORDS = (
    "预扣费", "剩余额度", "额度不足", "insufficient balance",
    "insufficient quota", "insufficient credit", "insufficient credits",
    "quota exhausted", "exceed quota limit", "quota limit exceeded",
    "monthly quota", "余额不足", "欠费停", "payment required",
    "billing address", "套餐已到期", "套餐到期", "订阅已到期",
    "subscription expired", "plan has expired",
)
QUOTA_EXTRA_RE = re.compile(
    r"\[131[09]\]|已达到\s*\d+\s*天使用上限|RateLimitError|request\s+rejected", re.I)
_RL_FIELDS = ("summary", "error", "output", "note")
# main.py:5034 _state_run_summary 的 verdict 白名单，compact 严格按这个集合判。
STATE_VERDICT_KEYS = frozenset(("pass", "publishable", "verify_pass",
                                "review_pass", "end_chapter", "quality_status"))
# 评审证据缺口（区别于「评分低于最低门槛」那种正常的判据否决）。
_INCOMPLETE_REVIEW_WORDS = ("未完成", "未判定", "证据不完整", "缺少可定位正文引文")


def _open(base):
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api_get(base, path):
    with _open(base).open(base + path, timeout=15) as resp:
        return json.load(resp)


def api_post(base, path, body):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with _open(base).open(req, timeout=15) as resp:
        return json.load(resp)


# ---------------------------------------------------------------- 判据（纯函数）

def _step_rate_limited(step):
    """只认真 429；request id 里的数字巧合不算。"""
    for key in _RL_FIELDS:
        if RATE_LIMIT_RE.search(str(step.get(key) or "")):
            return True
    return False


def _step_quota_blocked(step):
    """认额度耗尽原话（非 429 字面）；裸 429 / 裸「上限」一律不算。"""
    for key in _RL_FIELDS:
        text = str(step.get(key) or "")
        low = text.lower()
        if any(w.lower() in low for w in QUOTA_STRONG_WORDS) or QUOTA_EXTRA_RE.search(text):
            return True
    return False


def detect_schema(verdict):
    """按落盘字段形状判 schema；compact 按白名单精确判，不靠「键少」猜。"""
    if not isinstance(verdict, dict) or not verdict:
        return "unknown"
    if isinstance(verdict.get("review_resources"), dict) or verdict.get("type") == "doc":
        return "doc"
    if (verdict.get("type") == "serial_novel" or "review_status" in verdict
            or "chapter_scores" in verdict or "global_reviewer_count" in verdict):
        return "serial_novel"
    if "publishable" in verdict and set(verdict) <= STATE_VERDICT_KEYS:
        return "compact"
    return "unknown"


def judge_run(run):
    """输入 `/api/runs/<id>` 的 `run` 对象，输出可比对的结构化结论。"""
    verdict = run.get("verdict") or {}
    if not isinstance(verdict, dict):
        verdict = {}
    schema = detect_schema(verdict)
    resources = verdict.get("review_resources") or {}
    if not isinstance(resources, dict):
        resources = {}
    steps = [s for s in (run.get("steps") or []) if isinstance(s, dict)]

    rate_limited = [s.get("n") for s in steps if _step_rate_limited(s)]
    quota_blocked = [s.get("n") for s in steps if _step_quota_blocked(s)]
    model_steps = sum(1 for s in steps if (s.get("cost_usd") or 0) > 0)
    seats = resources.get("completed_seats")
    independence = verdict.get("review_independence")
    publishable = verdict.get("publishable")
    review_status = verdict.get("review_status")
    review_degraded = verdict.get("review_degraded")
    reasons = [str(x) for x in (verdict.get("review_degradation_reasons") or []) if x]
    incomplete_reasons = [r for r in reasons
                          if any(w in r for w in _INCOMPLETE_REVIEW_WORDS)]

    if schema == "doc":
        # 三件套字段是否齐全：state 的压缩版只有 publishable，不能拿它判质量。
        fields_complete = all(k in verdict for k in
                              ("publishable", "review_independence", "overall")) and bool(resources)
        honest = bool(fields_complete and publishable is False
                      and independence == "no-review" and seats == 0)
    elif schema == "serial_novel":
        # 连载链没有 review_resources；诚实标注 = 「质量未判定 → 不放行」。
        fields_complete = all(k in verdict for k in
                              ("publishable", "review_status", "review_degraded",
                               "review_independence")) and "global_reviewer_count" in verdict
        honest = bool(fields_complete and publishable is False
                      and review_status == "not_evaluated"
                      and review_degraded is True and bool(incomplete_reasons))
    else:
        fields_complete = False
        honest = False

    return {
        "run_id": run.get("id"),
        "status": run.get("status"),
        "schema": schema,
        "fields_complete": fields_complete,
        "publishable": publishable,
        "overall": verdict.get("overall"),
        "threshold": verdict.get("threshold"),
        "review_independence": independence,
        "completed_seats": seats,
        "required_seats": resources.get("required_seats"),
        # 真实 run 里通常没有这个键，缺省就是空列表；不要凭空补 local-heuristic。
        "review_fallbacks": verdict.get("review_fallbacks") or [],
        "review_status": review_status,
        "review_quality": verdict.get("review_quality"),
        "review_degraded": review_degraded,
        "review_degradation_reasons": reasons,
        "incomplete_review_reasons": incomplete_reasons,
        "reviewer_count": verdict.get("global_reviewer_count"),
        "reviewer_consensus": verdict.get("global_reviewer_consensus"),
        "quality_status": verdict.get("quality_status"),
        "successful_model_steps": model_steps,
        "rate_limited_steps": rate_limited,
        "quota_blocked_steps": quota_blocked,
        "last_step_rate_limited": bool(steps) and _step_rate_limited(steps[-1]),
        "verdict_honest": honest,
    }


def judge_recovery(d):
    """判「额度恢复后的真跑」是否达标。不看 publishable：稿子分低被拦是判据
    正常工作，不是额度没恢复。输出 blockers 人话缺口，11-09 不用人眼比字段。"""
    blockers = []
    notes = []
    if not d["fields_complete"]:
        blockers.append("verdict schema=%s 字段不足，无法判质量" % d["schema"])
    if d["rate_limited_steps"]:
        blockers.append("仍有 %d 步 429（n=%s）" % (
            len(d["rate_limited_steps"]),
            ",".join(str(n) for n in d["rate_limited_steps"][:12])))
    quota_steps = d.get("quota_blocked_steps") or []
    if quota_steps:
        blockers.append("仍有 %d 步额度耗尽原话（n=%s）：[1310] 七天上限 / "
                        "RateLimitError 等非 429 字面，旧正则检不出" % (
                            len(quota_steps),
                            ",".join(str(n) for n in quota_steps[:12])))
    if d["successful_model_steps"] < 1:
        blockers.append("没有真调模型的步骤（cost_usd>0）")

    if d["schema"] == "doc":
        seats, req = d["completed_seats"], d["required_seats"]
        if d["review_independence"] == "no-review" or not seats or (req and seats < req):
            blockers.append("评审席位 %s/%s、independence=%s（仍是 no-review）" % (
                seats, req, d["review_independence"]))
    elif d["schema"] == "serial_novel":
        if d["review_status"] != "evaluated":
            blockers.append("review_status=%s，评审证据未判定" % d["review_status"])
        if d["incomplete_review_reasons"]:
            blockers.append("降级理由里仍有 %d 条评审证据缺口" %
                            len(d["incomplete_review_reasons"]))
        if not d["reviewer_count"]:
            blockers.append("global_reviewer_count=%s，没有真实评审员" % d["reviewer_count"])
        if d["reviewer_count"]:
            notes.append("global_reviewer_count=%s 只说明排了席位，不等于评审跑完"
                         "（见底期真实样本就是 2 席仍 not_evaluated）" % d["reviewer_count"])
        if d["review_quality"] == "degraded":
            notes.append("review_quality=degraded 可能只是评分低于门槛；"
                         "review_config.assess 会按评分重写 review_degraded，"
                         "判「评审有没有真跑」看 review_status 与降级理由")
    else:
        blockers.append("schema=%s，没有对应恢复判据" % d["schema"])
    return {"recovery_ok": not blockers, "blockers": blockers, "notes": notes}


def human_verdict(d):
    schema = d["schema"]
    if not d["fields_complete"]:
        if schema == "compact":
            return ("字段不足：这是 /api/state 的压缩 verdict（白名单只留 6 键），"
                    "判三件套请改用 --verify-run 走 /api/runs/<id> 详情")
        if schema == "unknown":
            return "不认识的 verdict schema，别拿它下质量结论（先看 /api/runs/<id> 详情的键）"
        return "详情字段仍不足（schema=%s），别拿它下质量结论" % schema

    bits = ["schema=%s" % schema]
    if d["publishable"] is True:
        bits.append("可发布")
    else:
        if schema == "doc":
            reason = ("无真实评审席位（%s / %s 席）" %
                      (d["review_independence"], d["completed_seats"])
                      if d["review_independence"] == "no-review" or d["completed_seats"] == 0
                      else "评分 %s < 阈值 %s" % (d["overall"], d["threshold"]))
        elif schema == "serial_novel":
            if d["review_status"] == "not_evaluated":
                reason = ("评审证据未判定：review_status=%s、review_quality=%s、%d 条降级理由（首条：%s）" %
                          (d["review_status"], d["review_quality"],
                           len(d["review_degradation_reasons"]),
                           str(d["review_degradation_reasons"][0])[:60]
                           if d["review_degradation_reasons"] else "无"))
            else:
                reason = ("全局/章级评审未过或仍有 major 问题：review_status=%s、quality_status=%s" %
                          (d["review_status"], d["quality_status"]))
        else:
            reason = "评分 %s < 阈值 %s" % (d["overall"], d["threshold"])
        bits.append("不可发布：" + reason)
        if d["verdict_honest"]:
            bits.append("判据层已诚实标注，不是假绿")
    bits.append("真调模型成功 %d 步" % d["successful_model_steps"])
    if d["rate_limited_steps"]:
        bits.append("限流 %d 步（n=%s）%s" % (
            len(d["rate_limited_steps"]),
            ",".join(str(n) for n in d["rate_limited_steps"]),
            "、末步 429" if d["last_step_rate_limited"] else ""))
    else:
        bits.append("无 429 字面限流步骤")
    quota_steps = d.get("quota_blocked_steps") or []
    if quota_steps:
        bits.append("额度耗尽原话 %d 步（n=%s）" % (
            len(quota_steps), ",".join(str(n) for n in quota_steps)))
    elif not d["rate_limited_steps"]:
        bits.append("无额度耗尽原话")
    return "；".join(bits)


def human_recovery(r):
    if r["recovery_ok"]:
        lines = ["恢复判据：通过（无 429、真调模型、评审证据完整）"]
    else:
        lines = ["恢复判据：未通过，%d 条缺口" % len(r["blockers"])]
        lines.extend("  - " + b for b in r["blockers"])
    lines.extend("  提醒：" + n for n in r["notes"])
    return "\n".join(lines)


# ------------------------------------------------- 恢复计划 / 续跑前置（纯函数）

def plan_resume(tasks):
    """输入 GET /api/automation 的 tasks，输出恢复计划 + 写前 blockers。

    见底期真实清单是 5 条全 disabled：4 条待恢复、1 条本来 disabled。
    「待恢复 id 缺失」或「白名单外还有被停用的任务」都说明清单已变，
    这时盲 POST 会把不该开的任务开回来、或漏开被误停的任务。
    """
    by_id = {t.get("id"): t for t in (tasks or [])
             if isinstance(t, dict) and t.get("id")}
    missing = [tid for tid in TASK_IDS if tid not in by_id]
    unexpected = sorted(tid for tid, t in by_id.items()
                        if tid not in TASK_IDS and not t.get("enabled")
                        and tid not in KNOWN_DISABLED_IDS)
    known = sorted(tid for tid in by_id
                   if tid in KNOWN_DISABLED_IDS and not by_id[tid].get("enabled"))
    return {
        "total": len(by_id),
        "missing": missing,
        "unexpected_disabled": unexpected,
        "known_disabled": known,
        "already_enabled": [tid for tid in TASK_IDS
                            if (by_id.get(tid) or {}).get("enabled")],
        "targets": [{"id": tid,
                     "enabled": bool((by_id.get(tid) or {}).get("enabled")),
                     "next_run": (by_id.get(tid) or {}).get("next_run") or ""}
                    for tid in TASK_IDS if tid in by_id],
    }



def judge_retry_readiness(state):
    """输入 GET /api/state，判 4 个待续跑 task 是否真能 POST /api/tasks/<id>/retry。

    store.retry_task:1986-1988 拒的是「该 task 下还有 queued/running run」，
    跟 pause_control 无关；store.py:900 收尸把 running → failed（不看 paused），
    store.py:941-955 再把 task 同步成终态。所以「解除暂停」≠「可续跑」：
    只把 paused 清成 false 时 pause_control.paused=0，但 run 仍是 running，
    retry 仍被拒，四本同时解除暂停还会立刻齐跑抢见底池。
    """
    runs = [r for r in (state.get("runs") or []) if isinstance(r, dict)]
    tasks = {t.get("id"): t for t in (state.get("tasks") or [])
             if isinstance(t, dict) and t.get("id")}
    active = [r for r in runs if r.get("status") in ACTIVE_RUN_STATUSES]
    active_for_resume = [r for r in active
                         if r.get("task_id") in RESUME_TASK_IDS]
    other_active = [r for r in active
                    if r.get("task_id") not in RESUME_TASK_IDS]
    task_states = {tid: (tasks.get(tid) or {}).get("status") or "(缺失)"
                   for tid in RESUME_TASK_IDS}
    nonterminal = [tid for tid, st in task_states.items()
                   if st not in TASK_TERMINAL_STATUSES]
    blockers = []
    if active_for_resume:
        blockers.append("仍有 %d 条 run 处于 queued/running（%s）：store.retry_task 会拒"
                        "「任务仍在运行中，不能重试」；先重启 8765 让收尸把 running → failed，"
                        "只解除暂停不算" % (
                            len(active_for_resume),
                            ",".join(str(r.get("id")) for r in active_for_resume[:6])))
    if nonterminal:
        blockers.append("%d 个待续跑 task 还不是终态（%s）：重启回填 store.py:941 未完成，"
                        "retry 仍会被拒" % (
                            len(nonterminal),
                            ",".join("%s=%s" % (tid, task_states[tid])
                                     for tid in nonterminal[:6])))
    notes = []
    if other_active:
        notes.append("另有 %d 条其它 task 的 active run，不属于待续跑集合、不阻塞 retry，"
                     "但 11-09 别和续跑并发" % len(other_active))
    return {
        "resume_task_ids": list(RESUME_TASK_IDS),
        "active_runs": [{"id": r.get("id"), "task_id": r.get("task_id"),
                         "status": r.get("status"), "paused": bool(r.get("paused")),
                         "pause_phase": r.get("pause_phase"),
                         "interrupted": r.get("interrupted")}
                        for r in active_for_resume],
        "other_active_run_count": len(other_active),
        "task_states": task_states,
        "terminal_tasks": [tid for tid in RESUME_TASK_IDS
                           if task_states[tid] in TASK_TERMINAL_STATUSES],
        "retry_ready": not active_for_resume and not nonterminal,
        "blockers": blockers,
        "notes": notes,
    }


# ---------------------------------------------------------------- 动作

def cmd_check(base):
    print("额度恢复参考时刻：%s（脚本不替你确认额度，恢复前别带 --confirm-quota-restored）"
          % QUOTA_RECOVERY_AT)
    tasks = api_get(base, "/api/automation").get("tasks") or []
    by_id = {t.get("id"): t for t in tasks}
    plan = plan_resume(tasks)
    enabled = 0
    for tid in TASK_IDS:
        t = by_id.get(tid)
        if not t:
            print("  [缺失] %s —— 定时任务清单里找不到这个 id" % tid)
            continue
        on = bool(t.get("enabled"))
        enabled += on
        print("  [%s] %-32s name=%-14s next_run=%s" % (
            "开" if on else "停", tid, str(t.get("name") or "")[:14], t.get("next_run")))
    for tid in plan["known_disabled"]:
        print("  [保持停用] %-32s 本来就 disabled，不在恢复集合里" % tid)
    for tid in plan["unexpected_disabled"]:
        print("  [异常] %-32s 白名单外也被停用，可能是误停，先人工核对" % tid)
    state = api_get(base, "/api/state")
    pause = state.get("pause_control") or {}
    print("pause_control: %s" % json.dumps(pause, ensure_ascii=False))
    paused = [r for r in (state.get("runs") or [])
              if str(r.get("id") or "").startswith("r-20261106-0948")]
    print("09:48 批次 run %d 条：%s" % (
        len(paused), ", ".join("%s(%s/%s)" % (r.get("id"), r.get("status"),
                                              r.get("pause_phase")) for r in paused)))
    ready = judge_retry_readiness(state)
    print("续跑前置：%s（按 store.retry_task 的真实门槛判，不按 paused 数）" % (
        "可 retry" if ready["retry_ready"] else "不可 retry"))
    for tid, st in ready["task_states"].items():
        print("  [task] %-28s status=%s" % (tid, st))
    for b in ready["blockers"]:
        print("  [缺口] %s" % b)
    for n in ready["notes"]:
        print("  [提醒] %s" % n)
    if plan["missing"] or plan["unexpected_disabled"]:
        conclusion = ("清单异常（缺失 %d / 白名单外停用 %d），先人工核对 /api/automation，别开跑"
                      % (len(plan["missing"]), len(plan["unexpected_disabled"])))
    elif enabled < len(TASK_IDS):
        conclusion = ("仍处额度见底期抑制态（启用 %d/%d），不要开跑"
                      % (enabled, len(TASK_IDS)))
    elif not ready["retry_ready"]:
        conclusion = ("4 个定时任务已恢复，但续跑前置未满足（%d 条缺口）：先重启 8765 清残留；"
                      "别只解除暂停——paused=0 不等于 retry 可用。重启后重跑 --check"
                      % len(ready["blockers"]))
    else:
        conclusion = ("4 个定时任务已恢复、4 个 task 全终态且无 active run：可 "
                      "POST /api/tasks/<id>/retry；只先 retry 一本（4 个 task_id 见上），"
                      "别四本齐跑抢见底池，再 --verify-run <id> 看 blockers")
    print("结论：%s" % conclusion)
    return 0


def cmd_verify_run(base, run_id):
    detail = api_get(base, "/api/runs/" + run_id)
    run = detail.get("run") or detail
    d = judge_run(run)
    print("%s  status=%s  schema=%s" % (run_id, d["status"], d["schema"]))
    for key in ("publishable", "overall", "threshold", "review_independence",
                "completed_seats", "required_seats", "review_fallbacks",
                "review_status", "review_quality", "review_degraded",
                "reviewer_count", "reviewer_consensus", "quality_status",
                "incomplete_review_reasons", "review_degradation_reasons",
                "successful_model_steps", "rate_limited_steps",
                "quota_blocked_steps",
                "last_step_rate_limited", "verdict_honest"):
        value = d[key]
        if key == "rate_limited_steps" and isinstance(value, list):
            shown = ",".join(str(n) for n in value[:12])
            value = shown + (" …共 %d 步" % len(value) if len(value) > 12 else "")
        elif isinstance(value, list) and (len(value) > 3 or
                                          (value and len(str(value[0])) > 80)):
            value = "%d 条（首条：%s…）" % (len(value), str(value[0])[:70])
        print("  %-26s %s" % (key, value))
    print("结论：" + human_verdict(d))
    print(human_recovery(judge_recovery(d)))
    return 0


def cmd_resume_tasks(base, confirm, dry_run):
    if not confirm:
        print("拒绝：--resume-tasks 会写生产调度，必须显式带 --confirm-quota-restored，"
              "并先自行确认 %s 额度已恢复" % QUOTA_RECOVERY_AT)
        return 2
    plan = plan_resume(api_get(base, "/api/automation").get("tasks") or [])
    print("写前核对：清单 %d 条，待恢复 %d 条，本来 disabled %d 条%s" % (
        plan["total"], len(plan["targets"]), len(plan["known_disabled"]),
        "（dry-run，不写）" if dry_run else ""))
    for tid in plan["known_disabled"]:
        print("  [保持停用] %s" % tid)
    if plan["missing"]:
        print("拒绝写：待恢复 id 在清单里找不到 %s —— 清单已变，先人工核对" % plan["missing"])
        return 1
    if plan["unexpected_disabled"]:
        print("拒绝写：白名单外还有被停用的任务 %s —— 可能是 11-06 误停，先人工确认"
              % plan["unexpected_disabled"])
        return 1
    if plan["already_enabled"]:
        print("  [已启用] %s（重复 toggle 幂等，但会重算 next_run）" % plan["already_enabled"])
    print("目标：开回 %d 个定时任务%s" % (len(TASK_IDS), "（dry-run，不写）" if dry_run else ""))
    for tid in TASK_IDS:
        print("  POST /api/automation/%s/toggle {\"enabled\":true}" % tid)
        if dry_run:
            continue
        try:
            res = api_post(base, "/api/automation/%s/toggle" % tid, {"enabled": True})
        except Exception as exc:  # noqa: BLE001 - 任一失败立即停，别把半恢复当成功
            print("  [失败] %s：%r" % (tid, exc))
            return 1
        task = res.get("task") or {}
        if res.get("ok") is False or task.get("enabled") is False:
            print("  [失败] %s 返回 %s" % (tid, json.dumps(res, ensure_ascii=False)[:200]))
            return 1
        print("  [ok] %s enabled=%s" % (tid, task.get("enabled", res.get("ok"))))
    if dry_run:
        print("dry-run 结束，生产未改动")
        return 0
    tasks = {t.get("id"): t for t in (api_get(base, "/api/automation").get("tasks") or [])}
    bad = [tid for tid in TASK_IDS
           if not (tasks.get(tid) or {}).get("enabled")
           or not (tasks.get(tid) or {}).get("next_run")]
    if bad:
        print("复核失败：以下任务 enabled/next_run 未就绪 %s" % bad)
        return 1
    print("复核通过：4 个任务 enabled=true 且 next_run 非空。")
    print("后续（脚本不代做）：重启 8765（POST /api/selfupdate/restart）→ "
          "一本串行续跑（POST /api/tasks/<id>/retry，续跑前 min_words 调 3000+）→ "
          "--verify-run <id> 看 verdict 与恢复判据")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="额度见底期 / 恢复日检查")
    ap.add_argument("--base", default=BASE, help="服务地址（隔离实例可覆盖，默认生产 8765）")
    ap.add_argument("--check", action="store_true", help="只读复核调度与暂停状态（默认）")
    ap.add_argument("--verify-run", metavar="RUN_ID", help="只读判一条 run 的 verdict")
    ap.add_argument("--resume-tasks", action="store_true", help="开回 4 个定时任务（写操作）")
    ap.add_argument("--dry-run", action="store_true", help="与 --resume-tasks 合用，只打印不写")
    ap.add_argument("--confirm-quota-restored", action="store_true",
                    help="人工确认额度已恢复；缺这个标志拒绝任何写操作")
    args = ap.parse_args(argv)
    if args.resume_tasks:
        return cmd_resume_tasks(args.base, args.confirm_quota_restored, args.dry_run)
    if args.verify_run:
        return cmd_verify_run(args.base, args.verify_run)
    return cmd_check(args.base)


if __name__ == "__main__":
    sys.exit(main())
