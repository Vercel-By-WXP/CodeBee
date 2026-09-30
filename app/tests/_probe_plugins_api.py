# -*- coding: utf-8 -*-
"""本地插件 HTTP 路由打靶探针：/api/plugins 四操作全链路（隔离数据目录）。

复现口径：前端 loadPlugins 拿不到 200 就显示「插件加载失败」——本探针
验证 GET/POST 路由真实接通（2026-09-30 前路由缺失，接口 404）。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import http.client

ROOT = Path(__file__).resolve().parents[2]
PORT = int(os.environ.get("PROBE_PORT", "18977"))
FAILS = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (" | %s" % (extra,) if extra else ""))
    if not ok:
        FAILS.append(name)


def req(method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
    payload = json.dumps(body) if body is not None else None
    conn.request(method, path, payload, {"Content-Type": "application/json"})
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", "replace")
    conn.close()
    try:
        return resp.status, json.loads(raw) if raw else {}
    except ValueError:
        return resp.status, {"_raw": raw[:200]}


def main():
    tmp = Path(tempfile.mkdtemp(prefix="probe-plugins-"))
    env = dict(os.environ, TUTTI_DATA=str(tmp / "data"),
               PYTHONPATH=str(ROOT), USERPROFILE=str(tmp),
               HOME=str(tmp), TUTTI_TEST_SELFCONFIG="1")
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(ROOT / "app" / "main.py"),
         "--port", str(PORT), "--no-browser", "--host", "127.0.0.1"],
        env=env, cwd=str(ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        up = False
        for _ in range(60):
            try:
                st, _d = req("GET", "/api/state")
                if st == 200:
                    up = True
                    break
            except Exception:
                pass
            time.sleep(0.5)
        check("服务启动（临时数据目录 %s）" % tmp, up)
        if not up:
            return

        st, d = req("GET", "/api/plugins")
        check("GET /api/plugins 200", st == 200, st)
        rows = d.get("plugins") if st == 200 else []
        ids = [x.get("id") for x in rows or []]
        check("发现仓内样例插件 code-review-helper",
              "code-review-helper" in ids, ids)
        row = next((x for x in rows or [] if x.get("id") == "code-review-helper"), {})
        check("样例插件 state=available", row.get("state") == "available",
              row.get("state"))
        check("total 口径一致", d.get("total") == len(rows or []),
              (d.get("total"), len(rows or [])))

        st, d = req("POST", "/api/plugins/code-review-helper/install")
        check("install 200 且 ok", st == 200 and d.get("ok") is True, (st, d))
        check("技能包计数=1", d.get("skill_count") == 1, d.get("skill_count"))

        st, d = req("GET", "/api/plugins")
        row = next((x for x in d.get("plugins") or []
                    if x.get("id") == "code-review-helper"), {})
        check("装后 installed=true state=disabled",
              row.get("installed") is True and row.get("state") == "disabled",
              (row.get("installed"), row.get("state")))

        st, d = req("POST", "/api/plugins/code-review-helper/enable")
        check("enable 200", st == 200 and d.get("ok") is True, (st, d))
        st, d = req("GET", "/api/plugins")
        row = next((x for x in d.get("plugins") or []
                    if x.get("id") == "code-review-helper"), {})
        check("启后 state=enabled", row.get("state") == "enabled", row.get("state"))

        st, d = req("POST", "/api/plugins/code-review-helper/disable")
        check("disable 200", st == 200 and d.get("ok") is True, (st, d))

        st, d = req("POST", "/api/plugins/code-review-helper/remove")
        check("remove 200", st == 200 and d.get("ok") is True, (st, d))
        st, d = req("GET", "/api/plugins")
        row = next((x for x in d.get("plugins") or []
                    if x.get("id") == "code-review-helper"), {})
        check("卸后回 available", row.get("state") == "available",
              row.get("state"))

        st, d = req("POST", "/api/plugins/no-such-plugin/install")
        check("不存在插件 400 带错误串", st == 400 and d.get("error"),
              (st, d))
        st, d = req("POST", "/api/plugins/no-such-plugin/enable")
        check("未装插件启停 400", st == 400, (st, d))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
        if FAILS and out:
            print("---- service tail ----")
            print("\n".join(out.splitlines()[-40:]))
        print("SUMMARY %s (%d fail)" % ("OK" if not FAILS else "BAD", len(FAILS)))
        sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
