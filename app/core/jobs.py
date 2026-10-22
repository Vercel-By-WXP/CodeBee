# -*- coding: utf-8 -*-
"""任务执行器：直接启动、无等待队列地执行编排任务与管理操作。

每个 job 一条独立线程，run/step 数据按 run_id 隔离，store 层有全局锁。
设置中的并发数是保护上限：有空位就立即启动。满载时 orchestration 任务
（用户/自动化任务）回滚 queued 排队等下一个空位——补跑 Timer 到点重试，
封顶 BUSY_WAIT_MAX 拍，超时才判失败（满载秒判失败会逼用户原话重发，
侧栏堆同题重复任务，2026-09-22 纸枪对话实案）；升级排水与其余 kind
仍明确失败并提示稍后重试。并发保护保护的是本机资源（CLI 子进程数），
与各 API 厂商的限流无关——厂商侧 429 由 runner 冷却换将兜底。direct
对话（内置智能体直连 API，本机几乎零开销）走独立轻量池，不与编排任务
抢全局槽。安装/升级失败时自动触发 AI 诊断修复：
由真实智能体读取失败日志与本机环境给出修正命令；仅当命令命中白名单前缀
（npm/winget/brew/pip 安装类，按平台取对应渠道）才自动执行，否则把建议命令
记录在运行记录里等人工确认。
"""
from __future__ import annotations

import queue
import os
import re
import threading
import time
import traceback

from .jobs_mgmt import (  # noqa: F401 - 旧调用面再导出
    AI_REPAIR_ALLOW, AI_REPAIR_PROMPT, _ai_repair, _do_mgmt, _do_selfupgrade,
    _file_lock_error, _now, _repair_allow, _repair_command_allowed,
    _repair_command_argv, _repair_os_label, _smoke_response_ok,
)


# 仅保留给旧测试/诊断代码观察；生产 enqueue 永远不向这里写入。
_QUEUE = queue.Queue()
CANCELS = {}
_started = False
_alive = 0            # 已获执行位、尚未结束的 job 数
_restart_drain = False  # 升级重启前原子停止接单；不排队、不打断已运行任务
_target = 12          # 并发保护上限（settings.max_concurrent_jobs）
_chat_alive = 0       # 轻量池：direct 对话在跑数（独立于全局池计数）
_pool_lock = threading.Lock()
_idle_cond = threading.Condition(_pool_lock)
_timer_lock = threading.Lock()
_deferred_timers = {}
_workdir_waiters = {}  # normalized workdir -> {run_id: job} (insertion order)
_worker_lock = threading.Lock()
_workers = {}  # run_id → Thread; immediate follow-ups wait for the old writer to exit
_seq = 0
MAX_POOL = 24
CHAT_POOL = 4         # 轻量池上限：direct 对话直连 API 本机开销极小，4 个足够
BUSY_WAIT_S = 15.0    # 满载排队后的补跑轮询间隔（秒）；测试可调小
BUSY_WAIT_MAX = 40    # 补跑拍数封顶：15s × 40 = 10 分钟，超时才判失败
QUEUED_SWEEP_INTERVAL_S = 60  # 运行期 queued 巡检周期：续跑 Timer 断链的最后防线
QUEUED_SWEEP_MIN_AGE_S = 120  # 只接管卡了超过该秒数的 queued run（正常排队/刚建的不掺和）


class JobsBusyError(RuntimeError):
    """并发保护位已满；调用方应稍后重试，不得排队。"""


class DuplicateJobError(RuntimeError):
    """同一 run 已启动或结束，拒绝重复执行。"""


def configure(max_workers):
    """设置并发保护上限（1-24）；只影响后续启动，不中断已运行任务。"""
    global _target
    _target = max(1, min(MAX_POOL, int(max_workers)))
    return _target


def capacity_status():
    """返回当前并发槽状态，供会产生管理记录的入口做无副作用预检。"""
    with _pool_lock:
        return {
            "accepting": not _restart_drain and _alive < _target,
            "active": _alive,
            "limit": _target,
            "restarting": _restart_drain,
        }


def start_worker():
    """加载并发保护配置；线程只在真实任务到达时创建。"""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_queued_sweep_loop,
                     name="queued-sweep", daemon=True).start()
    try:
        from . import settings
        configure(settings.load()["max_concurrent_jobs"])
        return
    except Exception:
        pass
    configure(12)


def _queued_sweep_loop():
    """运行期 queued 巡检：requeue_pending 只在启动时跑一次，而续跑 Timer
    随进程死亡——resume_enqueue_at 到点时若进程已换血，副本就永远躺平在
    queued（2026-09-29 实案：连载续跑副本躺了 4 小时无人接）。这里周期兜底
    捞起超龄 queued：到点的退避副本、蒸发的满载排队都走 requeue_pending
    统一收口。防双跑靠三层既有闸：_task_active_run 单飞 + enqueue CAS 认领
    + 重复 job 跳过；退避未到点/max_age 内的新鲜记录 requeue_pending 自己跳。"""
    while True:
        time.sleep(QUEUED_SWEEP_INTERVAL_S)
        if _restart_drain:
            continue
        try:
            requeue_pending(limit=10, max_age_s=QUEUED_SWEEP_MIN_AGE_S)
        except Exception:
            pass


def _job_is_light(run):
    """direct 对话走轻量池：内置智能体直连模型 API，本机无 CLI 子进程，
    开销与编排任务（每步起一个 CLI）差几个量级，不该抢全局槽。判定按
    run 所属任务的 type，入口侧零改动（自动化/webhook 等新入口自动跟随）。
    run 缺失或类型未知时保守走全局池。direct 极少数回落 CLI 的场景，轻量
    池上限也只有 CHAT_POOL 个，本机压力可控。"""
    try:
        task_id = (run or {}).get("task_id")
        if not task_id:
            return False
        from . import store
        task = store.get_task(task_id)
        return str((task or {}).get("type") or "") == "direct"
    except Exception:
        return False


def _try_start_once(job):
    """CAS 认领持久化 run → 占并发位 → 起执行线程，一次直接启动尝试。

    返回 True=已起跑；False=并发位满载未启动（run 停在 running，由调用方
    决定回滚排队还是收口失败）。run 已非 queued（被取消/接管/收口）时抛
    DuplicateJobError；线程创建失败就地收口后原样上抛。升级排水在这里视作
    满载的一种（返回 False），快速失败还是排队的语义由调用方区分。"""
    global _alive, _chat_alive, _seq
    from . import store
    run_id = job.get("run_id")
    current = store.get_run(run_id)
    if current:
        # 先 CAS 认领持久化 run，再碰并发位。若先占位，两个调用可能分别看到
        # “容量已满”和“状态已变化”，把唯一 run 误收口为 failed 且无人执行。
        changed = store.update_run(run_id, expected_status="queued",
                                   status="running", started_at=_now(),
                                   dispatch_mode="direct")
        if changed is None:
            latest = store.get_run(run_id) or current
            raise DuplicateJobError("运行 %s 当前状态为 %s，拒绝重复启动" %
                                    (run_id, latest.get("status")))
    cancel_event_for(run_id)

    # CAS 认领后检查并发保护位。占位在 Thread.start 前递增，消除旧实现中线程尚未
    # 回写 alive、扩容循环一次造出几十条 worker 的竞态。direct 对话占轻量池，
    # 其余任务占全局池；job["light"] 随副本传给执行线程，结束时按原池释放。
    light = _job_is_light(current)
    with _pool_lock:
        if _restart_drain:
            busy = True
        elif light:
            busy = _chat_alive >= CHAT_POOL
            if not busy:
                _chat_alive += 1
                job["light"] = True
        else:
            busy = _alive >= _target
            if not busy:
                _alive += 1
        if not busy:
            _seq += 1
            seq = _seq
    if busy:
        CANCELS.pop(run_id, None)
        return False

    # A queued run may have spent minutes waiting for a slot or resume timer.
    # Only the successful slot owner starts the execution clock.  The CAS
    # prevents a cancellation between slot claim and launch from being revived.
    if current and current.get("task_id"):
        task = store.get_task(current["task_id"])
        if task:
            budget_s = store._task_timeout_s(current.get("timeout_budget_s") or
                                             task.get("timeout_s"))
            if store.update_run(run_id, expected_status="running",
                                timeout_budget_s=budget_s,
                                deadline_at=time.time() + budget_s) is None:
                _release_slot(job)
                CANCELS.pop(run_id, None)
                raise DuplicateJobError("运行 %s 已在启动前结束" % run_id)

    try:
        with _worker_lock:
            worker = threading.Thread(target=_run_job, args=(dict(job),),
                                      name="job-direct-%d" % seq, daemon=True)
            _workers[run_id] = worker
            worker.start()
    except Exception:
        with _worker_lock:
            _workers.pop(run_id, None)
        _release_slot(job)
        CANCELS.pop(run_id, None)
        _close_unstarted(job, "任务执行线程启动失败；本次未排队，请稍后重试",
                         statuses=("queued", "running"))
        raise
    return True


def enqueue(job):
    """立即为 job 预留执行位并启动独立线程；从不进入内存等待队列。

    返回前先把持久化 run 从 queued 原子切到 running，因此 HTTP/自动化调用方
    不会看到“已接受但仍排队”。满载时 orchestration 任务转排队等待空位（对
    调用方视作受理成功，见 _requeue_await_slot）；重复 run、线程创建失败、
    升级排水满载都会明确抛错，并就地把 run 收口为 failed，任何入口都不会
    留下无人认领的僵尸。
    """
    if not _started:
        start_worker()
    if not isinstance(job, dict):
        raise ValueError("job 必须是对象")
    run_id = job.get("run_id")
    if not run_id:
        raise ValueError("job.run_id 必填")

    if not _try_start_once(job):
        if _restart_drain:
            # 升级排水：接单通道已在关闭，排队只会排进一次注定重启的进程——
            # 照旧快速失败。
            message = "服务正在完成升级重启；本次未排队，请稍后重试"
            _close_unstarted(job, message,
                             statuses=("running",))
            raise JobsBusyError(message)
        if str(job.get("kind") or "") != "orchestration" or not _requeue_await_slot(job):
            # mgmt 与回滚失败（run 已被取消/收口）维持快速失败原语义；
            # orchestration 已转排队，对调用方视作受理成功。
            message = ("当前运行任务已达并发保护上限（%d）；本次未排队，请稍后重试"
                       % _target)
            _close_unstarted(job, message,
                             statuses=("running",))
            raise JobsBusyError(message)
    return True


def _release_slot(job=None):
    """释放执行位：按 job 的 light 标记归还对应池，两池全空才唤醒等待方。"""
    global _alive, _chat_alive
    with _idle_cond:
        if job is not None and job.get("light"):
            _chat_alive = max(0, _chat_alive - 1)
        else:
            _alive = max(0, _alive - 1)
        if _alive == 0 and _chat_alive == 0:
            _idle_cond.notify_all()


def wait_for_idle(timeout=10):
    """测试/停机辅助：等待所有直接执行任务（含轻量池）结束。"""
    import time as _t
    end = _t.time() + max(0, float(timeout))
    with _idle_cond:
        while _alive or _chat_alive:
            left = end - _t.time()
            if left <= 0:
                return False
            _idle_cond.wait(min(left, 0.2))
        return True


def begin_restart_drain():
    """升级任务已退出执行位且没有用户任务或 direct 对话时，原子停止接单。"""
    global _restart_drain
    with _pool_lock:
        if _restart_drain or _alive != 0 or _chat_alive != 0:
            return False
        _restart_drain = True
        return True


def cancel_restart_drain():
    """重启未执行或失败时恢复接单。"""
    global _restart_drain
    with _pool_lock:
        _restart_drain = False


def begin_shutdown_drain():
    """Stop accepting work before a confirmed desktop shutdown cancels runs."""
    global _restart_drain
    with _pool_lock:
        _restart_drain = True


def _close_unstarted(job, message, statuses=("queued",)):
    """无法启动时统一收口 run/task，供所有入口复用。"""
    try:
        from . import store
        run_id = job.get("run_id")
        run = store.get_run(run_id) if run_id else None
        if run and run.get("status") in statuses:
            store.update_run(run_id, expected_status=run.get("status"), status="failed",
                             error=message, ended_at=_now())
    except Exception:
        pass


def _requeue_await_slot(job, waits_left=None):
    """满载转排队：run 回滚 queued 并挂补跑 Timer，等下一个并发空位。

    只服务 orchestration 任务（用户/自动化任务）。enqueue 起手已把 run CAS
    成 running，这里再 CAS 回 queued（清 started_at，别让「排队中」顶着起跑
    时间），并同步任务状态回排队。返回 True=已转排队（调用方视作受理成功，
    不再向调用方抛忙）；False=回滚失败（run 已被取消/收口），走原快速失败
    路径。取消/重启/超时的收口：
    - 用户取消 → run 已非 running，回滚 CAS 落空，或补跑 enqueue 撞
      DuplicateJobError，链条自然断掉；
    - 服务重启 → Timer 随进程消失，启动时 requeue_pending() 接管 queued 记录；
    - 等满 BUSY_WAIT_MAX 拍仍无空位 → 判失败并给出可读原因。
    """
    run_id = job.get("run_id")
    if waits_left is None:
        waits_left = BUSY_WAIT_MAX
    try:
        from . import store
        changed = store.update_run(run_id, expected_status="running",
                                   status="queued", started_at="")
        if changed is None:
            return False
        if job.get("task_id"):
            try:
                store.update_task_status(job["task_id"], "queued")
            except Exception:
                pass
    except Exception:
        return False

    def _fire():
        # 补跑心跳：直接尝试一次启动（不走 enqueue 的排队分支，否则每拍都会
        # 带着全新重试额度重新入队，封顶永远打不中）。仍满载则续下一拍。
        with _timer_lock:
            _deferred_timers.pop(run_id, None)
        try:
            started = _try_start_once(job)
        except DuplicateJobError:
            return   # 已被取消/接管/收口：补跑链到此为止
        except Exception:
            return   # 线程启动失败等异常路径已就地收口，不续棒
        if started:
            return
        if _restart_drain:
            _close_unstarted(job, "服务正在完成升级重启；本次未排队，请稍后重试",
                             statuses=("queued", "running"))
        elif waits_left > 0:
            _requeue_await_slot(job, waits_left - 1)
        else:
            _close_unstarted(job, "并发位已满，排队等待超时仍未起跑，请稍后重试",
                             statuses=("queued", "running"))

    with _timer_lock:
        if run_id in _deferred_timers:
            return True   # 已有补跑在等：别叠 Timer（防与续跑退避串台）
        timer = threading.Timer(max(0.0, float(BUSY_WAIT_S)), _fire)
        timer.daemon = True
        _deferred_timers[run_id] = timer
        try:
            timer.start()
        except Exception:
            _deferred_timers.pop(run_id, None)
            return False
    return True


def _schedule_enqueue(job, delay_s):
    """按明确截止时间延迟启动；同一 run 只保留一个 Timer。"""
    run_id = job.get("run_id")
    if not run_id:
        return False

    def _fire():
        with _timer_lock:
            _deferred_timers.pop(run_id, None)
            _forget_workdir_waiter_locked(run_id)
        try:
            enqueue(job)
        except Exception:
            # enqueue 会把未启动 run 收口为 failed；Timer 线程不外抛。
            pass

    with _timer_lock:
        old = _deferred_timers.get(run_id)
        if old is not None:
            return False
        timer = threading.Timer(max(0.0, float(delay_s)), _fire)
        timer.daemon = True
        _deferred_timers[run_id] = timer
        try:
            timer.start()
        except Exception:
            _deferred_timers.pop(run_id, None)
            _close_unstarted(job, "自动续跑定时器启动失败，请手动重试")
            raise
    return True


def _workdir_key(workdir):
    """Normalize a workdir for the waiter index without touching the filesystem."""
    try:
        raw = str(workdir or "").strip()
        if not raw:
            return ""
        value = os.path.normcase(os.path.abspath(raw)).rstrip("\\/")
        return value if value and value not in (".", os.path.sep) else ""
    except Exception:
        return ""


def _forget_workdir_waiter_locked(run_id):
    """Remove a run from all workdir waiter buckets; caller holds _timer_lock."""
    for key, waiters in list(_workdir_waiters.items()):
        waiters.pop(run_id, None)
        if not waiters:
            _workdir_waiters.pop(key, None)


def register_workdir_waiter(workdir, job, delay_s=60.0):
    """Register a workdir-blocked orchestration and schedule its fallback timer.

    The timer preserves recovery across process churn; a finishing run can call
    :func:`wake_workdir_waiters` to promote the oldest waiter immediately.
    """
    key = _workdir_key(workdir)
    run_id = (job or {}).get("run_id")
    if not key or not run_id:
        return _schedule_enqueue(job, delay_s)
    queued_job = dict(job)
    queued_job["workdir"] = str(workdir or "")
    with _timer_lock:
        _workdir_waiters.setdefault(key, {})[run_id] = queued_job
    if _schedule_enqueue(queued_job, delay_s):
        return True
    with _timer_lock:
        _forget_workdir_waiter_locked(run_id)
    return False


def wake_workdir_waiters(workdir):
    """Wake the oldest queued orchestration waiting for ``workdir``.

    Only one waiter is promoted per release so a burst of same-directory jobs
    cannot all race back into the pipeline and recreate the 60-second loop.
    """
    key = _workdir_key(workdir)
    if not key:
        return False
    while True:
        with _timer_lock:
            waiters = _workdir_waiters.get(key)
            if not waiters:
                return False
            run_id, job = next(iter(waiters.items()))
            waiters.pop(run_id, None)
            if not waiters:
                _workdir_waiters.pop(key, None)
            timer = _deferred_timers.pop(run_id, None)
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass
        try:
            from . import store
            run = store.get_run(run_id)
            if not run or run.get("status") != "queued":
                continue
            store.update_run(run_id, expected_status="queued", resume_enqueue_at="")
        except Exception:
            continue
        try:
            if _schedule_enqueue(job, 0.0):
                return True
        except Exception:
            pass
        return False


def enqueue_now(run_id):
    """立即重试：退避窗口内的续跑副本跳过等待马上入队（详情页按钮触发）。

    返回 (ok, err)。先摘到点 Timer 再直接 enqueue——CAS 认领保证与 Timer
    竞态时只有一方生效，Timer 抢先起跑按目的已达成处理。resume_enqueue_at
    同步清空：副本一旦入队/起跑，UI 不该再显示「将于 HH:MM 自动续跑」；
    满载转排队后由补跑心跳接管，巡检也把它当普通排队记录。跳过退避是
    用户的主动选择（等效于手动重试），不受「别烧退避窗口」的巡检约束。"""
    from . import store
    run = store.get_run(run_id)
    if not run:
        return False, "运行不存在"
    if run.get("status") != "queued":
        return False, "该运行已不在排队等待中"
    if not str(run.get("resume_enqueue_at") or ""):
        return False, "该运行不在自动续跑等待中"
    if str(run.get("kind") or "orchestration") != "orchestration" or not run.get("task_id"):
        return False, "该运行不支持立即重试"
    resume_at = str(run.get("resume_enqueue_at") or "")
    with _timer_lock:
        old = _deferred_timers.pop(run_id, None)
    if old is not None:
        old.cancel()
    if store.update_run(run_id, resume_enqueue_at="") is None:
        if old is not None:
            _schedule_enqueue({"kind": "orchestration", "run_id": run_id,
                               "task_id": run.get("task_id")}, 1.0)
        return False, "运行状态已变化，请刷新后重试"
    try:
        enqueue({"kind": "orchestration", "run_id": run_id,
                 "task_id": run.get("task_id")})
    except DuplicateJobError:
        pass   # 到点 Timer 抢先起跑/运行已被取消接管：无需再排
    except Exception as exc:
        # A non-duplicate enqueue failure must not strand the run with the
        # backoff marker cleared and its original timer removed.
        current = store.get_run(run_id)
        if current and current.get("status") == "queued":
            store.update_run(run_id, resume_enqueue_at=resume_at)
            try:
                import time as _time
                due = _time.mktime(_time.strptime(resume_at, "%Y-%m-%d %H:%M:%S"))
                _schedule_enqueue({"kind": "orchestration", "run_id": run_id,
                                   "task_id": run.get("task_id")}, max(0.0, due - _time.time()))
            except Exception:
                pass
        return False, "立即重试失败，已恢复自动续跑：%s" % str(exc)[:160]
    return True, ""


def force_start_run(run_id):
    """强制起跑：无视同工作目录互斥立即入队起跑（详情页「强制起跑」按钮）。

    与 enqueue_now 的差别：不要求处于退避窗口，专服务被 _workdir_blocker
    打回排队的工作目录占用等待者。先在 run 上落 force_start 标记（起跑后的
    互斥检查见到标记即放行并留痕 warning），再摘到点补跑 Timer 立即 enqueue。
    用户点按钮 = 人工判定两个任务不冲突；共享的任务计划/项目记忆风险由前端
    确认弹窗明示。满载排队者点这个按钮意义不大（起跑仍会转回排队），但
    force_start 标记保留，等空位起跑时同样跳过互斥。返回 (ok, err)。"""
    from . import store
    run = store.get_run(run_id)
    if not run:
        return False, "运行不存在"
    if run.get("status") != "queued":
        return False, "该运行已不在排队等待中"
    if str(run.get("kind") or "orchestration") != "orchestration" or not run.get("task_id"):
        return False, "该运行不支持强制起跑"
    if store.update_run(run_id, expected_status="queued", force_start=True,
                        blocked_by="") is None:
        return False, "运行状态已变化，请刷新后重试"
    with _timer_lock:
        old = _deferred_timers.pop(run_id, None)
    if old is not None:
        old.cancel()
    # UI 别再显示「将于 HH:MM 自动续跑」：本 run 已改为人工强制语义
    store.update_run(run_id, resume_enqueue_at="")
    try:
        enqueue({"kind": "orchestration", "run_id": run_id,
                 "task_id": run.get("task_id")})
    except DuplicateJobError:
        pass   # 到点 Timer 抢先起跑/已被取消接管：标记已落，目的达成
    except Exception as exc:
        return False, "强制起跑失败：%s" % str(exc)[:160]
    return True, ""


def restore_deferred_resumes(limit=None, now=None):
    """重建重启前的自动续跑退避 Timer；返回成功恢复的数量。"""
    import time as _t
    try:
        from . import store
    except Exception:
        return 0
    now = _t.time() if now is None else float(now)
    restored = 0
    for run in store.list_runs(None):
        if limit is not None and restored >= limit:
            break
        if run.get("status") != "queued" or run.get("kind") != "orchestration":
            continue
        at = str(run.get("resume_enqueue_at") or "")
        if not at:
            continue
        try:
            due = _t.mktime(_t.strptime(at, "%Y-%m-%d %H:%M:%S"))
        except Exception:
            due = now
        job = {"kind": "orchestration", "run_id": run["id"],
               "task_id": run.get("task_id")}
        if _schedule_enqueue(job, max(0.0, due - now)):
            restored += 1
    return restored


def _run_job(job):
    """直接执行线程入口；无 get/put 等待阶段。"""
    run_id = job.get("run_id")
    ev = cancel_event_for(run_id) if run_id else threading.Event()
    did_execute = False
    try:
        # enqueue 已把 run 原子切为 running。线程真正得到调度时再检查一次，
        # 用户若在两者之间取消，直接跳过，避免取消后仍进入流水线。
        if run_id:
            from . import store
            r0 = store.get_run(run_id)
            if r0 and r0.get("status") != "running":
                return
            if job.get("kind") == "orchestration" and _yield_duplicate(run_id):
                return
        if job.get("kind") == "orchestration":
            from . import pipeline
            did_execute = True
            pipeline.execute_run(run_id)
        elif job.get("kind") == "mgmt":
            did_execute = True
            _do_mgmt(job, ev)
        elif job.get("kind") == "selfupgrade":
            did_execute = True
            _do_selfupgrade(job, ev)
        else:
            raise ValueError("未知任务类型 %r" % job.get("kind"))
    except Exception:
        try:
            from . import store
            err = traceback.format_exc()
            store.update_run(run_id, expected_status="running", status="failed",
                             error=err[-1500:], ended_at=_now())
        except Exception:
            pass
    finally:
        workdir_to_wake = ""
        should_wake_workdir = False
        if run_id:
            # 执行线程退出就是生命周期的最后边界。流水线分支若意外提前 return、
            # 或内部吞掉异常却漏写终态，旧逻辑会让 run/task 永久停在 running。
            try:
                from . import store
                latest = store.get_run(run_id)
                workdir_to_wake = store.run_workdir(run_id)
                if latest and latest.get("status") == "running":
                    store.update_run(
                        run_id, expected_status="running", status="failed",
                        ended_at=_now(),
                        error="执行流程已退出但未写入完成状态，已自动标记失败；可重试")
                    should_wake_workdir = True
                elif (did_execute and latest
                      and latest.get("status") not in ("queued", "running")):
                    # A pipeline that deliberately returned itself to queued
                    # (for example, a workdir blocker) must not wake that same
                    # waiter while its blocker is still running.
                    should_wake_workdir = True
            except Exception:
                pass
            CANCELS.pop(run_id, None)
            try:
                _maybe_auto_resume(run_id)
            except Exception:
                pass
            try:
                from . import notify
                notify.push_run_async(run_id)
            except Exception:
                pass
        _release_slot(job)
        if should_wake_workdir and workdir_to_wake:
            try:
                wake_workdir_waiters(workdir_to_wake)
            except Exception:
                pass
        with _worker_lock:
            _workers.pop(run_id, None)


def wait_run_stopped(run_id, timeout=10):
    """Wait for this run's writer, rather than every unrelated job, to finish."""
    with _worker_lock:
        worker = _workers.get(run_id)
    if worker is None:
        return True
    worker.join(max(0, float(timeout)))
    return not worker.is_alive()


def cancel(run_id):
    ev = CANCELS.get(run_id)
    if ev:
        ev.set()
        # 强制终止语义：置位事件后立刻把 running 落成 cancelled 终态，
        # UI 即刻翻牌，不等流水线走到下一个检查点（CLI 子进程树由取消
        # 事件在 run_process 的 0.4s 轮询里秒杀；内置智能体生成中的 HTTP
        # 由取消等待方放弃）。expected_status 守卫保证已正常结束的运行
        # 不被误改；流水线随后照常收尾，终态幂等。
        try:
            from . import store
            store.update_run(run_id, expected_status="running",
                             status="cancelled", ended_at=_now(),
                             error="用户主动取消")
        except Exception:
            pass
        return True
    # 事件不存在也可能撞在 enqueue 已把 run 切成 running、尚未来得及登记
    # CANCELS 的极短窗口。无论 queued/running 都直接落终态，不能让取消丢失。
    try:
        from . import store
        run = store.get_run(run_id)
        if not run:
            return False
        status = run.get("status")
        if status in ("queued", "running"):
            changed = store.update_run(run_id, expected_status=status,
                                       status="cancelled", ended_at=_now(),
                                       error="用户主动取消",
                                       cancelled_by_user=True)
            if changed is not None:
                with _timer_lock:
                    timer = _deferred_timers.pop(run_id, None)
                    _forget_workdir_waiter_locked(run_id)
                if timer is not None:
                    timer.cancel()
                return True
            latest = store.get_run(run_id) or {}
            return latest.get("status") == "cancelled"
    except Exception:
        pass
    return False


def cancel_all():
    """急停（kill switch）：取消全部 queued/running 运行，返回实际取消数。

    逐跑走 cancel() 同一条链路（事件置位 + 终态落盘 + 退避定时器清理），
    单跑失败不影响其余。cancelled_by_user 先落——与单跑取消端点同一语义，
    自动续跑不得把急停的任务又续上。
    """
    from . import store
    n = 0
    for r in store.list_runs(limit=None):
        rid = r.get("id")
        if not rid or (r.get("status") or "") not in ("queued", "running"):
            continue
        try:
            store.update_run(rid, cancelled_by_user=True)
        except Exception:
            pass
        if cancel(rid):
            n += 1
    return n


def cancel_event_for(run_id):
    ev = CANCELS.get(run_id)
    if ev is None:
        ev = threading.Event()
        CANCELS[run_id] = ev
    return ev


# 连载任务自动续跑上限（超时/中断后自动接着写，无需人工）。3 是 2026-09-25
# 戍边骑奴 9-20·续2 案的上限：20 章连载 3 轮烧完次数后卡死在「任务总时限已到」
# 等人工，而每轮都在稳定进章——止损已由零进度两轮同因止损兜底，上限只作
# 最后保险（真烧钱还有日/月花费硬顶闸），10 轮足够 20 章慢链天跑完。
AUTO_RESUME_MAX = 10
AUTO_RESUME_DELAY_S = 300  # 自动续跑延迟入队秒数：网关限流/欠费窗口通常分钟级，
                           # 立即重排会撞在同一堵墙上把续跑次数烧光（2026-09-17 七猫实测）

# 等待类失败特征（dsh「等待子任务期间不消耗目标轮次」借鉴，B8）：撞限流/额度
# 窗口/临时拥堵属于「墙会自己挪」的失败——退避等待不该烧正式续跑预算，走
# 独立的等待重排计数（AUTO_WAIT_RESUME_MAX），总闸仍是两边之和。
AUTO_WAIT_RESUME_MAX = 10
_WAIT_ERR_RE = re.compile(
    r"429|rate.?limit|限流|速率|额度|欠费|quota|余额|拥堵|暂时不可用|"
    r"他人在控制|423|过载|overload|熔断|稍后(?:再试|重试)|服务暂不可用",
    re.IGNORECASE)


def is_wait_type_error(err):
    """失败是否属于「等待型」：退避后大概率自愈的临时墙（限流/额度窗口）。"""
    return bool(_WAIT_ERR_RE.search(str(err or "")))


def pool_pressure():
    """(活跃运行数, 池上限)——create_task 等入口的并发压力提前提示（B8）。"""
    try:
        from . import store
        active = sum(1 for r in store.list_runs(limit=200)
                     if r.get("status") in ("queued", "running"))
    except Exception:
        active = 0
    return active, MAX_POOL


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


def _serial_progress(run):
    """连载运行的已过审章数——超时续跑的进度锚点（chapter_scores 长度）。"""
    try:
        return len(run.get("chapter_scores") or [])
    except Exception:
        return 0


def _maybe_auto_resume(run_id):
    """连载任务失败/超时自动续跑：继承已完成章继续，最多 AUTO_RESUME_MAX 次。

    真实长篇单次运行常因供应商拥堵超时中断；这里在执行线程收尾时自动续跑一次
    续跑（store.retry_task 会带上 inherit），让整个流程真正无人值守。
    2026-09-24 戍边骑奴 9-20 案：「任务总时限已到」终态此前不续跑，
    长篇在慢链天仍可能超过单次预算；超时也是应自动接着写的一种中断。
    同因连撞止损：续跑副本再失败时与本次失败的错误签名比对（2026-09-18
    重写任务 kimi 403 欠费案），一模一样说明退避没换来不同结果，直接落
    终态写明死因，不再烧剩余的退避次数。超时例外：错误签名恒为同一句
    「任务总时限已到」，但每轮都在落章——本轮比上轮多过审了章就不算
    「同因无效重试」，续跑交给次数上限兜底；两轮一章未进则照常止损。
    上限烧完时（超时且有进章）把已过审进度与断点续跑指引写进错误行，
    别让执行结果卡读起来像白跑。
    """
    try:
        from . import store
        run = store.get_run(run_id)
        if not run or run.get("status") not in ("failed", "cancelled", "timeout"):
            return False
        task = store.get_task(run.get("task_id")) if run.get("task_id") else None
        if not task or not task.get("serial"):
            return False
        if run.get("cancelled_by_user") or run.get("status") == "cancelled":
            return False   # 用户主动取消的运行绝不自动续跑
        # 等待类失败（限流/额度窗口/临时拥堵，B8）不烧正式续跑预算：墙大概率
        # 会自己挪，退避重排走独立计数；同因止损也豁免——等待的意义就是同因
        # 再试一次。总闸（正式+等待）与各上限仍然兜底，不会无限烧钱。
        wait_type = is_wait_type_error(run.get("error"))
        auto_resumes = int(run.get("auto_resumes") or 0)
        wait_resumes = int(run.get("auto_wait_resumes") or 0)
        if auto_resumes + wait_resumes >= AUTO_RESUME_MAX + AUTO_WAIT_RESUME_MAX:
            return False
        if wait_type:
            if wait_resumes >= AUTO_WAIT_RESUME_MAX:
                store.update_run(
                    run_id,
                    error=(run.get("error") or "")
                    + "｜等待类自动续跑次数已用完（限流/额度窗口持续未恢复）")
                return False
        elif auto_resumes >= AUTO_RESUME_MAX:
            # 上限烧完不再续跑。但「任务总时限已到」+本轮有进章时，错误行必须
            # 带上进度与续跑方式——不然执行结果卡只写「任务总时限已到」，读起来
            # 像白跑（2026-09-25 续2 案：用户看到 64/64 步+超时以为续跑没生效）。
            if run.get("status") == "timeout" and _serial_progress(run) > 0:
                try:
                    total = int((task.get("serial") or {}).get("chapters") or 0)
                    where = ("/" + str(total) + " 章") if total > 0 else " 章"
                    store.update_run(
                        run_id,
                        error=(run.get("error") or "")
                        + "｜自动续跑次数已用完，已过审 "
                        + str(_serial_progress(run)) + where
                        + "；点重试将从断点继续，已过审章不会重写")
                except Exception:
                    pass
            return False
        prev_id = run.get("auto_resumed_from")
        if prev_id and not wait_type:
            # 同因止损对等待类豁免（B8）：限流窗口里同因重试正是等待的语义，
            # 由 AUTO_WAIT_RESUME_MAX 与总闸兜底；非等待类维持原止损。
            prev = store.get_run(prev_id)
            sig_now = _err_signature(run.get("error"))
            timeout_progressed = (run.get("status") == "timeout"
                                  and _serial_progress(run) > _serial_progress(prev or {}))
            if (prev and sig_now and prev.get("error")
                    and sig_now == _err_signature(prev.get("error"))
                    and not timeout_progressed):
                store.update_run(run_id, auto_resume_stopped="same_cause",
                                 error=(run.get("error") or "")
                                 + "｜自动续跑止损：连续两次失败原因相同，不再重试")
                return False
        if _task_active_run(task["id"], exclude_run_id=run_id):
            return False   # 同任务已有运行排队/在跑：再排副本只会与之撞车
        ok, err, new_run = store.retry_task(task["id"])
        if not ok or not new_run:
            return False
        # 退避窗口要让用户看得见：把「预定启动时刻」写到 run 上，前端据此显示
        # 「将在 HH:MM 自动续跑」。这不是容量排队，且等待期间不占执行位。
        import time as _t
        resume_at = _t.strftime("%Y-%m-%d %H:%M:%S",
                                _t.localtime(_t.time() + AUTO_RESUME_DELAY_S))
        new_counters = {"auto_resumed_from": run_id, "resume_enqueue_at": resume_at}
        if wait_type:
            new_counters["auto_resumes"] = auto_resumes
            new_counters["auto_wait_resumes"] = wait_resumes + 1
        else:
            new_counters["auto_resumes"] = auto_resumes + 1
            new_counters["auto_wait_resumes"] = wait_resumes
        store.update_run(new_run["id"], **new_counters)

        _schedule_enqueue({"kind": "orchestration", "run_id": new_run["id"],
                           "task_id": task["id"]}, AUTO_RESUME_DELAY_S)
        return True
    except Exception:
        return False


RESUME_WINDOW_HOURS = 24   # 启动恢复只看最近 24h 内中断的运行（更早的视为已放弃）
UNCLAIMED_QUEUE_MAX_AGE_S = RESUME_WINDOW_HOURS * 3600


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
            if int(run.get("auto_resumes") or 0) + int(run.get("auto_wait_resumes") or 0) \
                    >= AUTO_RESUME_MAX + AUTO_WAIT_RESUME_MAX:
                continue
            ok, err, new_run = store.retry_task(task["id"])
            if not ok or not new_run:
                continue
            # 启动恢复沿用 B8 分账：等待类计数器原样继承，正式计数 +1
            store.update_run(new_run["id"],
                             auto_resumes=int(run.get("auto_resumes") or 0) + 1,
                             auto_wait_resumes=int(run.get("auto_wait_resumes") or 0),
                             auto_resumed_from=run["id"])
            try:
                enqueue({"kind": "orchestration", "run_id": new_run["id"],
                         "task_id": task["id"]})
                n += 1
            except Exception:
                pass
    except Exception:
        return n
    return n


def _yield_duplicate(run_id):
    """同任务单飞（执行线程入口把关）：系统续跑副本启动时若同任务已有
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
    """直接启动遗留的 queued 运行（启动恢复与运行期巡检共用）。

    兼容旧版本曾持久化的排队状态，以及续跑 Timer 随进程消失的历史情况。
    同任务已有在跑/等待续跑的不重复启动。mgmt 同样纳入，避免旧的排队记录
    堵住同条目去重闸。selfupgrade
    不补——升级本体有进程替换语义，自动重排不可控。

    max_age_s：巡检模式只接管「卡了超过该秒数」的，刚创建的退避记录不掺和；
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
            kind = run.get("kind")
            if run.get("status") != "queued" or kind not in ("orchestration", "mgmt"):
                continue
            # requeue_pending 也会在进程存活期间由巡检线程调用。load_all() 只
            # 能清理启动时发现的 queued 残留；如果进程一直不重启，旧的测试/异常
            # 创建记录会永远绕过 _recent() 留在「排队中」。超过恢复窗口的记录
            # 已不具备自动恢复价值，直接收口并同步任务状态，保留用户手动重试入口。
            # 明确的未来 resume_enqueue_at 属于有意退避，即使创建时间很老也不能提前
            # 打断；到点后下一轮巡检会重新进入这里。
            if _in_resume_backoff(run):
                continue
            if kind == "orchestration":
                if not run.get("task_id") or not _recent(run):
                    # Keep malformed/old records eligible for the expiry path,
                    # but never let an old duplicate override a newer run.
                    if run.get("task_id") and _task_active_run(
                            run["task_id"], exclude_run_id=run["id"]):
                        continue
                elif _task_active_run(run["task_id"], exclude_run_id=run["id"]):
                    continue   # 同任务已有更活跃的运行，别收口/再排一份
            if _age_s(run) >= UNCLAIMED_QUEUE_MAX_AGE_S:
                store.update_run(
                    run["id"], expected_status="queued", status="failed",
                    ended_at=_now(),
                    error="排队记录已过期，服务未能在恢复窗口内启动，可重试",
                )
                continue
            if max_age_s is not None and _age_s(run) < max_age_s:
                continue
            if kind == "orchestration":
                if not run.get("task_id") or not _recent(run):
                    continue
            else:
                eid = run.get("entry_id")
                if eid:
                    other = store.active_mgmt_run(eid)
                    if other and other.get("id") != run["id"]:
                        continue   # 同条目已有更活跃的 run，去重闸语义收敛
            try:
                enqueue({"kind": kind, "run_id": run["id"],
                         "task_id": run.get("task_id"),
                         "entry_id": run.get("entry_id"), "op": run.get("op")})
                n += 1
            except (JobsBusyError, DuplicateJobError):
                # 满载时 enqueue 已明确收口；重复 run 已由另一个执行方接管。
                continue
            except Exception:
                continue
    except Exception:
        pass
    return n


def workers_info():
    with _pool_lock:
        info = {"target": _target, "alive": _alive,
                "chat_alive": _chat_alive, "chat_pool": CHAT_POOL,
                "available": 0 if _restart_drain else max(0, _target - _alive),
                "mode": "restart-drain" if _restart_drain else "direct"}
    # queued runs are deliberately persisted rather than held in _QUEUE. Report
    # the same source the board renders so capacity waits are observable.
    try:
        from . import store
        info["queued"] = sum(1 for run in store.list_runs(None)
                             if run.get("status") == "queued")
    except Exception:
        info["queued"] = 0
    return info


def _drain_test_queue():
    """测试辅助：清空兼容队列并取消未决 Timer（生产代码勿调）。"""
    cancel_restart_drain()
    with _timer_lock:
        timers = list(_deferred_timers.values())
        _deferred_timers.clear()
        _workdir_waiters.clear()
    for timer in timers:
        try:
            timer.cancel()
        except Exception:
            pass
    while True:
        try:
            _QUEUE.get_nowait()
            _QUEUE.task_done()
        except Exception:
            return

