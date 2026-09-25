# -*- coding: utf-8 -*-
"""用量条实时化探针（临时数据目录 + 独立端口，不碰真实 data/ 与 8765）。

钉住「左下角用量条实时跟进」的三道防线：
  1. usage.record 落账 → SSE 推轻量 `event: usage`（不推全量状态）；
  2. /api/usage 聚合立即反映新记录（summary 与 _iter_records 文件缓存一致性）；
  3. 状态 data 推送与空闲心跳不受影响（不回归既有 SSE 行为）。

触发记账的通道：catalog.json 预置一个假 CLI（fixtures_fake_cli.py +
TUTTI_TEST_SELFCONFIG 过死链闸门），服务内以真实（非 mock）智能体跑任务，
步骤收尾必经 _record_usage。请求全部发往硬编码的本机环回地址 127.0.0.1
（http.client 连接目标为字面量）。
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("TUTTI_TEST_PORT") or 19048)
PY = sys.executable
FAKE_CLI = str(ROOT / "tests" / "fixtures_fake_cli.py")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " + name) if cond else ("  ✗ " + name + ("　— " + str(detail)[:200] if detail else "")))


def sse_read(resp, buf, deadline, want_bytes=1):
    """从 SSE 响应读到 deadline（连接超时必须大于最大空闲间隔，见 initial 探针）。"""
    while time.time() < deadline:
        try:
            chunk = resp.read1(65536)
        except Exception:
            return False
        if not chunk:
            return False
        buf.extend(chunk)
        if len(buf) >= want_bytes:
            return True
    return True


def req(path, payload=None, timeout=30):
    import http.client
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=timeout)
    try:
        conn.request("POST" if body is not None else "GET", path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        try:
            return resp.status, json.loads(raw or "{}")
        except Exception:
            return resp.status, {"raw": raw[:200]}
    finally:
        conn.close()


def main():
    tmp = Path(tempfile.mkdtemp(prefix="tutti-sse-usage-"))
    data_dir = tmp / "data"
    work = tmp / "work"
    work.mkdir()
    # 假 CLI 条目：detect.cli=本解释器（绝对路径 shutil.which 判已安装），
    # default_enabled 使其进入 effective_agents；env 过死链闸门
    catalog = [{"id": "fakecli", "name": "Fake CLI", "cli_group": "installable",
                "detect": {"cli": PY},
                "orch": {"kind": "generic", "command": PY,
                         "argv_template": [FAKE_CLI, "{prompt}"],
                         "env": {"TUTTI_TEST_SELFCONFIG": "1"}},
                "default_enabled": True}]
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ, TUTTI_DATA=str(data_dir), PYTHONPATH=str(ROOT))
    # 写接口/绑定同步会直写 ~/.claude 等真实 CLI 配置——测试服务主目录指向假 home
    env["USERPROFILE"] = env["HOME"] = tempfile.mkdtemp(prefix="tutti-home-")
    proc = subprocess.Popen([PY, "-X", "utf8", str(ROOT / "app" / "main.py"),
                             "--port", str(PORT), "--no-browser", "--host", "127.0.0.1"],
                            env=env, cwd=str(ROOT),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    conn = None
    try:
        up = False
        for _ in range(40):
            try:
                st, _d = req("/api/state", timeout=5)
                if st == 200:
                    up = True
                    break
            except Exception:
                pass
            time.sleep(0.5)
        check("服务启动（临时数据目录）", up)

        # ---- 1) SSE 连接 + 初始推送 ----
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=60)
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        buf = bytearray()
        sse_read(resp, buf, time.time() + 5, want_bytes=8)
        text = buf.decode("utf-8", "replace")
        check("连接即推初始全量状态", text.startswith("data: ") and '"tasks"' in text.split("\n", 1)[0],
              text[:120])

        # ---- 2) 建任务（假 CLI 真实步骤）→ 必须收到 event: usage ----
        st, d = req("/api/tasks", {"type": "code", "goal": "用量条实时化探针",
                                   "workdir": str(work), "mode": "auto"})
        check("任务受理", st == 200 and d.get("run_id"), d)
        run_id = d.get("run_id") or ""

        mark = len(buf)
        got_usage_event = False
        data_pushes = 0
        deadline = time.time() + 30
        while time.time() < deadline:
            sse_read(resp, buf, deadline, want_bytes=mark + 1)
            new = buf[mark:].decode("utf-8", "replace")
            got_usage_event = "event: usage" in new
            data_pushes = new.count("data: ")
            if got_usage_event:
                break
            time.sleep(0.1)
        check("记账后收到 event: usage 轻量事件", got_usage_event,
              buf[mark:][:200].decode("utf-8", "replace"))

        # ---- 3) /api/usage 聚合立即反映新记录 ----
        # 等 run 落终态（假 CLI 很快；30s 兜底防假 CLI 卡死拖死探针）
        t0 = time.time()
        while time.time() - t0 < 30:
            st2, d2 = req("/api/runs/" + run_id, timeout=10)
            if st2 == 200 and (d2.get("run") or {}).get("status") in ("done", "failed", "cancelled"):
                break
            time.sleep(0.3)
        st3, d3 = req("/api/usage?days=30", timeout=15)
        today = time.strftime("%Y-%m-%d")
        day = next((x for x in (d3.get("by_day") or []) if x.get("day") == today), None)
        check("台账聚合反映新记录（今日 calls≥1）",
              st3 == 200 and day and int(day.get("calls") or 0) >= 1, d3)

        # ---- 4) 不回归：状态 data 推送仍在（控制权/状态变化路径） ----
        check("状态 data 推送未回归", data_pushes > 0, "data 推送数=%d" % data_pushes)

    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        # 端口归还自证（按句柄 terminate，不按映像名连坐）
        freed = False
        for _ in range(10):
            s = socket.socket()
            try:
                s.connect(("127.0.0.1", PORT))
                s.close()
                time.sleep(0.3)
            except Exception:
                freed = True
                s.close()
                break
            finally:
                s.close()
        check("测试服务已退出、端口已归还", freed)
        if FAIL:
            print("（失败现场保留：%s）" % tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n===== SSE 用量探针：%d 通过 / %d 失败 =====" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()
