# -*- coding: utf-8 -*-
"""任务队列：可并发 worker 池（默认 3，1-6 可配）执行编排任务与管理操作。

多个任务同时跑、互不打扰：每个 job 一条独立线程，run/step 数据按 run_id
隔离，store 层有全局锁。目标并发数可在设置页调整；调小后多余线程在取到
新任务前自行退出，调大即时补齐。安装/升级失败时自动触发 AI 诊断修复：
由真实智能体读取失败日志与本机环境给出修正命令；仅当命令命中白名单前缀
（npm/winget/pip 安装类）才自动执行，否则把建议命令记录在运行记录里等人工确认。
"""
from __future__ import annotations

import queue
import threading
import traceback

_QUEUE = queue.Queue()
CANCELS = {}
_started = False
_alive = 0            # 活跃 worker 线程数
_target = 3           # 目标并发数（settings.max_concurrent_jobs）
_pool_lock = threading.Lock()
_seq = 0
MAX_POOL = 12         # 并发上限：每个 job 只是拉起 CLI 子进程，跨任务并行无共享
                      # 资源（同任务单飞另有守卫），照竞品（emdash/munder-difflin
                      # 高并行环境）放开到 12
WATCHDOG_INTERVAL_S = 60   # 队列看门狗巡检周期
WATCHDOG_STALE_S = 120     # queued 超过该秒数视为掉队（正常入队到被拿起 ≤5s）
_watchdog_started = False

AI_REPAIR_PROMPT = """你是环境工程师。在 Windows 上执行下面的安装命令失败了，请诊断原因并给出修正命令。
只输出一个 ```json 代码块，不要输出其他内容。JSON 结构：
{"diagnosis": "失败原因（一句话）", "command": "修正后的完整安装命令", "safe": true/false}
硬性约束：command 只能是本机包管理器的安装命令，前缀必须是 __ALLOW__ 之一。
给不出符合约束的安全命令时，safe 设为 false 且 command 留空。

## 失败的命令
__CMD__

## 失败输出（尾部）
__LOG__

## 本机环境
__ENV__"""

# AI 修复命令白名单：只放行包管理器的安装类命令（提示词里的前缀清单由它生成，
# 两处永远不会漂移）
AI_REPAIR_ALLOW = ("npm install ", "winget install", "py -3.13 -m pip install",
                   "uv tool install")


def _repair_command_allowed(cmd):
    cmd = (cmd or "").strip()
    return cmd.startswith(AI_REPAIR_ALLOW) and "|" not in cmd and "&" not in cmd and ">" not in cmd


def configure(max_workers):
    """设置目标并发数（1-12）：扩容立即补线程，缩容由空闲线程自行退出。"""
    global _target
    _target = max(1, min(MAX_POOL, int(max_workers)))
    if _started:
        _resize()
    return _target


def _resize():
    global _seq
    with _pool_lock:
        # 上限 50 次尝试：Thread.start() 返回到 _worker 真正执行之间有调度间隙，
        # 极端环境下（杀软挂起新线程）_alive 迟迟不涨，无界循环会转着圈造线程。
        attempts = 0
        while _alive < _target and attempts < 50:
            attempts += 1
            _seq += 1
            try:
                threading.Thread(target=_worker, name="job-worker-%d" % _seq,
                                 daemon=True).start()
            except RuntimeError:
                break  # 资源受限起不了新线程：保持现有 worker，不影响任务执行


def start_worker():
    """标记队列可用并加载并发配置。worker 线程**不在启动期创建**（真实装机
    案例：某些杀软环境下启动期 Thread.start() 挂死，进程停在任务队列一步），
    推迟到首次 enqueue 时由 _ensure_workers 创建——服务就绪不再依赖线程。"""
    global _started
    if _started:
        return
    _started = True
    try:
        from . import settings
        configure(settings.load()["max_concurrent_jobs"])
        return
    except Exception:
        pass
    configure(3)


def enqueue(job):
    if not _started:
        start_worker()
    _ensure_workers()
    _QUEUE.put(job)


def _ensure_workers():
    """队列里积压超过空闲 worker 数时补线程（惰性扩容，替代启动期预建）。
    看门狗线程同样惰性起：与 worker 一样不在启动期 Thread.start()（杀软
    挂起新线程的真实装机案例，见 start_worker 注释），首队到达时一起补。"""
    global _watchdog_started
    with _pool_lock:
        pending = _QUEUE.qsize()
        need = max(_target, 1) - _alive + pending
        if not _watchdog_started:
            _watchdog_started = True
            threading.Thread(target=_watchdog, name="job-watchdog",
                             daemon=True).start()
    if need > 0:
        _resize()


def _watchdog():
    """队列看门狗：周期把卡死的 queued 编排运行补回队列。

    job 队列在内存里，任何一次入队丢失（2026-09-18 实案：r-20260918-211920
    排队 1 小时无人接手、进程未重启则启动补队永远不跑）都会让 UI 永远
    「排队中」。这里每分钟自愈一次；重复入队由 worker 出队守卫（非 queued
    跳过）与同任务单飞守卫兜底，幂等。巡检自身异常绝不退出。"""
    import time as _t
    while True:
        _t.sleep(WATCHDOG_INTERVAL_S)
        try:
            requeue_pending(limit=6, max_age_s=WATCHDOG_STALE_S)
        except Exception:
            pass


def cancel(run_id):
    ev = CANCELS.get(run_id)
    if ev:
        ev.set()
        return True
    # 事件不存在=任务还在队列里没被 worker 拿起：直接落终态（取消事件在
    # worker 起跑时才创建，排队任务点取消会在这里漏掉——起跑后再杀一遍）。
    try:
        from . import store
        run = store.get_run(run_id)
        if not run:
            return False
        if run.get("status") == "queued":
            store.update_run(run_id, expected_status="queued", status="cancelled",
                             ended_at=_now())
            return True
        if run.get("status") == "running" and run.get("cancelled_by_user"):
            return True   # 上一轮取消已标记，等起跑时的兜底检查收口
    except Exception:
        pass
    return False


def cancel_event_for(run_id):
    ev = CANCELS.get(run_id)   # get-or-create：排队期置位的取消不因重建事件而丢失
    if ev is None:
        ev = threading.Event()
        CANCELS[run_id] = ev
    return ev


AUTO_RESUME_MAX = 3      # 连载任务自动续跑上限（超时/中断后自动接着写，无需人工）
AUTO_RESUME_DELAY_S = 300  # 自动续跑延迟入队秒数：网关限流/欠费窗口通常分钟级，
                           # 立即重排会撞在同一堵墙上把续跑次数烧光（2026-09-17 七猫实测）


def _task_active_run(task_id, exclude_run_id=None):
    """该任务当前 queued/running 的运行（同任务单飞守卫用）；无则 None。"""
    try:
        from . import store
        for r in store.task_runs(task_id):
            if r.get("id") == exclude_run_id:
                continue
            if r.get("status") in ("queued", "running"):
                return r
    except Exception:
        pass
    return None


def _err_signature(err):
    """错误串的稳定特征：剥掉章号/引用号/数字等易变片段，只留病根文本。
    同因连撞判定用——退避重跑后错误若还长得一样，说明墙没动（欠费/配置死），
    再多次退避也只是重复付费。"""
    import re
    s = str(err or "")
    s = re.sub(r"第\s*\d+\s*章", "第N章", s)
    s = re.sub(r"err_[0-9a-fA-F]+", "err_X", s)
    s = re.sub(r"\d+", "N", s)
    return re.sub(r"\s+", " ", s).strip()[:400]


def _maybe_auto_resume(run_id):
    """连载任务失败自动续跑：继承已完成章继续，最多 AUTO_RESUME_MAX 次。

    真实长篇单次运行常因供应商拥堵超时中断；这里在 worker 收尾时自动重排一次
    续跑（store.retry_task 会带上 inherit），让整个流程真正无人值守。
    同因连撞止损：续跑副本再失败时与本次失败的错误签名比对（2026-09-18
    重写任务 kimi 403 欠费案），一模一样说明退避没换来不同结果，直接落
    终态写明死因，不再烧剩余的退避次数。
    """
    try:
        from . import store
        run = store.get_run(run_id)
        if not run or run.get("status") not in ("failed", "cancelled"):
            return False
        task = store.get_task(run.get("task_id")) if run.get("task_id") else None
        if not task or not task.get("serial"):
            return False
        if run.get("cancelled_by_user") or run.get("status") == "cancelled":
            return False   # 用户主动取消的运行绝不自动续跑
        if int(run.get("auto_resumes") or 0) >= AUTO_RESUME_MAX:
            return False
        prev_id = run.get("auto_resumed_from")
        if prev_id:
            prev = store.get_run(prev_id)
            sig_now = _err_signature(run.get("error"))
            if (prev and sig_now and prev.get("error")
                    and sig_now == _err_signature(prev.get("error"))):
                store.update_run(run_id, auto_resume_stopped="same_cause",
                                 error=(run.get("error") or "")
                                 + "｜自动续跑止损：连续两次失败原因相同，不再重试")
                return False
        if _task_active_run(task["id"], exclude_run_id=run_id):
            return False   # 同任务已有运行排队/在跑：再排副本只会与之撞车
        ok, err, new_run = store.retry_task(task["id"])
        if not ok or not new_run:
            return False
        # 退避窗口要让用户看得见：把「预定入队时刻」写到 run 上，前端据此显示
        # 「将在 HH:MM 自动续跑」而不是笼统的排队中（run 在建好到入队之间会
        # 以 queued 状态干等 AUTO_RESUME_DELAY_S 秒）。
        import time as _t
        resume_at = _t.strftime("%Y-%m-%d %H:%M:%S",
                                _t.localtime(_t.time() + AUTO_RESUME_DELAY_S))
        store.update_run(new_run["id"], auto_resumes=int(run.get("auto_resumes") or 0) + 1,
                         auto_resumed_from=run_id, resume_enqueue_at=resume_at)

        def _enqueue():
            _QUEUE.put({"kind": "orchestration",
                        "run_id": new_run["id"], "task_id": task["id"]})
        t = threading.Timer(AUTO_RESUME_DELAY_S, _enqueue)
        t.daemon = True
        t.start()
        return True
    except Exception:
        return False


RESUME_WINDOW_HOURS = 24   # 启动恢复只看最近 24h 内中断的运行（更早的视为已放弃）


def _recent(run):
    """运行创建时间是否在恢复窗口内（时间格式 %Y-%m-%d %H:%M:%S）。"""
    import time as _t
    try:
        ts = _t.mktime(_t.strptime(run.get("created_at") or "", "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return False
    return (_t.time() - ts) <= RESUME_WINDOW_HOURS * 3600


def resume_interrupted(limit=3):
    """启动恢复：把**近期**中断的连载任务重新入队（继承已完成章）。返回恢复条数。

    服务被外部杀掉/崩溃时 worker 的 finally 不会执行；这里在启动时补一次。
    只在 RESUME_WINDOW_HOURS 窗口内、且该任务没有更新的终态运行时恢复——
    避免复活用户早已放弃或已完成任务的旧运行。用户手动取消的一律跳过。
    """
    try:
        from . import store
    except Exception:
        return 0
    runs = store.list_runs(200)
    latest_by_task = {}
    for r in runs:   # list_runs 已按 id 倒序：首次出现即该 task 最新运行
        tid = r.get("task_id")
        if tid and tid not in latest_by_task:
            latest_by_task[tid] = r["id"]
    n = 0
    try:
        for run in sorted(runs, key=lambda r: r["id"]):
            if n >= limit:
                break
            if run.get("status") != "failed" or not run.get("task_id"):
                continue
            if run.get("cancelled_by_user"):
                continue
            if run.get("paused"):
                continue   # 用户主动暂停的运行（重启收尸后 paused 标志保留）不自动续跑——
                           # 「继续」由用户点「继续任务」决定，不替用户做主
            if not _recent(run):
                continue
            if latest_by_task.get(run["task_id"]) != run["id"]:
                continue   # 该任务已有更新的运行（如用户重试/已完结），不复活旧中断
            task = store.get_task(run["task_id"])
            if not task or not task.get("serial"):
                continue
            if int(run.get("auto_resumes") or 0) >= AUTO_RESUME_MAX:
                continue
            ok, err, new_run = store.retry_task(task["id"])
            if not ok or not new_run:
                continue
            store.update_run(new_run["id"],
                             auto_resumes=int(run.get("auto_resumes") or 0) + 1,
                             auto_resumed_from=run["id"])
            _QUEUE.put({"kind": "orchestration", "run_id": new_run["id"], "task_id": task["id"]})
            n += 1
    except Exception:
        return n
    return n


def _yield_duplicate(run_id):
    """同任务单飞（worker 出队时把关）：系统续跑副本出队时若同任务已有
    运行排队/在跑，取消自己让位——恢复副本与原轮并行跑只会双烧评审。
    用户轮不在此拦（retry_task 建轮时已拒运行中任务）。返回 True 表示
    本运行已落 cancelled，不要执行。"""
    try:
        from . import store
        r0 = store.get_run(run_id)
        if not r0 or not r0.get("auto_resumed_from"):
            return False
        other = _task_active_run(r0.get("task_id"), exclude_run_id=run_id)
        if not other or other.get("kind") != "orchestration":
            return False
        store.update_run(run_id, status="cancelled", ended_at=_now(),
                         error="同任务已有运行在跑（%s），续跑副本自动让位" % other.get("id"))
        return True
    except Exception:
        return False


def _age_s(run, now=None):
    """run 已创建多少秒（created_at 解析失败返回 0：宁可早补，别误判卡死）。"""
    import time as _t
    now = now if now is not None else _t.time()
    try:
        ts = _t.mktime(_t.strptime(run.get("created_at") or "", "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0
    return max(0.0, now - ts)


def _in_resume_backoff(run, now=None):
    """续跑副本是否还在退避等待窗口内（resume_enqueue_at 未到点）。

    到点时间由 _maybe_auto_resume 落在 run 上，Timer 到点才入队；巡检/补队
    若无视它直接重排，等于把 300s 网关退避窗口烧掉（立即重排撞同一堵墙）。"""
    import time as _t
    now = now if now is not None else _t.time()
    at = str(run.get("resume_enqueue_at") or "")
    if not at:
        return False
    try:
        return _t.mktime(_t.strptime(at, "%Y-%m-%d %H:%M:%S")) > now
    except Exception:
        return False   # 解析失败按「不在退避」处理：宁可入队，别让副本永远卡死


def requeue_pending(limit=10, max_age_s=None):
    """把遗留的 queued 编排运行重新入队（启动补队与运行期巡检共用）。

    队列在内存里，进程一死排队项就没人管了（2026-09-18 七猫 r-162724
    排队僵尸案：续跑副本 created 后服务重启，Timer 随进程蒸发，运行永远
    停在「排队中」）。同任务已有在跑/排队的不重复补。

    max_age_s：巡检模式只补「卡了超过该秒数」的，刚入队的正常排队不掺和；
    None（启动模式）全量补。resume_enqueue_at 未到点的续跑副本两种模式都
    跳过——重启不该把退避窗口烧掉。返回补队条数。"""
    try:
        from . import store
    except Exception:
        return 0
    n = 0
    try:
        for run in store.list_runs(200):
            if n >= limit:
                break
            if run.get("status") != "queued" or run.get("kind") != "orchestration":
                continue
            if not run.get("task_id") or not _recent(run):
                continue
            if _in_resume_backoff(run):
                continue   # 退避窗口内的续跑副本：到点 Timer 自会入队
            if max_age_s is not None and _age_s(run) < max_age_s:
                continue
            if _task_active_run(run["task_id"], exclude_run_id=run["id"]):
                continue   # 同任务已有更活跃的运行，别再排一份
            _QUEUE.put({"kind": "orchestration", "run_id": run["id"],
                        "task_id": run["task_id"]})
            n += 1
    except Exception:
        pass
    return n


def _worker():
    global _alive
    with _pool_lock:
        _alive += 1
    try:
        while True:
            with _pool_lock:
                if _alive > _target:   # 缩容：多余的线程在空闲检查点自行退出
                    return
            try:
                job = _QUEUE.get(timeout=5)  # 定期醒来检查并发数是否被调小
            except queue.Empty:
                continue
            run_id = job.get("run_id")
            ev = cancel_event_for(run_id) if run_id else threading.Event()
            try:
                # 出队后状态闸：非 queued 一律跳过。排队期取消的（cancel 已
                # 直接落终态）不再进流水线；running/done/failed 的是看门狗
                # 重排/双入队产生的重复副本——另一 worker 已在跑或已跑完，
                # 再 execute_run 会把同一运行执行两次。查不到的 run（测试
                # mock）不拦，保持原行为。
                if run_id:
                    from . import store
                    r0 = store.get_run(run_id)
                    if r0 and r0.get("status") != "queued":
                        continue   # task_done 由 finally 统一收口，不能在此重复
                    if job.get("kind") == "orchestration" and _yield_duplicate(run_id):
                        continue   # 同任务单飞：续跑副本让位（已落 cancelled）
                if job.get("kind") == "orchestration":
                    from . import pipeline
                    pipeline.execute_run(run_id)
                elif job.get("kind") == "mgmt":
                    _do_mgmt(job, ev)
                elif job.get("kind") == "selfupgrade":
                    _do_selfupgrade(job)
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
                    try:
                        _maybe_auto_resume(run_id)   # 连载失败自动续跑（继承已完成章）
                    except Exception:
                        pass
                _QUEUE.task_done()
    finally:
        with _pool_lock:
            _alive -= 1


def workers_info():
    with _pool_lock:
        return {"target": _target, "alive": _alive, "queued": _QUEUE.qsize()}


def _drain_test_queue():
    """测试辅助：清空内存队列并结清未决 join（生产代码勿调）。"""
    while True:
        try:
            _QUEUE.get_nowait()
            _QUEUE.task_done()
        except Exception:
            return


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
    if op in ("install", "upgrade", "uninstall"):
        res = manager.run_mgmt_command(entry, op, cancel_event=ev, log_path=str(log_abs))
        ok = res["ok"]
        # 文件占用类失败（Windows 文件锁 EBUSY/EPERM）给人话结论并跳过 AI 修复：
        # 修复智能体面对文件锁只会给出 taskkill 全杀 node 之类白名单必拒的危险
        # 命令，白白烧一轮 300s 诊断（2026-09-18 dsh 同版本重装 EBUSY 案）
        lock_hit = (not ok and op in ("install", "upgrade") and _file_lock_error(res))
        if lock_hit:
            summary = ("失败：安装文件被占用（可能有同名程序在运行，或杀毒软件正在扫描），"
                       "请关闭占用该文件的程序后重试。完整输出见日志。")
        else:
            summary = ("完成" if ok else "失败") + (": " + res["error"][:300] if res.get("error") else "")
        store.finish_step(run_id, step["n"],
                          "done" if ok else "failed",
                          summary=summary,
                          exit_code=res.get("exit_code"))
        # AI 修复只针对安装类失败；卸载失败多为权限/程序占用，留给用户看日志处理
        if not ok and op in ("install", "upgrade") and not lock_hit:
            ok = _ai_repair(run_id, entry, ev, entry.get(op), log_abs)
    elif op == "smoke":
        from . import runner as _r
        from . import registry
        from . import usage as _usage
        agents = registry.effective_agents(catalog.load(), manager.detect_all())
        agent = next((a for a in agents if a["id"] == entry["id"]), None)
        if agent is None:
            store.finish_step(run_id, step["n"], "failed", summary="该智能体未安装或未启用编排")
            store.update_run(run_id, status="failed", error="未启用", ended_at=_now())
            return
        res = _r.run_agent(agent, "连通性测试：请只回复两个字：OK",
                           readonly=True,
                           # 300s：codex CLI 启动要拉 5 个 MCP 服务器 + 注入约 13 万
                           # token 技能上下文，首 token 常超 180s（2026-09-17 实测
                           # 网关裸探 8s 就回，慢在 CLI 自身启动与上下文）。
                           timeout=300, cancel_event=ev, log_path=str(log_abs))
        ok = res["ok"] and "OK" in (res.get("text") or "").upper()
        try:
            _usage.record(source="smoke", run_id=run_id, step=step["n"], role="smoke",
                          agent=agent.get("id", ""), agent_label=agent.get("label", ""),
                          tool=agent.get("kind", ""), model=res.get("model") or "",
                          ok=bool(res.get("ok")),
                          duration_s=float((res.get("raw") or {}).get("duration") or 0.0),
                          cost_usd=float(res.get("cost_usd") or 0.0),
                          usage=res.get("usage"))
        except Exception:
            pass
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


def _do_selfupgrade(job):
    """CodeBee 自升级：在 mgmt run 里跑 npm install -g @latest，日志实时落盘。"""
    from . import selfupdate, store
    run_id = job["run_id"]
    store.update_run(run_id, status="running", started_at=_now())
    step, log_abs = store.add_step(run_id, "selfupgrade", "__self__", "CodeBee")
    try:
        res = selfupdate.run_upgrade(run_id, str(log_abs))
    except Exception as e:
        res = {"ok": False, "exit_code": None, "error": repr(e)}
    store.finish_step(run_id, step["n"], "done" if res["ok"] else "failed",
                      summary="升级完成，点「重启」生效" if res["ok"]
                      else ("升级失败: " + res["error"][:300]),
                      exit_code=res.get("exit_code"))
    store.update_run(run_id, status="done" if res["ok"] else "failed", ended_at=_now(),
                     summary="CodeBee selfupgrade %s" % ("完成" if res["ok"] else "失败"))


def _file_lock_error(res):
    """安装/升级失败输出是否为文件占用类错误（npm/pip 的 EBUSY/EPERM 文件锁）。"""
    err = (res or {}).get("error") or ""
    return "EBUSY" in err or "EPERM" in err


def _ai_repair(run_id, entry, ev, failed_cmd, orig_log):
    """安装失败后的 AI 诊断修复：诊断 → 白名单校验 → 执行 → 复检。"""
    from . import catalog, manager, registry, router, runner, store
    from . import usage as _usage
    agents = registry.effective_agents(catalog.load(), manager.detect_all())
    agent, _reason = router.pick(agents, "repair", "mgmt")
    if agent is None or agent.get("mode") != "real":
        return False  # 无真实智能体可用，维持原失败
    try:
        log_tail = runner.tail_decoded(orig_log.read_bytes(), 2000) if orig_log.exists() else "（无输出）"
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
              .replace("__ENV__", "\n".join(env_lines))
              .replace("__ALLOW__", " / ".join(p.strip() for p in AI_REPAIR_ALLOW)))
    step, log_abs = store.add_step(run_id, "ai-repair", agent["id"], agent.get("label"),
                                   note="自动诊断修复")
    res = runner.run_agent(agent, prompt, readonly=True, timeout=300,
                           cancel_event=ev, log_path=str(log_abs))
    try:
        _usage.record(source="repair", run_id=run_id, step=step["n"], role="ai-repair",
                      agent=agent.get("id", ""), agent_label=agent.get("label", ""),
                      tool=agent.get("kind", ""), model=res.get("model") or "",
                      ok=bool(res.get("ok")),
                      duration_s=float((res.get("raw") or {}).get("duration") or 0.0),
                      cost_usd=float(res.get("cost_usd") or 0.0),
                      usage=res.get("usage"))
    except Exception:
        pass
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
        # write_bytes 而非 write_text(...encode())：bytes 传入文本模式 write 会
        # TypeError 且被下面的裸 except 吞掉，诊断三行从未落过日志（2026-09-18 修）
        log_abs.write_bytes(
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
    if fix["ok"]:
        # 修复安装同样会改变版本结论，徽章缓存一并作废复检
        manager.refresh_update_async(entry)
    installed = manager.detect_entry(entry)["installed"]
    store.finish_step(run_id, step["n"],
                      "done" if (fix["ok"] and installed) else "failed",
                      summary="诊断：%s → 执行 %r：%s，检测安装状态：%s" % (
                          diagnosis, cmd,
                          "命令成功" if fix["ok"] else "命令失败",
                          "已安装" if installed else "未检出"),
                      exit_code=fix.get("exit_code"))
    return bool(fix["ok"] and installed)
