# -*- coding: utf-8 -*-
"""撤回未下达指令 · 真实 HTTP 端到端：临时数据目录 + 独立端口，不碰真实 data/ 与 8765。

流程：起服务 → 提交 mock 任务跑到终态 → 对该 run 追加两条指令（验证 id 不撞号）
→ 撤回第一条 → GET 校验只剩第二条 → 撤回不存在 id 得 400。
（已送达拒撤路径由 test_direct_steering.TestRetractMessage 覆盖——终态 run
不会再 drain，e2e 里造不出 consumed=True 的竞态安全场景。）
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 18877
PY = sys.executable

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (("　— " + str(detail)[:200]) if (detail and not cond) else ""))


def req(method, path, payload=None):
    import http.client
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        try:
            return resp.status, json.loads(raw or "{}")
        except Exception:
            return resp.status, {"raw": raw[:200]}
    finally:
        conn.close()


def wait_run(run_id, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, d = req("GET", "/api/runs/" + run_id)
        if st == 200 and d["run"]["status"] in ("done", "failed", "cancelled"):
            return d["run"]
        time.sleep(0.5)
    return None


def main():
    tmp = Path(tempfile.mkdtemp(prefix="cb-retract-e2e-"))
    data_dir = tmp / "data"
    work = tmp / "work"
    work.mkdir()
    env = dict(os.environ, TUTTI_DATA=str(data_dir), PYTHONPATH=str(ROOT))
    # launch/绑定同步会直写 ~/.claude 等真实 CLI 配置（2026-09-22 a.test 假
    # 供应商毒入真 settings.json 案）——测试服务主目录整体指向假 home
    env["USERPROFILE"] = env["HOME"] = tempfile.mkdtemp(prefix="tutti-home-")
    proc = subprocess.Popen([PY, "-X", "utf8", str(ROOT / "app" / "main.py"),
                             "--port", str(PORT), "--no-browser", "--host", "127.0.0.1"],
                            env=env, cwd=str(ROOT),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        up = False
        for _ in range(40):
            try:
                st, d = req("GET", "/api/state")
                if st == 200:
                    up = True
                    break
            except Exception:
                pass
            time.sleep(0.5)
        check("服务启动", up)
        if not up:
            return

        # 提交 mock 任务并等其跑到终态——终态 run 不会再 drain，撤回窗口稳定
        st, d = req("POST", "/api/tasks", {
            "title": "撤回端到端", "type": "code", "goal": "输出 hello",
            "workdir": str(work)})
        check("创建任务", st == 200 and d.get("run_id"), d)
        rid = d.get("run_id")
        run = wait_run(rid)
        check("mock 任务跑到终态", run is not None, (run or {}).get("status"))
        if not run:
            return

        # 追加两条指令：id 必须不同（撤回删除后 len+1 会撞号——回归点）
        st, d = req("POST", "/api/runs/%s/messages" % rid, {"text": "要撤回的指令"})
        check("追加指令一", st == 200 and d.get("ok"), d)
        mid1 = (d.get("message") or {}).get("id")
        st, d = req("POST", "/api/runs/%s/messages" % rid, {"text": "保留的指令"})
        mid2 = (d.get("message") or {}).get("id")
        check("两条 id 不撞号", mid1 and mid2 and mid1 != mid2, [mid1, mid2])

        # 撤回第一条
        st, d = req("POST", "/api/runs/%s/messages/retract" % rid, {"id": mid1})
        check("撤回未下达指令成功", st == 200 and d.get("ok"), d)
        st, d = req("GET", "/api/runs/" + rid)
        msgs = (d.get("run") or {}).get("messages") or []
        check("信箱只剩第二条", [m["id"] for m in msgs] == [mid2],
              [m["id"] for m in msgs])

        # 重复撤回（已不在箱）→ 400 带原因
        st, d = req("POST", "/api/runs/%s/messages/retract" % rid, {"id": mid1})
        check("重复撤回被拒", st == 400 and "不在信箱" in (d.get("error") or ""), d)

        # 空 id → 400
        st, d = req("POST", "/api/runs/%s/messages/retract" % rid, {})
        check("空 id 被拒", st == 400 and "id" in (d.get("error") or ""), d)

        # 不存在的 run → 400（store 层返回运行不存在）
        st, d = req("POST", "/api/runs/r-nope/messages/retract", {"id": "000001"})
        check("不存在 run 被拒", st == 400, (st, d))

        # 落盘核验：run.json 里也只有保留的那条
        disk = json.loads((data_dir / "runs" / rid / "run.json")
                          .read_text(encoding="utf-8"))
        check("落盘同步删除", [m["id"] for m in disk.get("messages") or []] == [mid2],
              [m.get("id") for m in disk.get("messages") or []])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("FAILED:", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()
