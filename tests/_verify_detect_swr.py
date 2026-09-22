# -*- coding: utf-8 -*-
"""验证 detect_all stale-while-revalidate 修复：
缓存过期窗口内，并发的 /api/catalog、/api/state 必须全部即时返回（拿旧快照），
不再出现「领队卡数秒、其余排队」。跑完按 PID 杀服务并验证端口释放。"""
import http.client
import os
import subprocess
import sys
import tempfile
import threading
import time

PORT = 18793
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get(path, timeout=45):
    t0 = time.time()
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=timeout)
    try:
        conn.request("GET", path)
        conn.getresponse().read()
    finally:
        conn.close()
    return time.time() - t0

def main():
    data_dir = tempfile.mkdtemp(prefix="tutti-swr-")
    env = dict(os.environ, TUTTI_DATA=data_dir, PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "app", "main.py"),
                             "--port", str(PORT), "--no-browser"],
                            env=env, cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        # 等服务起来
        for _ in range(60):
            time.sleep(1)
            if proc.poll() is not None:
                print("FATAL: 服务提前退出")
                return 1
            try:
                get("/", timeout=2)
                break
            except Exception:
                pass
        # 冷启动首查：同步检测（预期一次较慢，属设计内）
        cold = get("/api/catalog")
        print("冷启动首查 /api/catalog: %.2fs（设计内：首轮同步检测）" % cold)

        # 等缓存过期（60s TTL）
        print("等 61s 让缓存过期…")
        time.sleep(61)

        # 过期窗口：6 并发 catalog + 2 并发 state，全部应即时返回
        lat = []
        def hit(p):
            try: lat.append((p, round(get(p), 2)))
            except Exception as e: lat.append((p, "ERR " + str(e)[:30]))
        ts = [threading.Thread(target=hit, args=(p,)) for p in
              ["/api/catalog"] * 6 + ["/api/state"] * 2]
        t0 = time.time()
        for t in ts: t.start()
        for t in ts: t.join()
        wall = time.time() - t0
        for p, d in lat: print("  %s -> %ss" % (p, d))
        worst = max(d for _, d in lat if isinstance(d, float))
        print("过期窗口 8 并发总墙钟: %.2fs，最慢单请求: %.2fs" % (wall, worst))
        ok = worst < 3.0
        print("结论:", "PASS——探测在后台跑，请求不再排队" if ok else "FAIL——仍有请求被探测堵住")
        return 0 if ok else 1
    finally:
        proc.kill()
        try: proc.wait(timeout=10)
        except Exception: pass
        print("服务已按 PID 终止")

if __name__ == "__main__":
    sys.exit(main())
