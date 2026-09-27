# -*- coding: utf-8 -*-
"""CodeBee 的 MCP 服务器形态：其他 AI 客户端（Claude Code / ZCode / Cursor）
把这里当工具服务器挂载，即可直接创建任务、查状态、看用量与评测榜单——
CodeBee 从编排工具变成可以被别的智能体调度的生态节点。

stdio JSON-RPC 2.0（按行分帧，与 core/mcp_client 同一传输约定）。启动：
  python -X utf8 <CodeBee目录>/app/mcp_server.py
客户端配置示例（Claude Code / 兼容客户端）：
  {"mcpServers": {"codebee": {"command": "python",
                              "args": ["-X", "utf8", "<CodeBee目录>/app/mcp_server.py"]}}}
仅本机 stdio，无网络监听，凭据不经过此通道；create_task 会真实调度智能体
执行（与界面上建任务同一条链）。
"""
from __future__ import annotations

import json
import sys

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "codebee", "version": "1.0"}

TOOLS = [
    {"name": "create_task", "description": "创建并启动一个 CodeBee 编排任务（真实调度智能体执行，与界面建任务同链）。返回 task_id 与 run_id。",
     "inputSchema": {"type": "object", "properties": {
         "goal": {"type": "string", "description": "任务目标（一句话说清要做什么）"},
         "type": {"type": "string", "description": "任务类型 id（如 code/novel/doc/direct），默认 doc"},
         "workdir": {"type": "string", "description": "工作目录绝对路径；留空用 CodeBee 默认保存路径"},
         "title": {"type": "string", "description": "任务标题；留空取目标前 30 字"}},
         "required": ["goal"]}},
    {"name": "get_status", "description": "查任务与最近一次运行的状态：进度、当前步骤、评分、错误。",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string", "description": "任务 ID"}}, "required": ["task_id"]}},
    {"name": "list_recent", "description": "列出最近的运行（id/标题/状态/时间）。",
     "inputSchema": {"type": "object", "properties": {
         "limit": {"type": "integer", "description": "条数，默认 10，最大 30"}}}},
    {"name": "usage_summary", "description": "用量台账汇总：今日/本月花费（¥）与 token 总量。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "bench_leaderboard", "description": "模型评测基准台能力榜（综合分 Top N）。",
     "inputSchema": {"type": "object", "properties": {
         "top": {"type": "integer", "description": "取前 N，默认 5"}}}},
]


def _enqueue(job):
    """thin wrapper：便于单测 mock（真链会拉起 worker 执行）。"""
    from core import jobs
    jobs.enqueue(job)


def _tool_create_task(args):
    from core import settings as settings_mod, store
    goal = str(args.get("goal") or "").strip()
    if not goal:
        return {"isError": True, "content": "goal 不能为空"}
    payload = {"type": str(args.get("type") or "doc").strip()[:40] or "doc",
               "title": (str(args.get("title") or "").strip() or goal[:30])[:40],
               "goal": goal[:4000],
               "workdir": str(args.get("workdir") or "").strip()
               or settings_mod.default_workdir()}
    task = store.create_task(payload)
    run = store.create_run("orchestration", task["title"], task_id=task["id"])
    store.update_task_status(task["id"], "queued")
    _enqueue({"kind": "orchestration", "run_id": run["id"], "task_id": task["id"]})
    return {"content": "已创建并排队：task_id=%s run_id=%s（可用 get_status 查进度）"
            % (task["id"], run["id"])}


def _tool_get_status(args):
    from core import store
    task = store.get_task(str(args.get("task_id") or "").strip())
    if not task:
        return {"isError": True, "content": "任务不存在"}
    lines = ["任务：%s（%s）" % (task.get("title"), task.get("status"))]
    run = store.latest_run_by_task().get(task["id"])
    if run:
        lines.append("最近运行：%s（%s）" % (run.get("id"), run.get("status")))
        steps = [s for s in (run.get("steps") or []) if isinstance(s, dict)]
        if steps:
            cur = steps[-1]
            lines.append("步骤 %d/%d：%s（%s）" % (len(steps), run.get("total_steps") or len(steps),
                                                  cur.get("title"), cur.get("status")))
        v = run.get("verdict") or {}
        if v.get("overall") is not None:
            lines.append("评分 %.1f（%s）" % (float(v["overall"]),
                                             "达标" if v.get("publishable") else "未达标"))
        if run.get("error"):
            lines.append("错误：%s" % str(run["error"])[:200])
    if task.get("git_state") == "isolated":
        lines.append("⚠ 任务分支待裁决（合并/丢弃）")
    return {"content": "\n".join(lines)}


def _tool_list_recent(args):
    from core import store
    try:
        limit = max(1, min(30, int(args.get("limit") or 10)))
    except (TypeError, ValueError):
        limit = 10
    rows = [{"id": r.get("id"), "title": r.get("title"), "status": r.get("status"),
             "ts": r.get("started_at") or ""} for r in store.list_runs(limit=limit)]
    return {"content": json.dumps(rows, ensure_ascii=False, indent=1)}


def _tool_usage_summary(args):
    from core import usage
    today, month = usage.cost_snapshot()
    tokens = sum(int(r.get("total") or 0) for r in usage._iter_records(31))
    return {"content": "今日花费 ¥%.2f\n本月花费 ¥%.2f\n近 31 天 tokens：%d（未标定单价的调用不计钱）"
            % (today, month, tokens)}


def _tool_bench(args):
    from core import evalbench
    try:
        top = max(1, min(10, int(args.get("top") or 5)))
    except (TypeError, ValueError):
        top = 5
    board = evalbench.state().get("leaderboard") or []
    rows = [{"rank": r["rank"], "model": r["model"], "overall": r["overall"],
             "provider": r.get("provider_name")} for r in board[:top]]
    return {"content": json.dumps(rows, ensure_ascii=False, indent=1)}


_TOOL_IMPL = {"create_task": _tool_create_task, "get_status": _tool_get_status,
              "list_recent": _tool_list_recent, "usage_summary": _tool_usage_summary,
              "bench_leaderboard": _tool_bench}


def handle_request(msg):
    """处理一条客户端请求，返回响应 dict（通知返回 None）。"""
    if not isinstance(msg, dict) or msg.get("method") is None:
        return None
    rid = msg.get("id")
    method = str(msg["method"])
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO}}
    if method.startswith("notifications/"):
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = str(params.get("name") or "")
        fn = _TOOL_IMPL.get(name)
        if fn is None:
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "isError": True, "content": "未知工具: %s" % name}}
        try:
            out = fn(params.get("arguments") or {})
        except Exception as e:
            out = {"isError": True, "content": "工具执行失败: %s" % e}
        return {"jsonrpc": "2.0", "id": rid, "result": out}
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": "no such method: %s" % method}}


def main():
    for raw in sys.stdin:
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        resp = handle_request(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
