# -*- coding: utf-8 -*-
"""任务队列：单一 worker 线程顺序执行编排任务与管理操作（安装/升级/冒烟）。

安装/升级失败时自动触发 AI 诊断修复：由真实智能体读取失败日志与本机环境，
给出修正命令；仅当命令命中白名单前缀（npm/winget/pip 安装类）才自动执行，
否则把建议命令记录在运行记录里等人工确认。
"""
from __future__ import annotations

import queue
import threading
import traceback

_QUEUE = queue.Queue()
CANCELS = {}
_started = False

AI_REPAIR_PROMPT = """你是环境工程师。在 Windows 上执行下面的安装命令失败了，请诊断原因并给出修正命令。
只输出一个 ```json 代码块，不要输出其他内容。JSON 结构：
{"diagnosis": "失败原因（一句话）", "command": "修正后的完整安装命令", "safe": true/false}
硬性约束：command 只能是本机包管理器的安装命令，前缀必须是 npm install / winget install /
py -3.13 -m pip install 之一。给不出符合约束的安全命令时，safe 设为 false 且 command 留空。

## 失败的命令
__CMD__

## 失败输出（尾部）
__LOG__

## 本机环境
__ENV__"""

# AI 修复命令白名单：只放行包管理器的安装类命令
AI_REPAIR_ALLOW = ("npm install ", "winget install", "py -3.13 -m pip install")


def _repair_command_allowed(cmd):
    cmd = (cmd or "").strip()
    return cmd.startswith(AI_REPAIR_ALLOW) and "|" not in cmd and "&" not in cmd and ">" not in cmd


def start_worker():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_worker, name="job-worker", daemon=True).start()


def enqueue(job):
    _QUEUE.put(job)


def cancel(run_id):
    ev = CANCELS.get(run_id)
    if ev:
        ev.set()
        return True
    return False


def cancel_event_for(run_id):
    ev = threading.Event()
    CANCELS[run_id] = ev
    return ev


def _worker():
    while True:
        job = _QUEUE.get()
        run_id = job.get("run_id")
        ev = cancel_event_for(run_id) if run_id else threading.Event()
        try:
            if job.get("kind") == "orchestration":
                from . import pipeline
                pipeline.execute_run(run_id)
            elif job.get("kind") == "mgmt":
                _do_mgmt(job, ev)
        except Exception:
            try:
                from . import store
                err = traceback.format_exc()
                store.update_run(run_id, status="failed", error=err[-1500:], ended_at=_now())
            except Exception:
                pass
        finally:
            if run_id:
                CANCELS.pop(run_id, None)
            _QUEUE.task_done()


def _now():
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _do_mgmt(job, ev):
    from . import catalog, manager, store
    run_id = job["run_id"]
    entry = catalog.by_id(job.get("entry_id"))
    op = job.get("op") or ""
    store.update_run(run_id, status="running", started_at=_now())
    if entry is None:
        store.update_run(run_id, status="failed", error="catalog 中找不到 %s" % job.get("entry_id"),
                         ended_at=_now())
        return
    step, log_abs = store.add_step(run_id, op or "mgmt", entry["id"], entry.get("name", entry["id"]))
    ok = False
    if op in ("install", "upgrade"):
        res = manager.run_mgmt_command(entry, op, cancel_event=ev, log_path=str(log_abs))
        ok = res["ok"]
        store.finish_step(run_id, step["n"],
                          "done" if ok else "failed",
                          summary=("完成" if ok else "失败") + (": " + res["error"][:300] if res.get("error") else ""),
                          exit_code=res.get("exit_code"))
        if not ok:
            ok = _ai_repair(run_id, entry, ev, entry.get(op), log_abs)
    elif op == "smoke":
        from . import runner as _r
        from . import registry
        agents = registry.effective_agents(catalog.load(), manager.detect_all())
        agent = next((a for a in agents if a["id"] == entry["id"]), None)
        if agent is None:
            store.finish_step(run_id, step["n"], "failed", summary="该智能体未安装或未启用编排")
            store.update_run(run_id, status="failed", error="未启用", ended_at=_now())
            return
        res = _r.run_agent(agent, "连通性测试：请只回复两个字：OK", readonly=True,
                           timeout=180, cancel_event=ev, log_path=str(log_abs))
        ok = res["ok"] and "OK" in (res.get("text") or "").upper()
        store.finish_step(run_id, step["n"], "done" if ok else "failed",
                          summary=("连通正常：%s" % (res.get("text") or "")[:120]) if ok
                          else ("异常：%s" % (res.get("error") or (res.get("text") or "")[:120])),
                          exit_code=res["raw"].get("exit_code"),
                          cost_usd=res.get("cost_usd", 0.0), tokens=res.get("tokens", 0))
    else:
        store.finish_step(run_id, step["n"], "failed", summary="未知操作 %s" % op)
    # 以步骤状态汇总 run 状态
    run = store.get_run(run_id)
    statuses = [s["status"] for s in (run.get("steps") if run else [])] or ["failed"]
    final = "done" if all(s == "done" for s in statuses) else "failed"
    suffix = "（AI 修复成功）" if ok and len((run.get("steps") if run else [])) > 1 else ""
    store.update_run(run_id, status=final, ended_at=_now(),
                     summary=("%s %s %s%s" % (entry.get("name"), op,
                                              "完成" if final == "done" else "失败", suffix)))


def _ai_repair(run_id, entry, ev, failed_cmd, orig_log):
    """安装失败后的 AI 诊断修复：诊断 → 白名单校验 → 执行 → 复检。"""
    from . import catalog, manager, registry, router, runner, store
    agents = registry.effective_agents(catalog.load(), manager.detect_all())
    agent, _reason = router.pick(agents, "repair", "mgmt")
    if agent is None or agent.get("mode") != "real":
        return False  # 无真实智能体可用，维持原失败
    try:
        log_tail = runner.decode_output(orig_log.read_bytes()[-2000:]) if orig_log.exists() else "（无输出）"
    except Exception:
        log_tail = "（日志不可读）"
    import shutil
    env_lines = [
        "OS: Windows",
        "node: %s" % (shutil.which("node") or "缺失"),
        "npm: %s" % (shutil.which("npm") or "缺失"),
        "pnpm: %s" % (shutil.which("pnpm") or "缺失"),
        "python: %s" % (shutil.which("python") or "缺失"),
    ]
    prompt = (AI_REPAIR_PROMPT.replace("__CMD__", failed_cmd or "（未知）")
              .replace("__LOG__", log_tail)
              .replace("__ENV__", "\n".join(env_lines)))
    step, log_abs = store.add_step(run_id, "ai-repair", agent["id"], agent.get("label"),
                                   note="自动诊断修复")
    res = runner.run_agent(agent, prompt, readonly=True, timeout=300,
                           cancel_event=ev, log_path=str(log_abs))
    if not res["ok"]:
        store.finish_step(run_id, step["n"], "failed",
                          summary="诊断调用失败：%s" % (res.get("error") or "")[:200])
        return False
    import re
    data = runner.extract_json(res.get("text") or "")
    diagnosis = str((data or {}).get("diagnosis") or "（无诊断）")[:200]
    cmd = str((data or {}).get("command") or "").strip()
    safe = bool((data or {}).get("safe")) and _repair_command_allowed(cmd)
    try:
        log_abs.write_text(
            ("\n[AI 诊断] %s\n[AI 建议] %s\n[白名单] %s\n" %
             (diagnosis, cmd or "（无）", "通过" if safe else "不通过，拒绝自动执行")).encode("utf-8"))
    except Exception:
        pass
    if not safe:
        store.finish_step(run_id, step["n"], "failed",
                          summary="AI 建议命令未过白名单，需人工执行：%s（诊断：%s）" % (cmd, diagnosis))
        return False
    from . import paths
    fix = runner.run_process(shell_cmd=cmd, cwd=str(paths.ROOT), timeout=1800,
                             cancel_event=ev, log_path=str(log_abs))
    manager.detect_all(force=True)
    with manager._LOCK:
        manager._STATE["versions"].pop(entry["id"], None)
    installed = manager.detect_entry(entry)["installed"]
    store.finish_step(run_id, step["n"],
                      "done" if (fix["ok"] and installed) else "failed",
                      summary="诊断：%s → 执行 %r：%s，检测安装状态：%s" % (
                          diagnosis, cmd,
                          "命令成功" if fix["ok"] else "命令失败",
                          "已安装" if installed else "未检出"),
                      exit_code=fix.get("exit_code"))
    return bool(fix["ok"] and installed)
