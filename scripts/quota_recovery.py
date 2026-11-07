# -*- coding: utf-8 -*-
"""额度见底期 → 恢复日（2026-11-09 14:09）的检查与恢复脚本。

把交接单第 3 节第 3 条那段 curl 变成**可校验**的动作，同时把「续跑验收看
verdict」代码化，避免 11-09 靠人眼比对字段。

    py -3.13 scripts/quota_recovery.py --check                      # 只读，随时可跑
    py -3.13 scripts/quota_recovery.py --verify-run <run_id>       # 只读，出人话结论
    py -3.13 scripts/quota_recovery.py --check-inherit            # 只读，报续跑会倒退哪几章
    py -3.13 scripts/quota_recovery.py --resume-tasks --dry-run    # 只看会做什么
    py -3.13 scripts/quota_recovery.py --resume-tasks --confirm-quota-restored
                                        # 上游窗口变了可加 --recovery-at "YYYY-MM-DD HH:MM"
    py -3.13 scripts/quota_recovery.py --retry-one --dry-run --confirm-quota-restored
                                        # 只预演「先 retry 哪一本」，零 POST
    py -3.13 scripts/quota_recovery.py --retry-one --confirm-quota-restored
                                        # 四道闸全过才真正 POST 一次

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

10. **「可 retry」不等于「retry 不重写已完成章」**（17:37 生产实案）：
    `store.retry_task` 的 `done` 原先只取 `prev["steps"]` 里 `draft-cN done`，
    而 `pipeline.py:4606` 是 `reuse = i in done_set and os.path.exists(ch_file)`
    —— **章号必须进 done_set，磁盘有稿也不够**。中途暂停 / 重启中断的 run
    steps 只覆盖到中断点，上一遍继承来并已复用的章（`inherit.done_chapters`）
    会丢。修复（并回同一 prev 的 inherit 章号）已同步部署包但**未重启**；
    `--check-inherit` 只读复现两版结果，报倒退章清单与真被判过样本数。
    ledger 的 redo 剔除 / 已发布章豁免要读 `publish.ledger.published_chapters`，
    API 不暴露 → 命中 `publishable=False` 完结 verdict 时一律报 `unknown`，
    不上绿标、也不把「主动回炉」误报成「被动倒退」。

11. **「人工确认」不等于「到点了」**（20:3x）：`QUOTA_RECOVERY_AT` 原先只被
    打印进文案，写路径没有任何时间判据。11-06/07/08 只要手滑带上
    `--confirm-quota-restored` 就能把 4 个调度开回去，白烧见底的池子。
    现在 `--resume-tasks` 真写前用 `judge_quota_window` 硬判：未到
    `2026-11-09 14:09`（或 `--recovery-at` 覆盖值）一律拒写且**零 IO**；
    解析不了按 `unknown` fail-closed。`--dry-run` 允许提前预演但打印提醒。
    时间只是**必要非充分条件**：窗口过了也可能账号续期 / 仍见底，
    人工确认照样必需。解析用与 `error_codes.quota_recovery_at()` 同款正则，
    能直接从生产 `[1310]` 原话取时刻，守测做交叉验证。

12. **「只 retry 一本」原先只是交接单里的一句话**（21:4x）：`--check` /
    `--check-inherit` / `judge_retry_readiness` / `judge_quota_window` 四个判据
    都在，但 11-09 唯一真正烧额度的 `POST /api/tasks/<id>/retry` 仍靠手敲
    curl —— 敲错本子（会倒退 4 章）或一次 retry 四本（互推成失败）都会
    白烧刚恢复的额度。`--retry-one` 把四个判据串到写路径：真写前四道闸
    （确认标志 / 恢复窗口 / 重启收尸前置 / 目标继承判据必须 `ok`），
    任一不过零 POST 拒写；目标不填按 `--check-inherit` 推荐值推导，
    **不硬编码 task id**；只 POST 一次，写后复核「其余 3 本没新增 active run」。
    `--dry-run` 允许提前预演、零 POST，但会打印窗口与收尸前置未满足的提醒。

13. **「4 个 task 全终态」不等于「本次重启收尸真的发生了」**（11-07 13:0x）：
    九步流程第 ④ 步 `POST /api/selfupdate/restart` 是全链唯一不可逆的
    生产中断动作；第 ⑤ 步 `--check` 原只判 `retry_ready`（无 active run +
    4 个 task 全终态）就宣布可 retry，看不出这些终态是不是**本次收尸**
    造成的。更坑的是 11-06 09:47:13 那次重启已经给这 4 个 task 的旧 run
    打过一模一样的 `interrupted=restart`，光看「有没有标记」或「最新时刻」
    会拿老账冒充本次（离线复核连抓两次）。
    `/api/state` 的 run 摘要带 `error` 但**不带** `interrupted`
    （`_state_run_summary` 白名单），所以先用 `error` 里的收尸专属文案筛候选，
    再只对候选拉 `/api/runs/<id>` 详情；判据以**重启前那批 active run 的 id**
    为锚点（id 重启前后不变），核对 4 条锚点的 `interrupted` /
    `interrupted_at` / `ended_at` 齐备且同批次、且严格新于全部历史老账。
    同一批次里别的 task 被一并收尸是合法行为，单列提醒不判误伤；
    判不了一律 `unknown`、不上绿标。`--check` 只在重启后（`retry_ready=True`）
    才拉详情，额度见底期零额外 GET。

脚本**不替人判断额度是否恢复**：`--resume-tasks` 必须显式带
`--confirm-quota-restored`，否则拒绝任何写操作。写之前还会用 `plan_resume`
核对真实 `/api/automation` 清单：待恢复 id 缺失、或白名单外出现被停用任务，
一律拒绝写（清单已变，盲 POST 会开错任务）。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
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
# 与 app/core/store.py:47 _RESTART_INTERRUPT_TEXT 逐字对齐（守测用 ast 锁防漂移）。
# 收尸写入的 error 形如「服务重启中断，可重试」；store.py:933 老账回填也认这个前缀。
# 摘要层只有 error 可用（interrupted 不在 _state_run_summary 白名单里）。
RESTART_INTERRUPT_TEXT = "服务重启中断"
REAP_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


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

    注意：`/api/state` 的 run 摘要**不带** `interrupted`，这里不采恒为 None
    的字段自欺；收尸标记走 `/api/runs/<id>` 详情，见 `judge_restart_reaping`。
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
                         "pause_phase": r.get("pause_phase")}
                        for r in active_for_resume],
        "other_active_run_count": len(other_active),
        "task_states": task_states,
        "terminal_tasks": [tid for tid in RESUME_TASK_IDS
                           if task_states[tid] in TASK_TERMINAL_STATUSES],
        "retry_ready": not active_for_resume and not nonterminal,
        "blockers": blockers,
        "notes": notes,
    }


# ------------------------------------------------- 重启收尸自洽性（只读，11-09 第 ⑤ 步）

def _run_reaped_by_restart(summary):
    """摘要层筛候选：只认收尸写入的专属 error 文案，别拿 reason 猜。"""
    return str(summary.get("error") or "").startswith(RESTART_INTERRUPT_TEXT)


def judge_restart_reaping(state, details, expected_ids=None):
    """核对「本次重启收尸」是否干净落在重启前那批 active run 上 —— 纯函数，零 IO。

    `store.py:900` 收尸只碰 `running` / 无退避 `queued`，打
    ``status=failed`` + ``interrupted=restart`` + ``interrupted_at`` +
    ``ended_at``；`store.py:941` 再把对应 task 回填成终态。`--check` 的
    `retry_ready` 只看「4 个 task 全终态 + 无 active run」，**看不出这些终态
    到底是不是本次重启收尸造成的** —— 手工改状态、收尸只做一半、或拿
    11-06 的历史老账冒充，都会让 `retry_ready` 假绿。

    为什么必须用 `expected_ids`：11-06 09:47:13 那次重启已经给 4 个待续跑
    task 的旧 run 打过一模一样的 restart 标记；光看「有没有收尸记录」或
    「全局最新的 interrupted_at」，重启没生效时也会拿老账判成 ok（11-07
    离线复核连抓两次）。只有**重启前那批 active run 的 id** 是无歧义锚点：
    id 重启前后不变，`--check` 的 09:48 批次观察项就是这份清单。

    `store.py:900` 本来就收所有 active run，所以同一批次里若有别的 task
    被一并收尸是**合法**的，单列提醒、不判成误伤（那会是假红）。
    判不了（没给锚点 / 详情缺失 / 字段缺失）一律 `unknown`、不上绿标。

    `details` 是 ``{run_id: /api/runs/<id> 的 run 对象}``，按候选拉。
    """
    expected = [str(x) for x in (expected_ids or []) if str(x)]
    notes = []
    unknown_ids = []
    empty = {"verdict": "unknown", "judged_count": 0, "batch_at": "",
             "batch_ids": [], "batch_size": 0, "blockers": [],
             "notes": notes, "unknown_ids": unknown_ids}

    def fail(verdict, msg, **kw):
        return dict(empty, verdict=verdict, blockers=[msg], **kw)

    if not expected:
        return fail("unknown", "没给重启前 active run 清单（expected_ids），"
                    "分不出本次收尸与历史老账：判不了就不上绿标")
    runs = [r for r in (state.get("runs") or []) if isinstance(r, dict)]
    by_id = {str(r.get("id") or ""): r for r in runs}
    # 锚点清单必须恰好覆盖 4 个待续跑 task：前缀筛漏 / 多筛 / 重影都要先人工核对。
    anchor_tasks = [str((by_id.get(rid) or {}).get("task_id") or "")
                    for rid in expected]
    missing_tasks = sorted(set(RESUME_TASK_IDS) - set(anchor_tasks))
    extra_tasks = sorted(set(anchor_tasks) - set(RESUME_TASK_IDS))
    if (missing_tasks or extra_tasks or len(anchor_tasks) != len(set(anchor_tasks))):
        return fail("blocked", "重启前 active 清单与 4 个待续跑 task 对不上"
                    "（缺 %s / 多 %s / %d 条）：先人工核对，别开跑"
                    % (",".join(missing_tasks) or "无",
                       ",".join(extra_tasks) or "无", len(expected)))
    judged = 0
    bad_shape = []
    ats = []
    for rid in expected:
        d = (details or {}).get(rid)
        if not isinstance(d, dict) or not d:
            unknown_ids.append(rid)
            continue
        judged += 1
        at = str(d.get("interrupted_at") or "")
        if (d.get("interrupted") != "restart" or not REAP_AT_RE.match(at)
                or d.get("status") != "failed" or not d.get("ended_at")):
            bad_shape.append(rid)
            continue
        ats.append(at)
    if unknown_ids:
        return fail("unknown", "%d 条锚点 run 的详情读不到（%s）：判不了就不上绿标，"
                    "走 /api/runs/<id> 人工核对" %
                    (len(unknown_ids), ",".join(unknown_ids[:6])),
                    judged_count=judged)
    if bad_shape:
        return fail("blocked", "%d 条锚点 run 没有完整的收尸标记/终态（%s）："
                    "重启可能没生效或收尸只做了一半，别按 retry_ready 开跑" %
                    (len(bad_shape), ",".join(bad_shape[:6])), judged_count=judged)
    if len(set(ats)) != 1:
        return fail("blocked", "锚点批次的 interrupted_at 不一致（%s）：不是同一次重启，"
                    "先人工核对" % ",".join(sorted(set(ats))[:3]), judged_count=judged)
    batch_at = ats[0]
    # YYYY-MM-DD HH:MM:SS 的字典序就是时间序。非锚点的 restart run 按时刻分三类：
    # 同批次 = 合法的一并收尸；更早 = 历史老账；更晚 = 拿老账冒充本次 / 时钟回拨，
    # 后一类必须拦住，否则「重启没生效」也会被判成 ok（11-07 离线复核抓到）。
    history_at, same_batch_others, future_others = [], [], []
    for rid, d in (details or {}).items():
        if rid in expected or not isinstance(d, dict):
            continue
        at = str(d.get("interrupted_at") or "")
        if d.get("interrupted") != "restart" or not REAP_AT_RE.match(at):
            continue
        if at == batch_at:
            same_batch_others.append(rid)
        elif at < batch_at:
            history_at.append(at)
        else:
            future_others.append(rid)
    if future_others:
        return fail("blocked", "存在 %d 条比锚点批次 %s 更晚的收尸记录（%s）："
                    "可能重启没生效、拿老账冒充本次，或时钟回拨；先人工核对"
                    % (len(future_others), batch_at, ",".join(future_others[:3])),
                    judged_count=judged)
    if same_batch_others:
        notes.append("同一批次另有 %d 条其它 task 的 run 被一并收尸（%s）："
                     "store.py:900 本来就收所有 active，属合法，列出来供人工核对"
                     % (len(same_batch_others), ",".join(same_batch_others[:3])))
    if history_at:
        notes.append("另有 %d 条历史重启老账（最晚 %s），不混进本次判据"
                     % (len(history_at), max(history_at)))
    return {
        "verdict": "ok",
        "judged_count": judged,
        "batch_at": batch_at,
        "batch_ids": list(expected),
        "batch_size": len(expected),
        "blockers": [],
        "notes": notes,
        "unknown_ids": unknown_ids,
    }


# ---------------------------------------------------------------- 恢复窗口判据（只读）

# 与 app/core/error_codes.py:_QUOTA_RECOVERY_RE 同一正则：上游原话
# 「[1310][已达到 7 天使用上限，2026-11-09 14:09:00 后可继续使用]」。
RECOVERY_AT_IN_TEXT_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})(?::\d{2})?\s*后可继续使用")
PLAIN_RECOVERY_AT_RE = re.compile(
    r"^\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})(?::\d{2})?\s*$")


def parse_recovery_at(text):
    """恢复时刻 → datetime；支持常量格式与上游 [1310] 原话。

    解析不了返回 None（判不了就 fail-closed，别给绿标）。与
    error_codes.quota_recovery_at() 同一正则，守测做交叉验证。
    """
    text = str(text or "")
    m = RECOVERY_AT_IN_TEXT_RE.search(text) or PLAIN_RECOVERY_AT_RE.match(text)
    if not m:
        return None
    try:
        return datetime.strptime("%s %s" % (m.group(1), m.group(2)),
                                 "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _now():
    """模块级可注入时钟：守测 patch 它，生产取真实本地时间（GMT+8）。"""
    return datetime.now()


def _human_remaining(seconds):
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return "%d 天 %d 小时 %d 分" % (days, hours, minutes)
    if hours:
        return "%d 小时 %d 分" % (hours, minutes)
    return "%d 分钟" % max(1, minutes)


def judge_quota_window(at_text=None, now=None):
    """恢复窗口判据：reached=True/False/None（None=解析不了，fail-closed）。

    时间只是**必要非充分条件**：窗口过了也可能账号续期 / 仍见底，
    --confirm-quota-restored 的人工确认照样必需。
    """
    at_text = QUOTA_RECOVERY_AT if at_text is None else at_text
    now = _now() if now is None else now
    at = parse_recovery_at(at_text)
    if at is None:
        return {"at_text": str(at_text), "at": None, "now": now,
                "reached": None, "remaining_seconds": 0,
                "human": "恢复时刻解析不了（%s），按 unknown 处理：不写生产调度"
                         % str(at_text)[:80]}
    delta = (at - now).total_seconds()
    if delta > 0:
        return {"at_text": str(at_text), "at": at, "now": now,
                "reached": False, "remaining_seconds": int(delta),
                "human": "距额度恢复窗口 %s 还有 %s（现在 %s）；别恢复调度"
                         % (at.strftime("%Y-%m-%d %H:%M"),
                            _human_remaining(delta),
                            now.strftime("%Y-%m-%d %H:%M"))}
    return {"at_text": str(at_text), "at": at, "now": now,
            "reached": True, "remaining_seconds": 0,
            "human": "额度恢复窗口 %s 已过 %s；仍需 --confirm-quota-restored "
                     "人工确认额度真恢复（时间只是必要非充分条件）"
                     % (at.strftime("%Y-%m-%d %H:%M"),
                        _human_remaining(-delta))}


# ---------------------------------------------------------------- 断点续跑继承判据（只读）

def _chapter_arg(value):
    """章号统一转 int；bool / 非数字一律 None（对齐 store.retry_task:2052-2058）。"""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _draft_done_chapters(steps):
    """store.retry_task:2037-2042 的取法：只认 status=done 的 draft-cN。"""
    out = set()
    for s in steps or []:
        if not isinstance(s, dict) or s.get("status") != "done":
            continue
        role = str(s.get("role") or "")
        if not role.startswith("draft-c"):
            continue
        tail = role.split("c")[-1]
        if tail.isdigit():
            out.add(int(tail))
    return out


def _run_historical_chapters(run):
    """一条 run 里出现过的章号：起草 done 步 / 继承章号 / 章分数（有分数即有稿）。

    口径是「历史出现过的章号并集」，不是「当前磁盘成稿」——磁盘文件不在 API 里，
    判不了，所以只拿落盘字段说话。未过线章（chapter_scores.passed=False）也算，
    但由 redo_risk 把「ledger 主动回炉」与「steps 截断被动丢失」分开判。
    """
    out = _draft_done_chapters(run.get("steps"))
    for c in ((run.get("inherit") or {}).get("done_chapters") or []):
        n = _chapter_arg(c)
        if n is not None:
            out.add(n)
    for scores in (run.get("chapter_scores"),
                   (run.get("verdict") or {}).get("chapter_scores")):
        for c in scores or []:
            if isinstance(c, dict):
                n = _chapter_arg(c.get("chapter"))
                if n is not None:
                    out.add(n)
    return out


def project_retry_inherit(runs, task_id):
    """复现 store.retry_task 在「重启收尸后」为某 task 生成的 inherit.done_chapters。

    纯函数、只读。四处与 store.py 逐条对齐：
      - 收尸派生：queued/running → failed + interrupted=restart（store.py:900）；
        这是 11-09 才会出现的派生状态，单列返回、不计真样本。
      - outline：按 (created_at_ns, id) 倒序取第一个非 degraded（2027-2034）。
      - ledger：倒序找最近一个 publishable 明确的完结 verdict（2077-2082）；
        True 清账、False 触发 redo 剔除。API 没有 published_chapters 与完整
        major_issues → 只报 redo_risk，不猜具体章（判不了就 unknown）。
      - done：最新终态 prev 的 draft-c done ∪ 同 run 的 inherit.done_chapters
        （2037-2061，17:37 修复后）。旧代码 / 部署包未重启时只按 steps。
    """
    rows = [dict(r) for r in (runs or []) if isinstance(r, dict)]
    reaped = []
    for r in rows:
        if r.get("status") in ACTIVE_RUN_STATUSES:
            reaped.append(r.get("id"))
            r["status"] = "failed"
            r["interrupted"] = "restart"
    ordered = sorted(rows, key=lambda r: (r.get("created_at_ns") or 0,
                                          str(r.get("id") or "")), reverse=True)
    outline = None
    for r in ordered:
        o = r.get("outline")
        if o and not o.get("degraded"):
            outline = o
            break
    redo_risk = False
    redo_risk_run_ids = []
    for r in ordered:
        if r.get("status") not in TASK_TERMINAL_STATUSES:
            continue
        pv = r.get("verdict") or {}
        if pv.get("publishable") is True:
            break
        if pv.get("publishable") is False:
            redo_risk = True
            redo_risk_run_ids.append(r.get("id"))
            break
    prev = next((r for r in ordered
                 if r.get("status") in TASK_TERMINAL_STATUSES), None)
    from_steps = _draft_done_chapters((prev or {}).get("steps"))
    from_inherit = set()
    for c in (((prev or {}).get("inherit") or {}).get("done_chapters") or []):
        n = _chapter_arg(c)
        if n is not None:
            from_inherit.add(n)
    limitations = []
    if not outline:
        limitations.append("没有可继承的非降级大纲：retry 不会落 inherit，整本从零重写")
    if redo_risk:
        limitations.append("最近完结 verdict 是 publishable=False：ledger 会剔除未达标章，"
                           "published_chapters 不在 API 里，判不了最终章号")
    if not prev:
        limitations.append("该 task 下没有终态 run：收尸后才有，判不了")
    return {
        "task_id": task_id,
        "judged_run_count": len(rows),
        "derived_reaped_run_ids": reaped,
        "prev_run_id": (prev or {}).get("id") or "",
        "prev_status": (prev or {}).get("status") or "",
        "from_steps": sorted(from_steps),
        "from_inherit": sorted(from_inherit),
        "done_chapters": sorted(from_steps | from_inherit),
        "legacy_done_chapters": sorted(from_steps),
        "outline_available": bool(outline),
        "redo_risk": redo_risk,
        "redo_risk_run_ids": redo_risk_run_ids,
        "limitations": limitations,
    }


def plan_check_inherit(state_runs, details):
    """输入 /api/state 简表 + /api/runs/<id> 详情，判 4 个 task 的续跑倒退。

    verdict 三档：ok（一章不丢）/ regress（明确会丢 N 章，列清单）/
    unknown（判不了，不上绿标）。决策 18：报真被判过样本数，派生收尸单列。
    """
    task_of = {s["id"]: s.get("task_id") for s in (state_runs or [])
               if isinstance(s, dict) and s.get("id")}
    results = []
    for tid in RESUME_TASK_IDS:
        rows = []
        for rid, r in (details or {}).items():
            if not isinstance(r, dict):
                continue
            if (r.get("task_id") or task_of.get(rid)) != tid:
                continue
            row = dict(r)
            row.setdefault("id", rid)
            row["task_id"] = tid
            rows.append(row)
        p = project_retry_inherit(rows, tid)
        hist = set()
        for r in rows:
            hist |= _run_historical_chapters(r)
        lost = sorted(hist - set(p["done_chapters"]))
        lost_legacy = sorted(hist - set(p["legacy_done_chapters"]))
        if not rows:
            verdict, reasons = "unknown", ["该 task 下没有可读的 run 详情，判不了"]
        elif not p["outline_available"] or not p["prev_run_id"]:
            verdict, reasons = "unknown", p["limitations"] or ["缺大纲或终态 run"]
        elif p["redo_risk"]:
            verdict, reasons = "unknown", p["limitations"]
        else:
            verdict = "regress" if lost else "ok"
            reasons = []
        p.update({
            "historical_chapters": sorted(hist),
            "lost_chapters": lost,
            "lost_legacy_chapters": lost_legacy,
            "extra_lost_without_restart": sorted(set(lost_legacy) - set(lost)),
            "verdict": verdict,
            "reasons": reasons,
        })
        results.append(p)
    ok = [r for r in results if r["verdict"] == "ok"]
    recommended = (max(ok, key=lambda r: len(r["done_chapters"]))["task_id"]
                   if ok else "")
    return {
        "judged_run_count": sum(r["judged_run_count"] for r in results),
        "judged_task_count": sum(1 for r in results if r["judged_run_count"]),
        "derived_reaped_count": sum(len(r["derived_reaped_run_ids"]) for r in results),
        "historical_chapter_count": sum(len(r["historical_chapters"]) for r in results),
        "unknown_task_count": sum(1 for r in results if r["verdict"] == "unknown"),
        "results": results,
        "recommended_first": recommended,
    }


def _fmt_chapters(chs, cap=14):
    if not chs:
        return "[]"
    if len(chs) <= cap:
        return "[" + ",".join(str(c) for c in chs) + "]"
    return "[%s … 共 %d 章]" % (",".join(str(c) for c in chs[:cap]), len(chs))


# ---------------------------------------------------------------- retry 写路径判据（纯函数）

def plan_retry_one(state, inherit_plan, task_id=None, confirm=False,
                   window_reached=True, dry_run=False):
    """11-09 唯一烧额度的写操作 POST /api/tasks/<id>/retry 的四道闸。

    纯函数、零 IO；所有拒写都在 POST 之前。判据全部来自已有的真实生产
    判据，不硬编码 task id：
      - inherit_plan：plan_check_inherit 的结果，recommended_first 是
        「一章不丢」里继承最完整的一本；
      - state：judge_retry_readiness 判「无 active run + 4 个 task 全终态」；
      - window_reached：judge_quota_window()["reached"] is True。
    真写时四道闸任一不过即拒；dry-run 只放宽「窗口 / 收尸前置」两条
    （这两条 11-09 重启后才会满足），其余照拦并打印提醒。
    """
    results = {r["task_id"]: r for r in (inherit_plan or {}).get("results") or []}
    recommended = (inherit_plan or {}).get("recommended_first") or ""
    target = task_id or recommended
    judged = (inherit_plan or {}).get("judged_run_count", 0)

    def reject(code, text):
        row = results.get(target) or {}
        return {"target": target, "recommended": recommended, "allow": False,
                "gate": code, "blockers": [text],
                "target_inherit_verdict": row.get("verdict", "missing"),
                "lost_chapters": row.get("lost_chapters") or [],
                "judged_run_count": judged, "retry_ready": None, "notes": []}

    if not confirm:
        return reject("confirm", "缺 --confirm-quota-restored：retry 会真烧额度，拒绝任何写")
    if not dry_run and window_reached is not True:
        return reject("window", "恢复窗口未到 / 解析不了：拒绝写，--dry-run 可提前预演")
    if not recommended:
        return reject("no_safe_task",
                      "--check-inherit 没判出「一章不丢」的本子，别 retry")
    if target not in RESUME_TASK_IDS:
        return reject("unknown_task",
                      "目标 %s 不在 4 个待续跑 task 里，拒绝写" % target)
    row = results.get(target) or {}
    if row.get("verdict") != "ok":
        if row.get("verdict") == "regress":
            return reject("inherit",
                          "目标 %s 会倒退 %d 章 %s：先核对磁盘成稿与历史章号，"
                          "别拿它当第一本" % (
                              target, len(row.get("lost_chapters") or []),
                              _fmt_chapters(row.get("lost_chapters"))))
        return reject("inherit",
                      "目标 %s 的继承判据是 unknown/缺详情：%s" % (
                          target, "；".join(row.get("reasons") or []) or "判不了"))
    readiness = judge_retry_readiness(state or {})
    notes = []
    if not dry_run and not readiness["retry_ready"]:
        return {"target": target, "recommended": recommended, "allow": False,
                "gate": "retry_ready",
                "blockers": ["重启收尸未完成：%s" % (
                    "；".join(readiness["blockers"]) or "retry 前置不满足")],
                "target_inherit_verdict": "ok", "lost_chapters": [],
                "judged_run_count": judged,
                "retry_ready": readiness["retry_ready"], "notes": []}
    # 注：不再单列「目标 task 是否终态」这道闸 —— retry_ready=True 的定义
    # 就是「无 active run 且 4 个 task 全终态」，再加一道是死分支。
    # judge_retry_readiness 的 blockers 已把未回填的 task 逐条点出来。
    if dry_run:
        if window_reached is not True:
            notes.append("dry-run 提醒：恢复窗口未到 / 解析不了，届时真写会被拒")
        if not readiness["retry_ready"]:
            notes.append("dry-run 提醒：重启收尸未完成（%d 条 active run / %d 个 task "
                         "未终态），11-09 重启后需重跑 --check 看逐条缺口"
                         % (len(readiness["active_runs"]),
                            len(readiness["resume_task_ids"])
                            - len(readiness["terminal_tasks"])))
    return {"target": target, "recommended": recommended, "allow": True,
            "gate": "allow", "blockers": [], "notes": notes,
            "target_inherit_verdict": "ok", "lost_chapters": [],
            "judged_run_count": judged, "retry_ready": readiness["retry_ready"]}


def judge_retry_post_write(state_before, state_after, target, new_run_id):
    """写后复核：只准目标 task 新增一条 active run，其余 3 本必须仍是 0。

    POST 已经发生、不能回滚；复核失败要显式报出来让人处理，别假装成功。
    """
    def active_by_task(state):
        out = {}
        for r in (state.get("runs") or []):
            if isinstance(r, dict) and r.get("status") in ACTIVE_RUN_STATUSES:
                tid = r.get("task_id")
                if tid in RESUME_TASK_IDS:
                    out[tid] = out.get(tid, 0) + 1
        return out

    before = active_by_task(state_before or {})
    after = active_by_task(state_after or {})
    others = sorted(tid for tid in RESUME_TASK_IDS
                    if tid != target and after.get(tid, 0) > before.get(tid, 0))
    target_delta = after.get(target, 0) - before.get(target, 0)
    problems = []
    if others:
        problems.append("其余 %d 本也新增了 active run（%s）：四本齐跑会抢见底池，"
                        "立刻去暂停" % (len(others), ",".join(others)))
    if target_delta > 1:
        problems.append("目标 task 新增 %d 条 active run（应恰好 1 条）" % target_delta)
    return {"ok": not problems, "problems": problems,
            "new_run_id": new_run_id, "active_before": before, "active_after": after}


# ---------------------------------------------------------------- 动作

def cmd_check(base):
    print("恢复窗口判据：%s" % judge_quota_window()["human"])
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
    # 只在重启后（4 个 task 全终态）才拉详情；额度见底期零额外 GET。
    reap = {"verdict": "unknown", "judged_count": 0, "batch_at": "",
            "batch_ids": [], "batch_size": 0,
            "blockers": ["收尸核对未执行"], "notes": [], "unknown_ids": []}
    if ready["retry_ready"]:
        reaped = [r for r in (state.get("runs") or [])
                  if isinstance(r, dict) and _run_reaped_by_restart(r)]
        details = {}
        for row in reaped:
            rid = str(row.get("id") or "")
            if not rid:
                continue
            try:
                details[rid] = api_get(base, "/api/runs/" + rid).get("run") or {}
            except Exception:
                details[rid] = {}   # 读不到交给 unknown 分支，不编造
        # 重启前那批 active run 的 id 在重启前后不变；09:48 批次观察项就是锚点。
        expected_ids = [str(r.get("id") or "") for r in paused]
        reap = judge_restart_reaping(state, details, expected_ids=expected_ids)
        print("重启收尸自洽性：%s（真被判过 %d 条锚点，本次批次 %s / %d 条）" % (
            reap["verdict"], reap["judged_count"],
            reap["batch_at"] or "(无)", reap["batch_size"]))
        for b in reap["blockers"]:
            print("  [缺口] %s" % b)
        for n in reap["notes"]:
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
    elif reap["verdict"] != "ok":
        conclusion = ("4 个 task 全终态，但重启收尸自洽性=%s（%d 条缺口）："
                      "先按 /api/runs/<id> 人工核对收尸范围，别开跑"
                      % (reap["verdict"], len(reap["blockers"])))
    else:
        conclusion = ("4 个定时任务已恢复、4 个 task 全终态、无 active run，"
                      "且重启收尸自洽性 ok（本次 %d 条）：可 "
                      "POST /api/tasks/<id>/retry；只先 retry 一本（4 个 task_id 见上），"
                      "别四本齐跑抢见底池，再 --verify-run <id> 看 blockers"
                      % reap["batch_size"])
    print("结论：%s" % conclusion)
    print("提示：挑本子先跑 --check-inherit（只读，报倒退章清单与真被判过样本数）；"
          "真要 retry 用 --retry-one（四道闸，不用手敲 curl）")
    return 0


def cmd_check_inherit(base):
    print("断点续跑继承判据（只读：只 GET /api/state + /api/runs/<id>，零 POST）")
    state = api_get(base, "/api/state")
    state_runs = state.get("runs") or []
    ids = [s["id"] for s in state_runs
           if isinstance(s, dict) and s.get("id")
           and s.get("task_id") in RESUME_TASK_IDS]
    details = {}
    for rid in ids:
        d = api_get(base, "/api/runs/" + rid)
        details[rid] = d.get("run") or d
    plan = plan_check_inherit(state_runs, details)
    print("真被判过的真实 run 样本数 %d/%d；待续跑 task %d/%d；"
          "历史出现过的章号合计 %d 章" % (
              plan["judged_run_count"], len(ids),
              plan["judged_task_count"], len(RESUME_TASK_IDS),
              plan["historical_chapter_count"]))
    print("派生重启收尸 run %d 条（11-09 才会出现，单列标注、不计真样本）；"
          "判不了的 task %d 个（unknown 不算判准）" % (
              plan["derived_reaped_count"], plan["unknown_task_count"]))
    for r in plan["results"]:
        label = {"ok": "一章不丢",
                 "regress": "会倒退 %d 章" % len(r["lost_chapters"]),
                 "unknown": "判不了"}[r["verdict"]]
        print("\n[%s] %s  最新终态 prev=%s(%s，模拟重启收尸后)" % (
            r["task_id"], label, r["prev_run_id"] or "无", r["prev_status"] or "无"))
        print("  retry 会继承 %s（steps %s ∪ inherit %s）" % (
            _fmt_chapters(r["done_chapters"]), _fmt_chapters(r["from_steps"]),
            _fmt_chapters(r["from_inherit"])))
        print("  历史出现过 %s；倒退章 %s" % (
            _fmt_chapters(r["historical_chapters"]),
            _fmt_chapters(r["lost_chapters"])))
        extra = r["extra_lost_without_restart"]
        print("  部署包未重启按旧代码只会继承 %s%s" % (
            _fmt_chapters(r["legacy_done_chapters"]),
            ("，多丢 %s" % _fmt_chapters(extra)) if extra else "（与修复后相同）"))
        for reason in r["reasons"]:
            print("  [判不了] %s" % reason)
    if plan["recommended_first"]:
        print("\n结论：先只 retry %s（ok 里继承最完整的一本）；其余 %d 本先核对"
              "磁盘成稿与历史章号，别四本齐跑。" % (
                  plan["recommended_first"],
                  sum(1 for r in plan["results"] if r["verdict"] != "ok")))
    else:
        print("\n结论：没有一本能判定「一章不丢」，先按上面 unknown / 倒退清单人工核对，别 retry")
    print("提醒：部署包 store.py 已同步但运行进程仍加载旧模块，必须先重启 8765"
          "（POST /api/selfupdate/restart）后修复才生效；重启后重跑 --check 确认可 retry。")
    return 0


def cmd_retry_one(base, task_id, confirm, dry_run, at_text=None):
    print("11-09 单本续跑护栏（只 retry 一本；真写前四道闸，任一不过零 POST）")
    # 闸 1/2 在第一次 GET 之前判，保证「拒写且零 IO」。
    window = judge_quota_window(at_text)
    pre = plan_retry_one({}, {}, task_id=task_id, confirm=confirm,
                         window_reached=window["reached"], dry_run=dry_run)
    if pre["gate"] in ("confirm", "window"):
        print("拒绝写：%s" % "；".join(pre["blockers"]))
        print("窗口判据：%s" % window["human"])
        return 2
    state = api_get(base, "/api/state")
    state_runs = state.get("runs") or []
    ids = [s["id"] for s in state_runs
           if isinstance(s, dict) and s.get("id")
           and s.get("task_id") in RESUME_TASK_IDS]
    details = {}
    for rid in ids:
        d = api_get(base, "/api/runs/" + rid)
        details[rid] = d.get("run") or d
    inherit = plan_check_inherit(state_runs, details)
    plan = plan_retry_one(state, inherit, task_id=task_id, confirm=confirm,
                          window_reached=window["reached"], dry_run=dry_run)
    print("真被判过的真实 run 样本数 %d/%d；判据推荐先 retry %s（不硬编码 id）" % (
        inherit["judged_run_count"], len(ids), inherit["recommended_first"] or "无"))
    print("恢复窗口：%s" % window["human"])
    readiness = judge_retry_readiness(state)
    print("retry 前置：%s（可 retry 的终态 task %d/4）" % (
        "满足" if readiness["retry_ready"] else "未满足",
        len(readiness["terminal_tasks"])))
    for b in readiness["blockers"]:
        print("  [前置] %s" % b)
    print("目标：%s（继承判据 %s）" % (plan["target"], plan["target_inherit_verdict"]))
    print("  POST /api/tasks/%s/retry%s" % (
        plan["target"], "（dry-run，不写）" if dry_run else ""))
    for n in plan["notes"]:
        print("  %s" % n)
    if not plan["allow"]:
        print("拒绝写：%s" % "；".join(plan["blockers"]))
        return 2
    if dry_run:
        print("dry-run 结束，生产未改动（真写要去掉 --dry-run 且四道闸全过）")
        return 0
    res = api_post(base, "/api/tasks/%s/retry" % plan["target"], {})
    new_run_id = res.get("run_id") or ""
    if res.get("ok") is False or not new_run_id:
        print("写入失败：%s" % json.dumps(res, ensure_ascii=False)[:200])
        return 1
    print("  [ok] 新 run %s" % new_run_id)
    after = api_get(base, "/api/state")
    chk = judge_retry_post_write(state, after, plan["target"], new_run_id)
    if not chk["ok"]:
        for p in chk["problems"]:
            print("复核失败：%s" % p)
        return 1
    print("复核通过：只 %s 新增 1 条 active run，其余 3 本未动；"
          "接着 --verify-run %s 看 verdict" % (plan["target"], new_run_id))
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


def cmd_resume_tasks(base, confirm, dry_run, at_text=None):
    if not confirm:
        print("拒绝：--resume-tasks 会写生产调度，必须显式带 --confirm-quota-restored，"
              "并先自行确认 %s 额度已恢复" % QUOTA_RECOVERY_AT)
        return 2
    window = judge_quota_window(at_text)
    if not dry_run and window["reached"] is not True:
        print("拒绝写：%s" % window["human"])
        print("（--confirm-quota-restored 只是人工确认，不能替代时间判据；"
              "--dry-run 可提前预演，上游窗口变更用 --recovery-at 覆盖）")
        return 2
    if dry_run and window["reached"] is not True:
        print("提示（dry-run 只预演、不写）：%s" % window["human"])
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
          "重跑 --check 确认可 retry → --retry-one --dry-run --confirm-quota-restored → "
          "去掉 --dry-run 才真写一本（续跑前 min_words 调 3000+）→ "
          "--verify-run <id> 看 verdict 与恢复判据")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="额度见底期 / 恢复日检查")
    ap.add_argument("--base", default=BASE, help="服务地址（隔离实例可覆盖，默认生产 8765）")
    ap.add_argument("--check", action="store_true", help="只读复核调度与暂停状态（默认）")
    ap.add_argument("--verify-run", metavar="RUN_ID", help="只读判一条 run 的 verdict")
    ap.add_argument("--check-inherit", action="store_true",
                    help="只读复现 retry 会继承的章号，报倒退章清单")
    ap.add_argument("--resume-tasks", action="store_true", help="开回 4 个定时任务（写操作）")
    ap.add_argument("--retry-one", action="store_true",
                    help="只 retry 一本（写操作，四道闸：确认/窗口/收尸/不倒退）")
    ap.add_argument("--task-id", help="--retry-one 的目标 task；不填按 --check-inherit 推荐")
    ap.add_argument("--dry-run", action="store_true",
                    help="与 --resume-tasks / --retry-one 合用，只打印不写")
    ap.add_argument("--confirm-quota-restored", action="store_true",
                    help="人工确认额度已恢复；缺这个标志拒绝任何写操作")
    ap.add_argument("--recovery-at", default=QUOTA_RECOVERY_AT,
                    help="覆盖上游恢复时刻（默认 2026-11-09 14:09）；支持 YYYY-MM-DD HH:MM 或 [1310] 原话")
    args = ap.parse_args(argv)
    if args.retry_one:
        return cmd_retry_one(args.base, args.task_id, args.confirm_quota_restored,
                             args.dry_run, args.recovery_at)
    if args.resume_tasks:
        return cmd_resume_tasks(args.base, args.confirm_quota_restored,
                                args.dry_run, args.recovery_at)
    if args.verify_run:
        return cmd_verify_run(args.base, args.verify_run)
    if args.check_inherit:
        return cmd_check_inherit(args.base)
    return cmd_check(args.base)


if __name__ == "__main__":
    sys.exit(main())
