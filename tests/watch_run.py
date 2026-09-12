# -*- coding: utf-8 -*-
"""监测一次运行直到结束：打印步骤时间线与结果摘要（请求发往本机环回 8765）。"""
import json
import sys
import time
from http.client import HTTPConnection

PORT = 8765
RUN_ID = sys.argv[1]
TIMEOUT_MIN = float(sys.argv[2]) if len(sys.argv) > 2 else 60


def get(path):
    conn = HTTPConnection("127.0.0.1", PORT, timeout=30)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return json.loads(resp.read().decode("utf-8"))
    finally:
        conn.close()


def main():
    t0 = time.time()
    seen = 0
    while True:
        try:
            run = get("/api/runs/" + RUN_ID)["run"]
        except Exception as e:
            print("poll error:", e)
            time.sleep(5)
            continue
        steps = run.get("steps") or []
        for s in steps[seen:]:
            print("[%s] %02d %-14s %-12s %s" % (
                s.get("ended_at") or s.get("started_at") or "", s["n"], s["role"],
                s["status"], (s.get("summary") or "")[:110]))
            sys.stdout.flush()
        seen = len(steps)
        if run["status"] in ("done", "failed", "cancelled"):
            print("\n== 最终状态：%s ==", run["status"])
            print("summary:", (run.get("summary") or "")[:300])
            if run.get("error"):
                print("error:", run["error"][:500])
            v = run.get("verdict") or {}
            if v:
                print("verdict:", json.dumps({k: v[k] for k in v if k != "route"},
                                             ensure_ascii=False)[:1200])
            return run["status"]
        if (time.time() - t0) > TIMEOUT_MIN * 60:
            print("\n== 监测超时（任务仍在 %s），已打印 %d 步 ==" % (run["status"], seen))
            return run["status"]
        time.sleep(10)


if __name__ == "__main__":
    main()
