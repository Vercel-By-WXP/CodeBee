# -*- coding: utf-8 -*-
"""Agent 反向通道：运行中的智能体主动汇报阻塞/进展（对偶指挥信箱）。

指挥信箱（store_messages）是用户→智能体的单向递话；本模块补上智能体→用户
的反向通道（2026-10-22 multica 调研落地，攒批「agent 汇报阻塞/进度」）：
- blocker：需要人工拍板/补关键信息——用户在对话区看到汇报卡，回信即解除；
- progress：主动同步重要进展（无人值守时替代「黑盒跑到完」）；
- resolve：智能体自报此前的阻塞已解除。

落点：run["agent_reports"]（与信箱同存 run.json）。写入面四个：HTTP
/api/runs/<id>/agent-reports、两个 MCP 面（mcp_server / mcp_harness）的
report_status 工具、内置智能体 report_status 工具，以及 CLI 步骤最终输出的
[[CODEBEE:BLOCKER|PROGRESS|RESOLVE]] 行标记解析（ingest_markers）。
"""
from __future__ import annotations

import re

_KINDS = ("blocker", "progress", "resolve")

# CLI 输出标记：一行一报（要多行就写多行标记）。只认大写全形键，正常文本
# 不可能撞上；标记行仍留在步骤原文里（审计透明），展示侧用 strip_markers 摘除。
_MARKER_RE = re.compile(r"^[ \t]*\[\[CODEBEE:(BLOCKER|PROGRESS|RESOLVE)\]\][ \t]*(.*)$",
                        re.MULTILINE)


def _store():
    from . import store
    return store


def _ensure_reports(run):
    if not isinstance(run.get("agent_reports"), list):
        run["agent_reports"] = []
    return run["agent_reports"]


def add_report(run_id, kind, text, source="agent", step=None):
    """追加一条 agent 汇报；blocker 顺手推桌面/手机通知。

    kind=resolve 除落一条记账外，还会把该 run 未解除的 blocker 标记
    resolved。返回新增记录 dict；run 不存在/kind 非法/正文为空返回 None
    （resolve 允许空正文——「阻塞解除了」本身就是完整信息）。
    """
    kind = str(kind or "").strip().lower()
    if kind not in _KINDS:
        return None
    text = str(text or "").strip()
    if not text and kind != "resolve":
        return None
    if len(text) > 2000:
        text = text[:2000]
    notify_text = ""
    with _store().LOCK:
        run = _store()._RUNS.get(run_id)
        if not run:
            return None
        reps = _ensure_reports(run)
        if len(reps) >= 100:   # 封顶同信箱纪律：防 run.json 无限膨胀
            del reps[:len(reps) - 99]
        # id 取「现存最大 +1」：封顶删除后 len 会回落，len+1 会撞号
        nxt = 1
        for r in reps:
            try:
                nxt = max(nxt, int(r.get("id") or 0) + 1)
            except (TypeError, ValueError):
                pass
        rec = {
            "id": "%06d" % nxt,
            "kind": kind,
            "text": text,
            "source": str(source or "agent")[:24],
            "step": step,
            "at": _store().time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if kind == "resolve":
            opened = [r for r in reps
                      if r.get("kind") == "blocker" and not r.get("resolved")]
            for r in opened:
                r["resolved"] = True
                r["resolved_by"] = "agent"
            rec["resolved_count"] = len(opened)
        elif kind == "blocker":
            rec["resolved"] = False
            title = (run.get("title") or run_id)[:40]
            notify_text = "⚠ 任务阻塞待拍板 · %s\n%s" % (title, text[:120])
        reps.append(rec)
        _store()._save_json(_store().paths.RUNS_DIR / run_id / "run.json", run)
    if notify_text:
        try:
            from . import notify
            notify.push_text(notify_text)
        except Exception:
            pass   # 通知故障绝不挡汇报落库
    try:
        # 汇报是状态变化：bump 一下让 SSE / 看板立即感知（否则等下一次无关 bump）
        _store().bump_state()
    except Exception:
        pass
    return rec


def list_reports(run_id):
    """只读列出该 run 的全部汇报记录。"""
    run = _store().get_run(run_id)
    if not run:
        return []
    reps = run.get("agent_reports")
    return [dict(r) for r in reps if isinstance(r, dict)] if isinstance(reps, list) else []


def open_blockers(run):
    """未解除的 blocker 列表（入参是 run dict，便于 state 投影复用）。

    run 已终态（含失败/取消）视为全部失效——run 都结束了，阻塞自然无从
    谈起，用户直接看结果即可；这也让重试链上的旧 run 阻塞不再悬着。
    """
    if not isinstance(run, dict):
        return []
    if run.get("status") in _store().TERMINAL_STATUSES:
        return []
    return [r for r in (run.get("agent_reports") or [])
            if isinstance(r, dict) and r.get("kind") == "blocker"
            and not r.get("resolved")]


def resolve_open_blockers(run_id, resolved_by="user"):
    """外部解除（用户在 UI 点掉/回信路径兜底）：返回解除条数。"""
    with _store().LOCK:
        run = _store()._RUNS.get(run_id)
        if not run:
            return 0
        n = 0
        for r in _ensure_reports(run):
            if r.get("kind") == "blocker" and not r.get("resolved"):
                r["resolved"] = True
                r["resolved_by"] = str(resolved_by or "")[:24]
                n += 1
        if n:
            _store()._save_json(_store().paths.RUNS_DIR / run_id / "run.json", run)
        return n


def ingest_markers(run_id, output, step=None):
    """解析步骤最终输出里的标记行为汇报记录（CLI 反向通道的兜底入口）。

    返回新增记录列表；解析/落库异常由调用方兜底（步骤收尾绝不因汇报失败）。
    """
    if not output:
        return []
    out = []
    for m in _MARKER_RE.finditer(str(output)):
        try:
            rec = add_report(run_id, m.group(1).lower(), m.group(2),
                             source="marker", step=step)
        except Exception:
            rec = None
        if rec:
            out.append(rec)
    return out


def strip_markers(text):
    """展示侧摘除标记行（时间线气泡不再残影，原文仍在步骤日志）。"""
    if not text:
        return text
    return _MARKER_RE.sub("", text)
