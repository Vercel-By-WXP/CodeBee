# -*- coding: utf-8 -*-
"""一键打开端到端：临时数据目录 + 独立端口 18801，不碰真实 data/ 与 8765。

流程：预写带自定义条目的 catalog.json → 起服务 → 校验 catalog 视图带 launch →
未配置 launch 的条目 400 → web 类条目 launch（open=false 免开浏览器）→
端口被占位服务监听 → 重复 launch 走「已在运行」复用路径 → 收尾。
请求全部发往硬编码的本机环回地址 127.0.0.1（http.client 连接目标为字面量）。
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 18811
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


def port_open(port, timeout=0.5):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def main():
    tmp = Path(tempfile.mkdtemp(prefix="tutti-launch-e2e-"))
    data_dir = tmp / "data"
    data_dir.mkdir(parents=True)
    work_port = 18812

    # 占位"web 服务"：监听端口，接受一个连接（就绪探测的 connect）后即退出，
    # 保证测试收尾后机器上不留常驻进程
    # 占位"web 服务"：监听端口、循环接受连接（就绪探测的 connect 每次都成功）；
    # 另绑一个停止端口（work_port+1），收到连接即退出 —— 测试结束后机器上不留常驻进程
    server_py = tmp / "serve_once.py"
    server_py.write_text(
        "import os, socket, sys, threading\n"
        "def guard():\n"
        "    g = socket.socket()\n"
        "    g.bind(('127.0.0.1', int(sys.argv[2])))\n"
        "    g.listen(1)\n"
        "    g.accept()\n"
        "    os._exit(0)\n"
        "threading.Thread(target=guard, daemon=True).start()\n"
        "s = socket.socket()\n"
        "s.bind(('127.0.0.1', int(sys.argv[1])))\n"
        "s.listen(5)\n"
        "while True:\n"
        "    conn, _ = s.accept()\n"
        "    conn.close()\n", encoding="utf-8")

    # catalog 只放测试条目：一个 web 类（指向占位服务）、一个没配 launch 的。
    # 注意 cmd /c 的引号剥离规则：整串引号超过一对时会剥掉首尾引号把命令弄碎，
    # 因此路径无空格就不加引号（与 catalog install/upgrade 命令串同一约束）
    def q(p):
        s = str(p)
        return '"%s"' % s if " " in s else s

    cmd = "%s %s %d %d" % (q(PY), q(server_py), work_port, work_port + 1)
    (data_dir / "catalog.json").write_text(json.dumps([
        {"id": "fake-web", "name": "Fake Web", "detect": {"cli": "python"},
         "orch": None, "install": "", "upgrade": "",
         "launch": {"kind": "web", "command": cmd, "port": work_port}},
        {"id": "fake-plain", "name": "Fake Plain", "detect": {"cli": "python"},
         "orch": None, "install": "", "upgrade": ""},
    ], ensure_ascii=False, indent=2), encoding="utf-8")

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
                st, _d = req("GET", "/api/state")
                if st == 200:
                    up = True
                    break
            except Exception:
                pass
            time.sleep(0.5)
        check("服务启动（临时数据目录 %d）" % PORT, up)

        st, d = req("GET", "/api/catalog")
        view = {c["id"]: c for c in d.get("catalog", [])}
        check("catalog 视图带 launch 字段",
              st == 200 and view.get("fake-web", {}).get("launch", {}).get("kind") == "web", d)

        st, d = req("POST", "/api/catalog/fake-plain/launch", {"open": False})
        check("未配置 launch → 400", st == 400 and "launch" in d.get("error", ""), d)

        st, d = req("POST", "/api/catalog/fake-web/launch", {"open": False})
        check("web 类 launch 返回 ok + url",
              st == 200 and d.get("ok") and d.get("url") == "http://127.0.0.1:%d" % work_port, d)
        log_ok = False
        logp = data_dir / "launch" / "fake-web.log"
        for _ in range(10):  # 重定向文件由 cmd 进程创建，比 POST 返回晚几毫秒
            if logp.exists():
                log_ok = True
                break
            time.sleep(0.3)
        check("启动日志已落盘（data/launch/fake-web.log）", log_ok)

        listening = False
        for _ in range(30):
            if port_open(work_port):
                listening = True
                break
            time.sleep(0.3)
        check("占位服务已被拉起并监听端口", listening)

        st, d = req("POST", "/api/catalog/fake-web/launch", {"open": False})
        check("重复 launch 走「已在运行」复用", st == 200 and "已在运行" in d.get("message", ""), d)

        st, d = req("POST", "/api/catalog/no-such/launch", {"open": False})
        check("未知条目 → 404", st == 404, d)
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except Exception:
            proc.kill()
        # 通知占位服务退出（连一下停止端口），再确认无残留监听
        try:
            with socket.create_connection(("127.0.0.1", work_port + 1), timeout=1):
                pass
        except OSError:
            pass
        for _ in range(10):
            if not port_open(work_port):
                break
            time.sleep(0.5)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()
