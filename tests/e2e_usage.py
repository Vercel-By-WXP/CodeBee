# -*- coding: utf-8 -*-
"""用量统计端到端：临时数据目录 + 独立端口（不碰真实 data/ 与 8765）。

流程：预置台账 JSONL → 起服务 → GET /api/usage 断言多维聚合 →
days 窗口过滤 → mock 任务跑完不入账 → 台账文件落盘检查。
请求全部发往字面量 127.0.0.1（Mimosa 约束）。
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 18797
PY = sys.executable

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (("　— " + str(detail)[:200]) if (detail and not cond) else ""))


def req(method, path):
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
    try:
        conn.request(method, path)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        try:
            return resp.status, json.loads(raw or "{}")
        except Exception:
            return resp.status, {"raw": raw[:200]}
    finally:
        conn.close()


def seed_record(day, **kw):
    rec = {"ts": day + " 10:00:00", "day": day, "source": "pipeline",
           "run_id": "r-seed", "task_id": "t-seed", "task_type": "code",
           "role": "implement", "agent": "codex-cli", "agent_label": "Codex CLI",
           "tool": "codex", "model": "gpt-seed", "provider": "",
           "ok": True, "duration_s": 3.0, "cost_usd": 0.01,
           "input": 100, "output": 50, "cached": 20, "reasoning": 0, "total": 170}
    rec.update(kw)
    return json.dumps(rec, ensure_ascii=False)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="tutti-usage-"))
    data_dir = tmp / "data"
    work = tmp / "work"
    work.mkdir()
    today = datetime.date.today()
    month = today.strftime("%Y%m")
    usage_dir = data_dir / "usage"
    usage_dir.mkdir(parents=True)
    # 今天 + 昨天 + 40 天前（跨月窗口验证）
    lines = [
        seed_record(today.isoformat()),
        seed_record((today - datetime.timedelta(days=1)).isoformat(),
                    tool="claude", agent="claude-code", model="claude-seed",
                    role="review", ok=False, total=90, input=60, output=30),
        seed_record((today - datetime.timedelta(days=40)).isoformat(),
                    tool="qwen", model="qwen-seed", total=999, input=999, output=0),
    ]
    (usage_dir / ("usage-%s.jsonl" % month)).write_text(
        "\n".join(lines[:2]) + "\n", encoding="utf-8")
    old_month = (today - datetime.timedelta(days=40)).strftime("%Y%m")
    (usage_dir / ("usage-%s.jsonl" % old_month)).write_text(
        lines[2] + "\n", encoding="utf-8")

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

        # ---- 1) 全量聚合
        st, d = req("GET", "/api/usage?days=0")
        t = (d.get("totals") or {})
        check("接口 200 + 结构完整", st == 200 and all(
            k in d for k in ("totals", "by_day", "by_tool", "by_agent", "by_model",
                             "by_role", "by_task_type", "recent")), list(d))
        check("全量 tokens=1259（含 40 天前）", t.get("tokens") == 170 + 90 + 999, t)
        check("调用数与成败", t.get("calls") == 3 and t.get("ok") == 2 and t.get("failed") == 1, t)
        check("缓存细分入账", t.get("cached") == 60, t.get("cached"))
        check("费用合计", abs(t.get("cost_usd", 0) - 0.03) < 1e-6, t.get("cost_usd"))
        tools = {r["key"]: r for r in d["by_tool"]}
        check("按工具维度", set(tools) == {"codex", "claude", "qwen"}, list(tools))
        roles = {r["key"]: r for r in d["by_role"]}
        check("按角色维度", roles.get("implement", {}).get("calls") == 2
              and roles.get("review", {}).get("calls") == 1, list(roles))
        check("按模型维度", {r["key"] for r in d["by_model"]} == {"gpt-seed", "claude-seed", "qwen-seed"})
        check("最近调用含种子记录", len(d["recent"]) == 3, len(d.get("recent", [])))

        # ---- 2) 7 天窗口：40 天前的排除，by_day 连续 7 天补零
        st, d = req("GET", "/api/usage?days=7")
        t = d.get("totals") or {}
        check("7 天窗口排除 40 天前记录", t.get("tokens") == 170 + 90, t)
        check("by_day 连续 7 天", len(d.get("by_day", [])) == 7, len(d.get("by_day", [])))
        by_day = {x["day"]: x["tokens"] for x in d.get("by_day", [])}
        check("按天趋势数值", by_day.get(today.isoformat()) == 170
              and by_day.get((today - datetime.timedelta(days=1)).isoformat()) == 90, by_day)

        # ---- 3) 非法 days 回落 30 天
        st, d = req("GET", "/api/usage?days=abc")
        check("非法 days 回落 30 天", st == 200 and len(d.get("by_day", [])) == 30)

        # ---- 4) mock 任务完整跑一轮 → 不入账（mock 无真实调用）
        st, d = req("GET", "/api/state")
        real = [a["id"] for a in d["agents"] if a.get("mode") == "real"]
        for aid in real:
            import http.client
            body = json.dumps({"agent_id": aid, "enabled": False}).encode("utf-8")
            conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
            conn.request("POST", "/api/orchestration", body=body,
                         headers={"Content-Type": "application/json"})
            conn.getresponse().read()
            conn.close()
        payload = json.dumps({"type": "code", "goal": "usage-e2e mock 任务",
                              "workdir": str(work), "mode": "auto"}).encode("utf-8")
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
        conn.request("POST", "/api/tasks", body=payload,
                     headers={"Content-Type": "application/json"})
        resp = json.loads(conn.getresponse().read().decode("utf-8"))
        conn.close()
        run_id = resp.get("run_id")
        check("mock 任务受理", bool(run_id), resp)
        final = None
        t0 = time.time()
        while time.time() - t0 < 90:
            st, d = req("GET", "/api/runs/" + str(run_id))
            if st == 200 and d["run"]["status"] in ("done", "failed", "cancelled"):
                final = d["run"]
                break
            time.sleep(0.5)
        check("mock 任务跑完", final and final["status"] == "done",
              (final or {}).get("status"))
        st, d = req("GET", "/api/usage?days=0")
        check("mock 调用不入账（calls 仍为 3）", (d["totals"] or {}).get("calls") == 3,
              (d["totals"] or {}).get("calls"))

        # ---- 5) 台账文件保持 append-only 结构
        files = sorted(usage_dir.glob("usage-*.jsonl"))
        n_lines = sum(len(f.read_text(encoding="utf-8").strip().splitlines()) for f in files)
        check("台账文件仍为 3 行（mock 未写）", n_lines == 3, n_lines)

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        if FAIL:
            print("（失败现场保留：%s）" % tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n===== 用量端到端：%d 通过 / %d 失败 =====" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()
