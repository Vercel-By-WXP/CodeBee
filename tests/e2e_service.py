# -*- coding: utf-8 -*-
"""第 2 轮自测：真实服务端到端（临时数据目录 + 独立端口，不碰真实 data/ 与 8765）。

流程：起服务 → flows CRUD → 绑定跨厂商链 → 编排者配置 → 并发设置 →
并发提交 3 个 mock 任务（不同类型）→ 验证并行执行与结果 → 落盘检查 → 收尾。
请求全部发往硬编码的本机环回地址 127.0.0.1（http.client 连接目标为字面量）。
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
PORT = 18799
PY = sys.executable

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (("　— " + str(detail)[:200]) if (detail and not cond) else ""))


def req(method, path, payload=None):
    """请求固定发往本机环回地址（http.client 连接目标为字面量 127.0.0.1）。"""
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
    tmp = Path(tempfile.mkdtemp(prefix="tutti-e2e-"))
    data_dir = tmp / "data"
    work = tmp / "work"
    work.mkdir()
    env = dict(os.environ, TUTTI_DATA=str(data_dir), PYTHONPATH=str(ROOT))
    proc = subprocess.Popen([PY, "-X", "utf8", str(ROOT / "app" / "main.py"),
                             "--port", str(PORT), "--no-browser", "--host", "127.0.0.1"],
                            env=env, cwd=str(ROOT),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        # 等服务就绪
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

        # ---- 1) 任务流程：内置 + 自定义 CRUD
        st, d = req("GET", "/api/flows")
        ids = [f["id"] for f in d["flows"]]
        check("内置 6 种任务类型", st == 200 and all(
            x in ids for x in ("code", "novel", "doc", "translation", "research", "speech")), ids)
        st, d = req("POST", "/api/flows", {
            "id": "podcast", "name": "播客脚本", "icon": "🎙", "engine": "review",
            "manuscript": "script.md", "rubric": ["选题", "结构", "口语化"],
            "threshold": 6.5, "rounds": 2, "note": "端到端验证"})
        check("创建自定义流程", st == 200 and d.get("ok"), d)
        st, d = req("GET", "/api/flows")
        check("自定义流程出现在列表", any(f["id"] == "podcast" for f in d["flows"]))
        st, d = req("POST", "/api/flows/podcast/delete")
        check("删除自定义流程", st == 200)
        st, d = req("POST", "/api/flows", {"id": "novel", "name": "冒充", "engine": "review",
                                           "rubric": ["x"]})
        check("内置流程不可覆盖", st == 400, d)

        # ---- 2) 跨厂商模型链：建两厂商 → 绑定 chain → 校验落盘
        req("POST", "/api/models/provider", {
            "name": "E2E厂商A", "protocol": "anthropic",
            "base_url": "https://a.e2e.test/v1", "api_key": "sk-e2e-a" + "x" * 20})
        req("POST", "/api/models/provider", {
            "name": "E2E厂商B", "protocol": "openai",
            "base_url": "https://b.e2e.test/v1", "api_key": "sk-e2e-b" + "y" * 20})
        st, d = req("GET", "/api/models")
        provs = {p["name"]: p["id"] for p in d["providers"]}
        pa, pb = provs["E2E厂商A"], provs["E2E厂商B"]
        st, d = req("POST", "/api/models/binding", {
            "agent_id": "claude-code",
            "chain": [{"provider_id": pa, "model": "claude-x"},
                      {"provider_id": pb, "model": "gpt-y"}]})
        b = d.get("binding") or {}
        check("绑定跨厂商模型链", st == 200 and len(b.get("chain") or []) == 2, b)
        check("链冗余字段同步", b.get("model") == "claude-x"
              and b.get("models") == ["claude-x", "gpt-y"], b)
        disk = json.loads((data_dir / "models.json").read_text(encoding="utf-8"))
        bc = (disk["bindings"].get("claude-code") or {}).get("chain") or []
        check("chain 落盘 models.json", len(bc) == 2
              and {c["provider_id"] for c in bc} == {pa, pb}, bc)

        # ---- 3) 编排者：配置保存 + 校验
        st, d = req("POST", "/api/orchestrator", {
            "provider_id": pa, "model": "orch-planner", "enabled": True})
        o = d.get("orchestrator") or {}
        check("编排者配置保存", st == 200 and o.get("model") == "orch-planner", o)
        check("编排者 ready 判定", o.get("ready") is True, o)
        st, d = req("POST", "/api/orchestrator", {"provider_id": "ghost"})
        check("编排者拒绝不存在的供应商", st == 400)

        # ---- 4) 并发设置
        st, d = req("POST", "/api/settings", {"max_concurrent_jobs": 4})
        check("并发数保存=4", st == 200 and d["settings"]["max_concurrent_jobs"] == 4, d)
        st, d = req("GET", "/api/settings")
        check("并发数读取", d.get("max_concurrent_jobs") == 4, d)

        # ---- 5) 并发提交 3 个不同类型 mock 任务 → 并行执行
        # 先禁用全部真实 CLI（默认目录里部分 CLI 默认参与编排，会让 mock 任务
        # 变成真实模型调用）：保持本测试零配额、可重复。
        st, d = req("GET", "/api/state")
        real_agents = [a["id"] for a in d["agents"] if a.get("mode") == "real"]
        for aid in real_agents:
            req("POST", "/api/orchestration", {"agent_id": aid, "enabled": False})
        st, d = req("GET", "/api/state")
        left = [a["id"] for a in d["agents"] if a.get("mode") == "real"]
        check("真实 CLI 已全部禁用（仅 mock 参与编排）", not left, left)

        req("POST", "/api/flows", {
            "id": "podcast", "name": "播客脚本", "engine": "review",
            "manuscript": "script.md", "rubric": ["选题", "结构"], "threshold": 6.0})
        submissions = []
        for typ, goal, ms in [
                ("code", "e2e 并发代码任务一", None),
                ("podcast", "e2e 并发播客任务二", "script.md"),
                ("doc", "e2e 并发文档任务三", None)]:
            payload = {"type": typ, "goal": goal, "workdir": str(work), "mode": "auto"}
            if ms:
                payload["manuscript"] = ms
            st, d = req("POST", "/api/tasks", payload)
            submissions.append((typ, d.get("run_id"), d))
        check("3 个任务全部受理", all(st == 200 and s[1] for s in submissions), submissions)

        t0 = time.time()
        saw_parallel = False
        while time.time() - t0 < 60:
            st, d = req("GET", "/api/state")
            running = [r for r in d["runs"] if r["status"] == "running"]
            if len(running) >= 2:
                saw_parallel = True   # 至少同时看到 2 个 running
                break
            if all(r["status"] in ("done", "failed")
                   for r in d["runs"] if r["id"] in [s[1] for s in submissions]):
                break
            time.sleep(0.2)
        check("多任务并行执行（同时 ≥2 个 running）", saw_parallel)

        results = []
        for typ, run_id, _ in submissions:
            results.append((typ, wait_run(run_id)))
        check("3 个任务全部完成", all(r and r["status"] == "done" for _, r in results),
              [(t, (r or {}).get("status"), (r or {}).get("error", "")[:80]) for t, r in results])
        by_type = {t: r for t, r in results}
        v = (by_type["code"] or {}).get("verdict") or {}
        check("代码任务 verdict(engine=code)", v.get("engine") == "code"
              and v.get("pass") is True, v)
        v2 = (by_type["podcast"] or {}).get("verdict") or {}
        check("自定义流程 verdict(type=podcast)", v2.get("type") == "podcast"
              and v2.get("engine") == "review", v2)
        check("自定义流程产出文件", (work / "script.md").is_file())
        v3 = (by_type["doc"] or {}).get("verdict") or {}
        check("内置 doc 流程达标", v3.get("publishable") is True
              and set((v3.get("scores") or {})) == {"准确性", "结构清晰", "表达流畅", "实用价值"}, v3)

        # ---- 6) 非法类型被拒
        st, d = req("POST", "/api/tasks", {"type": "nope", "goal": "x", "workdir": str(work)})
        check("未知任务类型 400", st == 400, d)

        # ---- 7) 落盘完整性
        tasks = list((data_dir / "tasks").glob("*.json"))
        runs = list((data_dir / "runs").glob("*/run.json"))
        reports = list((data_dir / "runs").glob("*/report.md"))
        check("任务/运行/报告落盘", len(tasks) == 3 and len(runs) == 3 and len(reports) == 3,
              (len(tasks), len(runs), len(reports)))
        settings_disk = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
        check("settings.json 落盘", settings_disk.get("max_concurrent_jobs") == 4, settings_disk)

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        if FAIL:
            try:
                out, _ = proc.communicate(timeout=5)
                print("---- 服务输出（尾部）----")
                print((out or b"").decode("utf-8", "replace")[-3000:])
            except Exception:
                pass
            print("（失败现场保留：%s）" % tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n===== 第 2 轮（服务端到端）：%d 通过 / %d 失败 =====" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()
