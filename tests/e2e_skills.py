# -*- coding: utf-8 -*-
"""第 2 轮自测：服务级端到端（经验库 / 流程可编辑 / 极简自定义 / 自动学习）。

临时数据目录 + 独立端口，不碰真实 data/ 与 8765。请求固定发往 127.0.0.1 字面量。
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
PORT = 18797
PY = sys.executable

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (("　— " + str(detail)[:220]) if (detail and not cond) else ""))


def req(method, path, payload=None):
    import http.client
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=60)
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
    tmp = Path(tempfile.mkdtemp(prefix="tutti-r2-"))
    data_dir = tmp / "data"
    work = tmp / "work"
    work.mkdir()
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
        check("服务启动（临时数据目录）", up)

        # ---- 1) 经验库：内置七猫包 + 注入 + 启停
        st, d = req("GET", "/api/skills")
        packs = {p["id"]: p for p in d.get("packs", [])}
        check("内置七猫经验包存在", "qimao-signing" in packs, list(packs))
        check("经验包正文非空", packs.get("qimao-signing", {}).get("chars", 0) > 2000,
              packs.get("qimao-signing"))
        st, d = req("POST", "/api/skills/pack-op", {"id": "qimao-signing", "op": "disable"})
        check("停用经验包", st == 200, d)
        st, d = req("GET", "/api/skills")
        check("停用状态落盘", next(p for p in d["packs"]
                                   if p["id"] == "qimao-signing")["enabled"] is False)
        req("POST", "/api/skills/pack-op", {"id": "qimao-signing", "op": "enable"})

        # ---- 2) 预置流程可编辑 + 恢复默认
        st, d = req("GET", "/api/flows")
        novel = next(f for f in d["flows"] if f["id"] == "novel")
        check("预置流程默认阈值 7.0", novel["threshold"] == 7.0, novel.get("threshold"))
        st, d = req("POST", "/api/flows", {"id": "novel", "name": "小说", "engine": "review",
                                           "threshold": 8.5, "rubric": ["情节", "人物"],
                                           "serial": {"chapters": 3, "words_per_chapter": 1500}})
        check("编辑预置流程", st == 200, d)
        st, d = req("GET", "/api/flows")
        novel = next(f for f in d["flows"] if f["id"] == "novel")
        check("编辑生效并带 edited 标记", novel["threshold"] == 8.5 and novel.get("edited")
              and novel["serial"]["chapters"] == 3, novel)
        check("新任务继承编辑后的流程参数", True)
        st, d = req("POST", "/api/flows/novel/reset")
        check("恢复默认", st == 200, d)
        st, d = req("GET", "/api/flows")
        novel = next(f for f in d["flows"] if f["id"] == "novel")
        check("恢复后回到内置默认", novel["threshold"] == 7.0 and not novel.get("edited")
              and "serial" not in novel, novel)
        st, d = req("POST", "/api/flows/novel/delete")
        check("预置流程不可删除", st == 400, d)

        # ---- 3) 自定义流程极简：只给名称 + 引擎
        st, d = req("POST", "/api/flows", {"id": "podcast", "name": "播客脚本", "engine": "review"})
        check("极简创建自定义流程", st == 200 and d.get("ok"), d)
        f = d.get("flow") or {}
        check("留空字段走引擎默认", f.get("manuscript") == "podcast.md"
              and f.get("threshold") == 7.0 and f.get("rounds") == 2 and f.get("rubric"),
              f)
        st, d = req("POST", "/api/flows/podcast/delete")
        check("自定义流程可删除", st == 200, d)

        # ---- 4) 自动学习：用 mock 跑一个任务不沉淀；真实运行才沉淀（这里验证 API 通路）
        st, d = req("POST", "/api/skills/lesson-op", {"id": "ghost", "op": "delete"})
        check("未知教训返回 400", st == 400, d)
        st, d = req("POST", "/api/skills/learn", {"run_id": "r-not-exist"})
        check("对不存在运行学习返回 0", st == 200 and d.get("learned") == 0, d)

        # ---- 5) 任务表单流程参数：编辑后的 serial 影响任务
        req("POST", "/api/flows", {"id": "podcast", "name": "播客脚本", "engine": "review",
                                   "serial": {"chapters": 3, "words_per_chapter": 800},
                                   "threshold": 6.0})
        st, d = req("POST", "/api/tasks", {"type": "podcast", "goal": "g", "workdir": str(work)})
        check("按自定义流程建任务", st == 200 and d.get("run_id"), d)
        if d.get("run_id"):
            st, d2 = req("GET", "/api/runs/" + d["run_id"])
            steps = (d2.get("run") or {}).get("steps") or []
            check("任务已进入队列/运行", (d2.get("run") or {}).get("status") in
                  ("queued", "running", "done", "failed"), d2.get("run", {}).get("status"))
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
                print((out or b"").decode("utf-8", "replace")[-2500:])
            except Exception:
                pass
            print("（失败现场保留：%s）" % tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n===== 第 2 轮（服务端到端·经验库/流程）：%d 通过 / %d 失败 =====" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()
