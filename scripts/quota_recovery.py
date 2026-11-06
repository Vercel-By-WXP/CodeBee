# -*- coding: utf-8 -*-
"""额度见底期 → 恢复日（2026-11-09 14:09）的检查与恢复脚本。

把交接单第 3 节第 3 条那段 curl 变成**可校验**的动作，同时把 14:39 定下的
「续跑验收看 verdict 三件套」代码化，避免 11-09 靠人眼比对字段。

    py -3.13 scripts/quota_recovery.py --check                      # 只读，随时可跑
    py -3.13 scripts/quota_recovery.py --verify-run <run_id>       # 只读，出人话结论
    py -3.13 scripts/quota_recovery.py --resume-tasks --dry-run    # 只看会做什么
    py -3.13 scripts/quota_recovery.py --resume-tasks --confirm-quota-restored

判据的四个字段陷阱（都是 2026-11-06 15:00 从生产详情量出来的，别再踩）：

1. `/api/state` 的 `verdict` 是 `_state_run_summary` 的**压缩白名单**，两条真实
   run 只带 `{"publishable": False}`；判三件套必须走 `/api/runs/<id>` 详情。
2. 429 写在 step 的 `summary` / `note`（`error` 常为空）；而且 request id 里
   会巧合出现 "429" → 只认 `429 Too Many Requests` / `last status: 429`。
3. `completed_seats` 在 `verdict.review_resources.completed_seats`，不是顶层；
   `review_fallbacks` 在真实 run 里**根本没有这个键**。`local-heuristic` 是
   `app/core/local_fallback.py` 的常量、只出现在守测期望值里，不是落盘基线。
4. 真调模型成功步按 `cost_usd > 0` 数：断点续跑复用成稿是 `cost=0/model=''`。

脚本**不替人判断额度是否恢复**：`--resume-tasks` 必须显式带
`--confirm-quota-restored`，否则拒绝任何写操作。
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
RATE_LIMIT_RE = re.compile(r"429\s+Too\s+Many\s+Requests|last\s+status:\s*429", re.I)
_RL_FIELDS = ("summary", "error", "output", "note")


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


def judge_run(run):
    """输入 `/api/runs/<id>` 的 `run` 对象，输出可比对的结构化结论。"""
    verdict = run.get("verdict") or {}
    if not isinstance(verdict, dict):
        verdict = {}
    resources = verdict.get("review_resources") or {}
    if not isinstance(resources, dict):
        resources = {}
    steps = [s for s in (run.get("steps") or []) if isinstance(s, dict)]

    # 三件套字段是否齐全：state 的压缩版只有 publishable，不能拿它判质量。
    fields_complete = all(k in verdict for k in
                          ("publishable", "review_independence", "overall")) and bool(resources)
    rate_limited = [s.get("n") for s in steps if _step_rate_limited(s)]
    model_steps = sum(1 for s in steps if (s.get("cost_usd") or 0) > 0)
    seats = resources.get("completed_seats")
    independence = verdict.get("review_independence")
    publishable = verdict.get("publishable")

    honest = bool(fields_complete and publishable is False
                  and independence == "no-review" and seats == 0)
    return {
        "run_id": run.get("id"),
        "status": run.get("status"),
        "fields_complete": fields_complete,
        "publishable": publishable,
        "overall": verdict.get("overall"),
        "threshold": verdict.get("threshold"),
        "review_independence": independence,
        "completed_seats": seats,
        "required_seats": resources.get("required_seats"),
        # 真实 run 里通常没有这个键，缺省就是空列表；不要凭空补 local-heuristic。
        "review_fallbacks": verdict.get("review_fallbacks") or [],
        "successful_model_steps": model_steps,
        "rate_limited_steps": rate_limited,
        "last_step_rate_limited": bool(steps) and _step_rate_limited(steps[-1]),
        "verdict_honest": honest,
    }


def human_verdict(d):
    if not d["fields_complete"]:
        return ("字段不足：这是 /api/state 的压缩 verdict（白名单只留 publishable 等 6 键），"
                "判三件套请改用 --verify-run 走 /api/runs/<id> 详情")
    bits = []
    if d["publishable"] is True:
        bits.append("可发布")
    else:
        reason = ("无真实评审席位（%s / %s 席）" %
                  (d["review_independence"], d["completed_seats"])
                  if d["review_independence"] == "no-review" or d["completed_seats"] == 0
                  else "评分 %s < 阈值 %s" % (d["overall"], d["threshold"]))
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
        bits.append("无限流步骤")
    return "；".join(bits)


# ---------------------------------------------------------------- 动作

def cmd_check(base):
    print("额度恢复参考时刻：%s（脚本不替你确认额度，恢复前别带 --confirm-quota-restored）"
          % QUOTA_RECOVERY_AT)
    tasks = api_get(base, "/api/automation").get("tasks") or []
    by_id = {t.get("id"): t for t in tasks}
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
    state = api_get(base, "/api/state")
    pause = state.get("pause_control") or {}
    print("pause_control: %s" % json.dumps(pause, ensure_ascii=False))
    paused = [r for r in (state.get("runs") or [])
              if str(r.get("id") or "").startswith("r-20261106-0948")]
    print("09:48 批次 run %d 条：%s" % (
        len(paused), ", ".join("%s(%s/%s)" % (r.get("id"), r.get("status"),
                                              r.get("pause_phase")) for r in paused)))
    print("结论：%s" % ("定时任务已恢复、可进入重启 / 串行续跑"
                     if enabled == len(TASK_IDS)
                     else "仍处额度见底期抑制态（启用 %d/%d），不要开跑" %
                     (enabled, len(TASK_IDS))))
    return 0


def cmd_verify_run(base, run_id):
    detail = api_get(base, "/api/runs/" + run_id)
    run = detail.get("run") or detail
    d = judge_run(run)
    print("%s  status=%s" % (run_id, d["status"]))
    for key in ("publishable", "overall", "threshold", "review_independence",
                "completed_seats", "required_seats", "review_fallbacks",
                "successful_model_steps", "rate_limited_steps",
                "last_step_rate_limited", "verdict_honest"):
        print("  %-24s %s" % (key, d[key]))
    print("结论：" + human_verdict(d))
    return 0


def cmd_resume_tasks(base, confirm, dry_run):
    if not confirm:
        print("拒绝：--resume-tasks 会写生产调度，必须显式带 --confirm-quota-restored，"
              "并先自行确认 %s 额度已恢复" % QUOTA_RECOVERY_AT)
        return 2
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
          "--verify-run <id> 看 verdict 三件套")
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
