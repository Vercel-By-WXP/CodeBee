# -*- coding: utf-8 -*-
"""同条目管理操作去重闸 e2e：真实服务 + 临时数据目录（不碰真实 data/ 与 8765）。

种一个安装命令很慢的假条目，连点两次「升级」必须挂到同一个 run（deduped
标记）；跑完后再点第三次要能起全新 run——防同包全局 npm 双开互锁成双僵尸
（2026-09-18 codex 案）复发。请求全部发往字面量 127.0.0.1。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 18941
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


def wait_run(run_id, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, d = req("GET", "/api/runs/" + run_id)
        if st == 200 and d["run"]["status"] in ("done", "failed", "cancelled"):
            return d["run"]
        time.sleep(0.5)
    return None


def main():
    tmp = Path(tempfile.mkdtemp(prefix="tutti-dedup-"))
    data_dir = tmp / "data"
    data_dir.mkdir(parents=True)
    # 假条目：安装/升级=约 2s 的慢命令，保证第二次点击落在第一次的运行窗口内
    (data_dir / "catalog.json").write_text(json.dumps([{
        "id": "slowfake", "name": "SlowFake", "cli_group": "installable",
        "detect": {"cli": "slowfake-nowhere"},
        "install": "ping -n 3 127.0.0.1", "upgrade": "ping -n 3 127.0.0.1",
        "default_enabled": False}], ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ, TUTTI_DATA=str(data_dir), PYTHONPATH=str(ROOT))
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
        check("服务启动（临时数据目录）", up)

        st1, r1 = req("POST", "/api/catalog/slowfake/upgrade")
        check("第一次升级受理", st1 == 200 and r1.get("run_id"), r1)
        st2, r2 = req("POST", "/api/catalog/slowfake/upgrade")
        check("第二次升级去重挂到同一 run", st2 == 200 and r2.get("deduped")
              and r2.get("run_id") == r1.get("run_id"), r2)
        run = wait_run(r1.get("run_id") or "")
        check("第一次升级正常跑完", bool(run) and run["status"] == "done",
              run and run.get("summary"))

        st3, r3 = req("POST", "/api/catalog/slowfake/upgrade")
        check("结束后第三次点击起全新 run", st3 == 200 and not r3.get("deduped")
              and r3.get("run_id") not in (None, r1.get("run_id")), r3)
        run3 = wait_run(r3.get("run_id") or "")
        check("第三次也正常跑完", bool(run3) and run3["status"] == "done")
    finally:
        try:
            proc.kill()
        except Exception:
            pass
    print("\n通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
