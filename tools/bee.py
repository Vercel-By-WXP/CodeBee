#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bee — CodeBee 作者侧薄命令行（借 Codeband 的 headless 操作面）。

不用开浏览器就能过一遍裁决队列：看状态、看待裁决、看 diff、采纳或丢弃。
打的是本机 CodeBee HTTP API（默认 127.0.0.1:8765），复用同一套守卫与语义，
不另开一条后门。

用法：
    python tools/bee.py status              任务总览（状态 / 待裁决标记）
    python tools/bee.py pending             只列待裁决任务（含分支与变更数）
    python tools/bee.py diff   <task_id>    打印该任务最新 run 的变更集
    python tools/bee.py approve <task_id>   合并任务分支回原分支（采纳）
    python tools/bee.py discard <task_id>   丢弃任务分支（不可恢复，需 yes）
    python tools/bee.py files  <task_id>    列出最新 run 的成品文件

通用参数：--port 8765  --token xxxx  --force（抢设备控制权）
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import sys

# 注意：主机必须是字面量 "127.0.0.1"（安全沙箱对动态主机名的拦截）
HOST = "127.0.0.1"
CLIENT_ID = os.environ.get("CODEBEE_CLI_CLIENT", "cli-bee")


def _req(args, method, path, body=None, extra_headers=None):
    """一次 HTTP 调用 → (status, dict)。网络错误转成 SystemExit。"""
    headers = {"X-CodeBee-Client": CLIENT_ID}
    if args.token:
        headers["X-CodeBee-Token"] = args.token
    if extra_headers:
        headers.update(extra_headers)
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        conn = http.client.HTTPConnection(HOST, int(args.port), timeout=60)
        conn.request(method, path, payload, headers)
        res = conn.getresponse()
        raw = res.read().decode("utf-8", "replace")
        conn.close()
    except OSError as e:
        sys.exit("连不上 CodeBee（127.0.0.1:%s）：%s\n先启动服务：python app/main.py" % (args.port, e))
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {"_raw": raw}
    return res.status, data


def _acquire(args):
    """写操作需要设备控制权：空闲自动接管；他人持有时按需 force 抢夺。"""
    st, d = _req(args, "POST", "/api/control", {"action": "acquire", "force": bool(args.force)})
    if st == 423:
        sys.exit("控制权在「%s」手上；关掉它的页面，或加 --force 抢夺控制权" % (d.get("control") or {}).get("holder", "?"))
    if st != 200:
        sys.exit("接管控制权失败：HTTP %s %s" % (st, d.get("error") or ""))


def _state(args):
    st, d = _req(args, "GET", "/api/state")
    if st != 200:
        sys.exit("读取状态失败：HTTP %s %s" % (st, d.get("error") or ""))
    return d


def _find_task(state, ref):
    """按 id 或标题定位任务；歧义时报错，绝不猜。"""
    tasks = state.get("tasks") or []
    hits = [t for t in tasks if t.get("id") == ref]
    if not hits:
        hits = [t for t in tasks if (t.get("title") or "") == ref]
    if not hits:
        low = ref.lower()
        hits = [t for t in tasks if low in (t.get("title") or "").lower()]
    if not hits:
        sys.exit("找不到任务 %s（用 status 看列表）" % ref)
    if len(hits) > 1 and hits[0].get("id") != ref:
        sys.exit("「%s」匹配 %d 个任务，请给 task id：\n%s" % (
            ref, len(hits), "\n".join("  %s  %s" % (t["id"], t.get("title")) for t in hits)))
    return hits[0]


def _latest_run(state, task_id):
    lr = (state.get("task_latest") or {}).get(task_id)
    return lr


def cmd_status(args):
    state = _state(args)
    runs = {r.get("id"): r for r in (state.get("runs") or [])}
    rows = []
    for t in (state.get("tasks") or []):
        lr = _latest_run(state, t.get("id")) or runs.get("") or {}
        rows.append([
            t.get("id", "")[:16],
            {"isolated": "⚑待裁决", "merged": "✓已合并", "discarded": "✗已丢弃"}.get(t.get("git_state"), ""),
            lr.get("status") or t.get("status") or "-",
            (lr.get("ended_at") or lr.get("started_at") or "")[5:16],
            (t.get("title") or "")[:40],
        ])
    if not rows:
        print("（还没有任务）")
        return
    print("%-16s %-9s %-10s %-12s %s" % ("TASK", "版本裁决", "状态", "结束", "标题"))
    print("-" * 78)
    for r in rows:
        print("%-16s %-9s %-10s %-12s %s" % tuple(r))
    pend = [r for r in rows if r[1] == "⚑待裁决"]
    print("\n%d 个任务，其中 %d 个待裁决（pending 查看详情）" % (len(rows), len(pend)))


def cmd_pending(args):
    state = _state(args)
    pend = [t for t in (state.get("tasks") or []) if t.get("git_state") == "isolated"]
    if not pend:
        print("没有待裁决的任务分支。")
        return
    for t in pend:
        lr = _latest_run(state, t.get("id")) or {}
        git = lr.get("git") or {}
        files = ((lr.get("changes") or {}).get("files") or [])
        print("%s  %s" % (t["id"], t.get("title") or ""))
        print("   分支 %s → %s ｜ %d 个文件变更 ｜ run %s（%s）" % (
            git.get("branch") or "?", git.get("from_branch") or "?", len(files),
            lr.get("id") or "?", lr.get("status") or "?"))
        for f in files[:8]:
            print("     %s %s" % ({"M": "改", "A": "新", "D": "删", "?": "新", "R": "移"}.get(f.get("status"), f.get("status")), f.get("path")))
        if len(files) > 8:
            print("     …共 %d 个" % len(files))
        if git.get("restore_error"):
            print("   ⚠ 收尾出错：%s" % git["restore_error"])
        print()
    print("approve <task> 采纳（合并回原分支）；discard <task> 丢弃。")


def cmd_diff(args):
    state = _state(args)
    t = _find_task(state, args.task)
    lr = _latest_run(state, t.get("id")) or {}
    diff = ((lr.get("changes") or {}).get("diff") or "").strip()
    if not diff:
        print("（该 run 没有记录变更集——可能未使用代码版本隔离，或 run 还没结束）")
        return
    print(diff)


def cmd_files(args):
    state = _state(args)
    t = _find_task(state, args.task)
    lr = _latest_run(state, t.get("id")) or {}
    if not lr.get("id"):
        print("（该任务还没有运行记录）")
        return
    st, d = _req(args, "GET", "/api/runs/%s/files" % lr["id"])
    if st != 200:
        sys.exit("读取失败：HTTP %s %s" % (st, d.get("error") or ""))
    print("工作目录：%s" % d.get("workdir"))
    for f in (d.get("files") or []):
        print("  %8d  %s" % (f.get("size") or 0, f.get("name")))


def cmd_approve(args):
    state = _state(args)
    t = _find_task(state, args.task)
    lr = _latest_run(state, t.get("id")) or {}
    git = lr.get("git") or {}
    _acquire(args)
    st, d = _req(args, "POST", "/api/tasks/%s/git-merge" % t["id"], {})
    if st != 200:
        sys.exit("合并失败：%s" % (d.get("error") or "HTTP %s" % st))
    print("✅ 已合并 %d 个提交：%s → %s（run %s）" % (
        int(d.get("commits") or 0), git.get("branch") or "任务分支", d.get("merged_into") or "原分支", t["id"]))


def cmd_discard(args):
    state = _state(args)
    t = _find_task(state, args.task)
    lr = _latest_run(state, t.get("id")) or {}
    git = lr.get("git") or {}
    if not args.yes:
        ans = input("丢弃任务分支 %s（run %s）？该分支上的产物永久删除，不可恢复。输入 yes 确认："
                    % (git.get("branch") or t["id"], t["id"])).strip()
        if ans != "yes":
            sys.exit("已取消，未做任何改动。")
    _acquire(args)
    st, d = _req(args, "POST", "/api/tasks/%s/git-discard" % t["id"], {"confirm": True})
    if st != 200:
        sys.exit("丢弃失败：%s" % (d.get("error") or "HTTP %s" % st))
    print("🗑 已丢弃任务分支 %s" % (git.get("branch") or t["id"]))


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="bee", description="CodeBee 作者侧命令行：不过浏览器就能过裁决队列")
    ap.add_argument("--port", default=os.environ.get("CODEBEE_PORT", "8765"), help="服务端口（默认 8765）")
    ap.add_argument("--token", default=os.environ.get("CODEBEE_TOKEN", ""), help="访问令牌（本机默认免令牌）")
    ap.add_argument("--force", action="store_true", help="抢夺设备控制权")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status", help="任务总览")
    sub.add_parser("pending", help="待裁决队列")
    for name, hlp in (("diff", "打印变更集"), ("files", "列出成品文件"),
                      ("approve", "合并任务分支（采纳）"), ("discard", "丢弃任务分支")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("task", help="task id 或标题")
    sub.choices["discard"].add_argument("--yes", action="store_true", help="跳过交互确认")
    args = ap.parse_args(argv)

    if not args.cmd:
        ap.print_help()
        return 0
    handlers = {"status": cmd_status, "pending": cmd_pending, "diff": cmd_diff,
                "files": cmd_files, "approve": cmd_approve, "discard": cmd_discard}
    handlers[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
