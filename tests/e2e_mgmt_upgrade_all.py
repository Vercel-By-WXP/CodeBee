# -*- coding: utf-8 -*-
"""「一键升级全部」e2e：真实服务 + 临时数据目录（不碰真实 data/ 与 8765）。

种三个假条目验证选条与接线：
- fakeA：已安装（detect.cli=ping）+ 配了慢升级命令 → 必须被批量受理；
- fakeB：未安装 → 不收（一键升级不做首次安装）；
- fakeC：已安装但只配 install 没配 upgrade → 不收（收了只会起必败 run）。
再验证：受理中的条目第二次点一键升级要落进 busy（同条目去重闸共用），
跑完后第三次点击能起全新 run。请求全部发往字面量 127.0.0.1。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 18942
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
    tmp = Path(tempfile.mkdtemp(prefix="tutti-upall-"))
    data_dir = tmp / "data"
    data_dir.mkdir(parents=True)
    # fakeA 升级命令约 5s，保证第二次点击落在第一次的运行窗口内
    (data_dir / "catalog.json").write_text(json.dumps([
        {"id": "fakeA", "name": "FakeA", "cli_group": "installable",
         "detect": {"cli": "ping"},
         "install": "ping -n 1 127.0.0.1", "upgrade": "ping -n 6 127.0.0.1",
         "default_enabled": False},
        {"id": "fakeB", "name": "FakeB", "cli_group": "installable",
         "detect": {"cli": "nope-nowhere-xyz"},
         "install": "ping -n 1 127.0.0.1", "upgrade": "ping -n 1 127.0.0.1",
         "default_enabled": False},
        {"id": "fakeC", "name": "FakeC", "cli_group": "installable",
         "detect": {"cli": "ping"},
         "install": "ping -n 1 127.0.0.1",
         "default_enabled": False},
    ], ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ, TUTTI_DATA=str(data_dir), PYTHONPATH=str(ROOT),
               # 只加载种盘的 catalog.json：默认合并会把真实 CLI 条目带进来，
               # 而本机这些 CLI 是真装的——一键升级会把真机升级真跑一遍
               # （2026-09-26 首跑实弹案：codex 被真升 0.157.1）
               TUTTI_TEST_NO_DEFAULT_CATALOG="1")
    # launch/绑定同步会直写 ~/.claude 等真实 CLI 配置——测试服务主目录整体指向假 home
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
        check("服务启动（临时数据目录）", up)

        st1, r1 = req("POST", "/api/catalog/upgrade-all")
        ids1 = sorted(s["id"] for s in (r1.get("started") or []))
        check("第一次受理：只收已安装且配了升级命令的 fakeA",
              st1 == 200 and ids1 == ["fakeA"]
              and not r1.get("busy") and not r1.get("failed"), r1)
        run1 = ((r1.get("started") or [{}])[0].get("run_id")) or ""

        st2, r2 = req("POST", "/api/catalog/upgrade-all")
        check("第二次受理：进行中的 fakeA 落 busy 不重复起 run",
              st2 == 200 and not (r2.get("started") or [])
              and (r2.get("busy") or []) == ["FakeA"], r2)

        run = wait_run(run1)
        check("批量升级 run 正常跑完", bool(run) and run["status"] == "done",
              run and run.get("summary"))
        check("run 归属正确（entry_id=fakeA, op=upgrade）",
              bool(run) and run.get("entry_id") == "fakeA"
              and run.get("op") == "upgrade", run and (run.get("entry_id"), run.get("op")))

        st3, r3 = req("POST", "/api/catalog/upgrade-all")
        ids3 = [s["id"] for s in (r3.get("started") or [])]
        rid3 = ((r3.get("started") or [{}])[0].get("run_id")) or ""
        check("跑完后第三次受理起新 run（fakeA 渠道 unsupported 不被缓存挡）",
              st3 == 200 and ids3 == ["fakeA"] and rid3 not in (None, "", run1), r3)
        run3 = wait_run(rid3)
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
