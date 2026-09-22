# -*- coding: utf-8 -*-
"""SSE 即时性端到端探针（2026-09-22 侧栏假在跑案回归）。

验证链路：POST cancel → jobs.cancel → store.update_run → bump_state()
→ /api/events 版本号变化 → SSE 帧到达。cancel 链路里没有其他 bump，
「取消后收到 cancelled 快照」只能由 update_run 的 bump 触发——修之前
这条链是断的（SSE 存活的前端不轮询，侧栏冻在旧快照）。

环境纪律：临时数据目录 + 假 home + 独立端口，绝不碰 8765 与真实 data/；
http.client 连接目标写死 127.0.0.1 字面量；跑完 terminate 服务进程并
确认端口释放。
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK] " if cond else "  [NG] ") + name +
          (("  -- " + str(detail)[:200]) if (detail and not cond) else ""))


def pick_port():
    for p in (18933, 18934, 18935, 18936):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            s.close()
    raise RuntimeError("no free port")


def req(port, method, path, payload=None, timeout=10):
    import http.client
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
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


def main():
    tmp = Path(tempfile.mkdtemp(prefix="tutti-sseprobe-"))
    data_dir = tmp / "data"
    (data_dir / "tasks").mkdir(parents=True)
    (data_dir / "runs" / "r-probe0000sse01" / "steps").mkdir(parents=True)
    (data_dir / "work").mkdir()

    # 种子：queued + 未来时间的 resume_enqueue_at（启动清扫视为有意退避，不动它）
    tid, rid = "t-probe0000sse01", "r-probe0000sse01"
    task = {"id": tid, "title": "sse-bump-probe", "goal": "probe",
            "status": "queued", "created_at": "2026-09-22 12:00:00"}
    (data_dir / "tasks" / (tid + ".json")).write_text(
        json.dumps(task, ensure_ascii=False), encoding="utf-8")
    run = {"id": rid, "kind": "orchestration", "title": "sse-bump-probe",
           "task_id": tid, "status": "queued", "steps": [], "messages": [],
           "created_at": "2026-09-22 12:00:00", "started_at": None,
           "ended_at": None, "cost_usd": 0.0, "tokens": 0, "error": "",
           "verdict": None, "summary": "",
           "resume_enqueue_at": "2099-01-01 08:00:00"}
    (data_dir / "runs" / rid / "run.json").write_text(
        json.dumps(run, ensure_ascii=False), encoding="utf-8")

    port = pick_port()
    env = dict(os.environ, TUTTI_DATA=str(data_dir), PYTHONPATH=str(ROOT))
    env["USERPROFILE"] = env["HOME"] = tempfile.mkdtemp(prefix="tutti-home-")
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(ROOT / "app" / "main.py"),
         "--port", str(port), "--no-browser", "--host", "127.0.0.1"],
        env=env, cwd=str(ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    frames = []          # (recv_monotonic, parsed_payload_or_None, raw_head)
    sse_err = []

    def sse_reader():
        import http.client
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
            conn.request("GET", "/api/events")
            resp = conn.getresponse()
            buf = []
            while True:
                line = resp.readline()
                if not line:
                    break
                line = line.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("data: "):
                    buf.append(line[6:])
                elif line == "" and buf:
                    raw = "\n".join(buf)
                    buf = []
                    try:
                        frames.append((time.monotonic(), json.loads(raw), raw[:80]))
                    except Exception:
                        frames.append((time.monotonic(), None, raw[:80]))
        except Exception as e:
            sse_err.append(str(e))

    try:
        up = False
        for _ in range(40):
            try:
                st, _d = req(port, "GET", "/api/state")
                if st == 200:
                    up = True
                    break
            except Exception:
                pass
            time.sleep(0.5)
        check("服务启动（临时数据目录, port=%d）" % port, up)
        if not up:
            return

        threading.Thread(target=sse_reader, daemon=True).start()
        time.sleep(1.5)   # 让 SSE 连接就位（连接初期可能推一帧控制权快照）
        n0 = len(frames)

        st, d = req(port, "POST", "/api/runs/%s/cancel" % rid, payload={})
        t_cancel = time.monotonic()
        check("cancel 请求成功", st == 200 and d.get("ok"), (st, d))

        deadline = t_cancel + 12
        hit = None
        while time.monotonic() < deadline:
            for ts, payload, head in frames:
                if ts <= t_cancel or payload is None:
                    continue
                lr = (payload.get("task_latest") or {}).get(tid) or {}
                if lr.get("status") == "cancelled":
                    hit = (ts, lr)
                    break
            if hit:
                break
            time.sleep(0.2)
        lag = (hit[0] - t_cancel) if hit else -1
        check("取消后 SSE 推送 cancelled 快照（update_run→bump→SSE 全链）",
              hit is not None,
              "frames=%d sse_err=%s" % (len(frames), sse_err[:1]))
        if not hit:
            for i, (ts, payload, head) in enumerate(frames):
                if payload is None:
                    print("    frame%d unparsed: %s" % (i, head))
                    continue
                tl = payload.get("task_latest") or {}
                print("    frame%d t=+%.2fs task_latest=%s" % (
                    i, ts - t_cancel,
                    {k: v.get("status") for k, v in tl.items()}))
                print("      runs=%s" % [(r.get("id"), r.get("status"))
                                         for r in payload.get("runs") or []])
        if hit:
            # 冷启动后首个全量快照构建要几秒（run 扫描/健康/用量聚合），阈值放宽；
            # 关键是不为 0——修复前这条链永远不会推
            check("推送及时到达（<10s，侧栏秒级翻牌）", lag < 10.0, "%.2fs" % lag)

        st, d = req(port, "GET", "/api/runs/%s" % rid)
        check("落盘终态 cancelled", st == 200 and
              d.get("run", {}).get("status") == "cancelled", (st, d))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        time.sleep(1.0)
        s = socket.socket()
        port_free = s.connect_ex(("127.0.0.1", port)) != 0
        s.close()
        check("服务已退出、端口已释放", port_free, port)

    print("")
    print("PASS=%d FAIL=%d" % (len(PASS), len(FAIL)))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
