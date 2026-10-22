# -*- coding: utf-8 -*-
"""运行消息队列与暂停控制的存储逻辑。

从 store 拆出（单文件行数治理），原模块再导出保持旧调用面；
跨模块引用经 _store() 解析，保持运行时状态与测试打桩语义。
"""
from __future__ import annotations


def _store():
    from . import store
    return store


def _ensure_messages(run):
    if "messages" not in run or not isinstance(run.get("messages"), list):
        run["messages"] = []
    return run["messages"]


def append_run_inject(run_id, text):
    """追加钩子注入文本到 run.hook_inject（message_submit 钩子通道）。
    直连轮间组装提示词时消费一次并清零。返回 bool。"""
    text = str(text or "").strip()
    if not text:
        return False
    with _store().LOCK:
        run = _store()._RUNS.get(run_id)
        if not run:
            return False
        old = str(run.get("hook_inject") or "")
        run["hook_inject"] = (old + ("\n\n" if old and text else "") + text)[:4000]
        _store()._save_json(_store().paths.RUNS_DIR / run_id / "run.json", run)
        return True


def pop_run_inject(run_id):
    """取出并清空 hook_inject（直连轮间提示词头一次性消费）。"""
    with _store().LOCK:
        run = _store()._RUNS.get(run_id)
        if not run:
            return ""
        txt = str(run.get("hook_inject") or "")
        if txt and "hook_inject" in run:
            run["hook_inject"] = ""
            try:
                _store()._save_json(_store().paths.RUNS_DIR / run_id / "run.json", run)
            except Exception:
                pass
        return txt


def add_message(run_id, text, sender="本机", attachments=None):
    """往运行信箱追加一条用户指令。attachments 为已落盘到 workdir 的
    附件相对路径清单（由 main.py 提交后传入）。返回消息 dict 或 None。"""
    text = str(text or "").strip()
    atts = [str(a) for a in (attachments or []) if a][:12]
    if not text and not atts:
        return None
    if len(text) > 4000:
        text = text[:4000]
    with _store().LOCK:
        run = _store()._RUNS.get(run_id)
        if not run:
            return None
        msgs = _store()._ensure_messages(run)
        if len(msgs) >= 200:  # 信箱封顶：只保留最近 200 条，防 run.json 无限膨胀
            del msgs[:len(msgs) - 199]
        # id 取「现存最大 +1」而非 len+1：撤回/封顶删除后 len 会回落，
        # 用 len+1 会和存活消息撞号，前端按 id 撤回就错杀。
        nxt = 1
        for m in msgs:
            try:
                nxt = max(nxt, int(m.get("id") or 0) + 1)
            except (TypeError, ValueError):
                pass
        msg = {
            "id": "%06d" % nxt,
            "text": text, "sender": str(sender or "")[:24],
            "attachments": atts,
            "created_at": _store().time.strftime("%H:%M:%S"),
            "consumed": False,
        }
        msgs.append(msg)
        # 反向通道闭环（2026-10-22 multica）：用户回信=对未解除阻塞的应答，
        # 顺手解除该 run 的 blocker——否则阻塞卡永远红着，回信了也不消。
        # 同锁内直接改同一 run dict，不再绕道 store_reports 的加锁助手。
        reps = run.get("agent_reports")
        if isinstance(reps, list):
            for r in reps:
                if (isinstance(r, dict) and r.get("kind") == "blocker"
                        and not r.get("resolved")):
                    r["resolved"] = True
                    r["resolved_by"] = "user:" + msg["id"]
        _store()._save_json(_store().paths.RUNS_DIR / run_id / "run.json", run)
        return msg


def drain_messages(run_id, consumed_by=None):
    """取出并标记全部未消费消息（运行中指挥注入点调用）。

    consumed_by：{"step": 步号, "role": 角色}——送达回执，记录指令最终进了
    哪个步骤，详情页「✓已下达」据此显示去向。返回 [{text,attachments,...}]。"""
    with _store().LOCK:
        run = _store()._RUNS.get(run_id)
        if not run:
            return []
        pend = [m for m in _store()._ensure_messages(run) if not m.get("consumed")]
        if not pend:
            return []
        receipt = None
        if consumed_by:
            try:
                receipt = {"step": int(consumed_by.get("step") or 0),
                           "role": str(consumed_by.get("role") or "")[:40]}
            except Exception:
                receipt = None
        for m in pend:
            m["consumed"] = True
            if receipt:
                m["consumed_by"] = dict(receipt)
        _store()._save_json(_store().paths.RUNS_DIR / run_id / "run.json", run)
        return [{"text": m.get("text", ""), "attachments": m.get("attachments", []),
                 "sender": m.get("sender", ""), "created_at": m.get("created_at", "")}
                for m in pend]


def peek_messages(run_id):
    """只读未消费消息（不标记）：规划步骤合入上下文用，执行步骤仍会 drain 注入。"""
    with _store().LOCK:
        run = _store()._RUNS.get(run_id)
        if not run:
            return []
        return [{"id": m.get("id"), "text": m.get("text", ""), "attachments": m.get("attachments", []),
                 "sender": m.get("sender", ""), "created_at": m.get("created_at", "")}
                for m in _store()._ensure_messages(run) if not m.get("consumed")]


def pending_message(run_id, msg_id):
    """Resolve an existing queued message, rejecting delivered inherited copies."""
    with _store().LOCK:
        run = _store()._RUNS.get(run_id)
        if not run:
            return None
        message = next((m for m in run.get("messages") or []
                        if str(m.get("id")) == str(msg_id)), None)
        if not message:
            return None
        key = (message.get("id"), message.get("created_at"), message.get("text"))
        for owner in _store()._RUNS.values():
            if owner["id"] != run_id and (not run.get("task_id") or
                    owner.get("task_id") != run.get("task_id")):
                continue
            if any(m.get("consumed") and
                   (m.get("id"), m.get("created_at"), m.get("text")) == key
                   for m in owner.get("messages") or []):
                return None
        return dict(message)


def retract_message(run_id, msg_id):
    """撤回一条尚未下达（consumed=False）的指令：从信箱删除。

    只删未消费的——已随步骤注入执行的删不掉（那是审计事实，且 prompt 已喂出）。
    返回 (ok, err)：ok=False 时 err 说明原因，供前端 toast。"""
    msg_id = str(msg_id or "").strip()
    if not msg_id:
        return False, "缺少消息 id"
    with _store().LOCK:
        run = _store()._RUNS.get(run_id)
        if not run:
            return False, "运行不存在"
        msgs = _store()._ensure_messages(run)
        for m in msgs:
            if str(m.get("id")) == msg_id:
                key = (m.get("id"), m.get("created_at"), m.get("text"))
                copies = [(r, other) for r in _store()._RUNS.values()
                          if r["id"] == run_id or (run.get("task_id") and
                                                  r.get("task_id") == run.get("task_id"))
                          for other in r.get("messages") or []
                          if (other.get("id"), other.get("created_at"), other.get("text")) == key]
                delivered = next((other for _, other in copies if other.get("consumed")), None)
                if delivered:
                    cb = delivered.get("consumed_by") or {}
                    where = ("已随步骤 #" + str(cb.get("step")) + " 送达") if cb.get("step") \
                        else "已送达执行"
                    return False, "指令" + where + "，无法撤回"
                for owner, other in copies:
                    owner["messages"].remove(other)
                    _store()._save_json(_store().paths.RUNS_DIR / owner["id"] / "run.json", owner)
                _store().bump_state()
                return True, ""
        return False, "消息不在信箱中（可能已被撤回）"


def set_paused(run_id, paused):
    """暂停/放行，并记录请求是否已经到达下一步闸门。

    ``paused`` is kept for old callers.  New readers should use
    ``pause_phase``: ``requested`` means the current step is still running;
    ``paused`` means the gate has actually been reached.
    """
    with _store().LOCK:
        run = _store()._RUNS.get(run_id)
        if not run:
            return False
        target = bool(paused)
        if target:
            phase = "paused" if run.get("pause_phase") == "paused" else "requested"
            update = {"paused": True, "pause_phase": phase,
                      "pause_requested_at": run.get("pause_requested_at") or
                      _store().time.strftime("%Y-%m-%d %H:%M:%S"),
                      "pause_reached_at": run.get("pause_reached_at") if phase == "paused" else None}
        else:
            update = {"paused": False, "pause_phase": "none",
                      "pause_requested_at": None, "pause_reached_at": None}
    _store().update_run(run_id, **update)
    return True


def pause_control():
    """全量步骤暂停计数，不受运行列表窗口限制；管理操作没有步骤闸门。"""
    with _store().LOCK:
        active = [run for run in _store()._RUNS.values()
                  if run.get("kind") == "orchestration"
                  and run.get("status") in ("queued", "running")]
        return {"active": len(active),
                "paused": sum(1 for run in active if run.get("paused"))}


def pause_observability():
    """Return the split pause lifecycle without changing the legacy summary."""
    with _store().LOCK:
        active = [run for run in _store()._RUNS.values()
                  if run.get("kind") == "orchestration"
                  and run.get("status") in ("queued", "running")]
        requested = sum(1 for run in active
                        if run.get("paused") and
                        run.get("pause_phase", "requested") == "requested")
        reached = sum(1 for run in active
                      if run.get("paused") and
                      run.get("pause_phase") == "paused")
        return {"active": len(active), "requested": requested, "reached": reached}


def mark_pause_reached(run_id):
    """Mark the gate reached once no step from the current parallel wave runs."""
    changed = False
    with _store().LOCK:
        run = _store()._RUNS.get(run_id)
        if (not run or not run.get("paused") or
                run.get("status") not in ("queued", "running")):
            return False
        if any(s.get("status") == "running" for s in run.get("steps") or []):
            return False
        if run.get("pause_phase") == "paused":
            return True
        run["pause_phase"] = "paused"
        run["pause_reached_at"] = _store().time.strftime("%Y-%m-%d %H:%M:%S")
        _store()._save_json(_store().paths.RUNS_DIR / run_id / "run.json", run)
        changed = True
    if changed:
        _store().bump_state()
    return True


def set_paused_all(paused):
    """批量暂停/放行所有仍在排队或运行的编排任务。

    只触碰活动 run；终态记录保留原值，避免运行记录页的全局控制改变历史。
    返回实际发生变化的 run 数，供 UI 给出准确反馈。
    """
    target = bool(paused)
    changed = 0
    with _store().LOCK:
        for run in _store()._RUNS.values():
            if run.get("kind") != "orchestration" or run.get("status") not in ("queued", "running"):
                continue
            if bool(run.get("paused")) == target:
                continue
            run["paused"] = target
            if target:
                run["pause_phase"] = ("paused" if run.get("pause_phase") == "paused"
                                       else "requested")
                run["pause_requested_at"] = run.get("pause_requested_at") or _store().time.strftime(
                    "%Y-%m-%d %H:%M:%S")
                if run["pause_phase"] != "paused":
                    run["pause_reached_at"] = None
            else:
                run["pause_phase"] = "none"
                run["pause_requested_at"] = None
                run["pause_reached_at"] = None
            _store()._save_json(_store().paths.RUNS_DIR / run["id"] / "run.json", run)
            changed += 1
    if changed:
        _store().bump_state()
    return changed