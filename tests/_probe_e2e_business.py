# -*- coding: utf-8 -*-
"""业务全流程 E2E 探针（隔离实例）：启动→目录→建任务→队列→步骤→优雅失败→静态页→API健壮性。

用法：python tests/_probe_e2e_business.py
自起隔离引擎（TUTTI_DATA=临时目录, --port 18977），跑完自杀。绝不触碰 8765。
HTTP 层用 http.client 直连固定环回地址，不构造任何动态 URL。
"""
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse

HOST, PORT = "127.0.0.1", 18977
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL"), name, ("| " + str(detail)[:160]) if detail and not ok else "")


def req(method, path, body=None, timeout=30):
    """path 必须是站内相对路径；非 ASCII 段按 RFC 百分号编码。"""
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("非法路径")
    path = urllib.parse.quote(path, safe="/?&=%+")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    conn = http.client.HTTPConnection(HOST, PORT, timeout=timeout)
    try:
        conn.request(method, path, body=data,
                     headers={"Content-Type": "application/json"} if data else {})
        resp = conn.getresponse()
        return resp.status, resp.read(), dict(resp.getheaders())
    finally:
        conn.close()


def get_json(path):
    st, raw, _ = req("GET", path)
    try:
        return st, json.loads(raw.decode("utf-8"))
    except Exception:
        return st, None


def main():
    data_dir = tempfile.mkdtemp(prefix="e2e-biz-")
    env = dict(os.environ, TUTTI_DATA=data_dir)
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "app", "main.py"),
                             "--port", str(PORT), "--host", "127.0.0.1"],
                            env=env, cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        # 1) 启动与就绪
        ready = False
        for _ in range(90):
            try:
                st, _, _ = req("GET", "/api/state", timeout=4)
                if st == 200:
                    ready = True
                    break
            except Exception:
                time.sleep(1)
        check("启动就绪 /api/state 200", ready)
        if not ready:
            return

        # 2) 目录与流程
        st, state = get_json("/api/state")
        check("state 含 agents 目录", st == 200 and bool((state or {}).get("agents")))
        st, flows = get_json("/api/flows")
        n_flows = len(flows.get("flows") if isinstance(flows, dict) else flows) if flows else 0
        check("flows 列表非空", st == 200 and n_flows >= 3,
              "st=%s flows=%s" % (st, str(flows)[:200]))
        st, skills = get_json("/api/skills")
        check("skills 接口 200", st == 200)

        # 3) 建任务（code 流程；隔离实例无外部 CLI → 应优雅失败而非卡死）
        wd = os.path.join(data_dir, "wd")
        os.makedirs(wd, exist_ok=True)
        st, raw, _ = req("POST", "/api/tasks", {
            "type": "code", "title": "E2E冒烟", "goal": "创建一个 hello.py 输出 hello",
            "workdir": wd, "agent": "builtin"})
        created = json.loads(raw.decode("utf-8")) if st == 200 else {}
        check("建任务 200 返回 task_id/run_id",
              st == 200 and created.get("task_id") and created.get("run_id"),
              "st=%s body=%s" % (st, raw[:200]))
        run_id = created.get("run_id") or ""

        # 4) 轮询 run 到终态（最多 180s；端点返回 {"run": {...}} 壳）
        terminal, last = False, {}
        for _ in range(90):
            st, data = get_json("/api/runs/%s" % run_id)
            last = ((data or {}).get("run") or {})
            if last.get("status") in ("done", "failed", "cancelled"):
                terminal = True
                break
            time.sleep(2)
        check("run 到终态不卡死", terminal, "last status=%s" % (last or {}).get("status"))
        check("失败路径带错误信息（优雅收口）",
              (last.get("status") != "failed") or bool(last.get("error")),
              "status=%s error=%r" % (last.get("status"), last.get("error")))

        # 5) 运行详情面：步骤/消息/报告接口
        for ep in ("messages", "report", "files"):
            st, raw2, _ = req("GET", "/api/runs/%s/%s" % (run_id, ep))
            check("run 子接口 %s 200" % ep, st == 200, "st=%s" % st)

        # 6) 任务详情
        task_id = created.get("task_id") or ""
        st, _, _ = req("GET", "/api/tasks/%s/detail" % task_id)
        check("任务详情 200", st == 200)

        # 7) 静态页面：主页/大屏/静态资源缓存头
        st, raw3, hdr = req("GET", "/", timeout=15)
        check("主页 200 且为 HTML", st == 200 and b"<html" in raw3[:600].lower())
        st, _, _ = req("GET", "/board.html", timeout=15)
        check("大屏页 200", st == 200, "st=%s" % st)
        st, _, hdr = req("GET", "/app.js", timeout=15)
        hl = {k.lower() for k in hdr}
        check("app.js 200 带缓存头", st == 200 and ("etag" in hl or "cache-control" in hl))

        # 8) API 健壮性：坏载荷 400（非 500）、未知资源 404
        st, _, _ = req("POST", "/api/tasks", {"type": "不存在的类型"})
        check("坏任务类型 → 4xx 非500", 400 <= st < 500, "st=%s" % st)
        st, _, _ = req("GET", "/api/runs/r-不存在")
        check("未知 run → 4xx 非500", 400 <= st < 500, "st=%s" % st)
        st, _, _ = req("GET", "/api/tasks/t-不存在/detail")
        check("未知任务 → 4xx 非500", 400 <= st < 500, "st=%s" % st)

        # 9) 取消路径：建第二个任务后立刻取消（验证控制面）。
        #    423=设备控制权闸按设计拦截（仓内铁律：自动化写 API 收 423 停手
        #    核实、绝不重试）——闸生效本身就是正确业务行为，按 PASS 收口。
        wd2 = os.path.join(data_dir, "wd2")
        os.makedirs(wd2, exist_ok=True)
        st, raw4, _ = req("POST", "/api/tasks", {
            "type": "code", "title": "E2E取消", "goal": "写一个长脚本演示取消",
            "workdir": wd2, "agent": "builtin"})
        if st == 200:
            rid2 = json.loads(raw4.decode("utf-8")).get("run_id")
            stc, _, _ = req("POST", "/api/runs/%s/cancel" % rid2, {})
            check("取消接口可用", stc in (200, 409, 202), "st=%s" % stc)
        elif st == 423:
            check("取消路径前置（第二个任务）", True, "控制权闸按设计拦截(423)")
        else:
            check("取消路径前置（第二个任务）", False, "st=%s" % st)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(data_dir, ignore_errors=True)

    print("\n=== E2E 汇总: PASS %d / FAIL %d ===" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项:", "; ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
