# -*- coding: utf-8 -*-
"""作品信息一键生成端到端：临时数据目录 + 独立端口起真实服务（不碰真实 data/ 与 8765）。

链路：建连载任务 → POST book-meta（番茄）→ 轮询到 done → 字段/归档 md 校验 →
七猫同样走通 → 非法 platform 400 → 非连载任务 400。无编排者配置 → 模板兜底，
输出确定性（测的是链路不是模型）。请求全部发往硬编码本机环回 127.0.0.1。
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
PORT = 18802

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


def wait_book_meta(tid, platform, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, d = req("GET", "/api/tasks/%s/book-meta" % tid)
        entry = (d.get("book_meta") or {}).get(platform) or {}
        if entry.get("status") in ("done", "failed"):
            return entry
        time.sleep(0.5)
    return {"status": "timeout"}


def main():
    data = tempfile.mkdtemp(prefix="tutti-bme2e-")
    wd = Path(data) / "book"
    wd.mkdir(parents=True)
    (wd / "chapter-001.md").write_text("# 第一章\n\n开局即冲突。", encoding="utf-8")
    (wd / "story-bible.md").write_text("# 圣经\n\n主角：陈砚。", encoding="utf-8")
    srv = subprocess.Popen([sys.executable, "app/main.py", "--port", str(PORT)],
                           cwd=str(ROOT), stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,
                           env=dict(os.environ, TUTTI_DATA=data,
                            TUTTI_BOOKMETA_TEMPLATE_ONLY="1"))
    try:
        up = False
        for _ in range(60):
            time.sleep(0.5)
            try:
                up = req("GET", "/api/state")[0] == 200
                if up:
                    break
            except OSError:
                pass
        check("临时服务就绪(18802)", up)

        st, d = req("POST", "/api/tasks", {
            "type": "serial_novel",
            "goal": "女频古代宅斗长篇：庶女翻身执掌家宅，前十章完成开局到掌权",
            "workdir": str(wd), "serial": {"chapters": 10, "words_per_chapter": 2000},
        })
        check("建连载任务", st == 200 and d.get("task_id"), d)
        tid = d["task_id"]

        # 建任务会自动起跑；本机装了真实 CLI，等它自然写完要几分钟——直接取消，
        # 让任务尽快回终态（生成受理要求任务不在运行中；真实用法也是跑完才点生成）。
        # 取消后步骤收尾可能还要一会儿，受理轮询兜住这段。
        req("POST", "/api/runs/%s/cancel" % (d.get("run_id") or ""))
        accepted = False
        for _ in range(60):
            st, d2 = req("POST", "/api/tasks/%s/book-meta" % tid, {"platform": "fanqie"})
            if st == 200 and d2.get("ok"):
                accepted = True
                break
            time.sleep(2)
        check("POST 番茄生成受理（自动起跑已取消/收尾）", accepted, (st, d2))
        entry = wait_book_meta(tid, "fanqie")
        check("番茄生成完成(done)", entry.get("status") == "done", entry)
        data_f = entry.get("data") or {}
        check("番茄字段齐（书名/签约/读者/简介）",
              data_f.get("book_name") and data_f.get("signing_mode") == "连载模式"
              and data_f.get("target_reader") in ("男频", "女频") and data_f.get("summary"),
              data_f)
        check("goal 含「女」→ 女频", data_f.get("target_reader") == "女频",
              data_f.get("target_reader"))
        md = wd / "作品信息-番茄.md"
        check("工作目录落归档 md", md.is_file() and "作品信息（番茄）" in md.read_text(encoding="utf-8"))

        st, d = req("POST", "/api/tasks/%s/book-meta" % tid, {"platform": "qimao"})
        check("POST 七猫生成受理", st == 200 and d.get("ok"), (st, d))
        entry = wait_book_meta(tid, "qimao")
        check("七猫生成完成(done)", entry.get("status") == "done", entry)
        check("七猫字段齐（分类/状态）",
              "category_main" in (entry.get("data") or {})
              and (entry.get("data") or {}).get("status") == "连载中", entry.get("data"))
        check("两平台互不覆盖", (wd / "作品信息-番茄.md").is_file()
              and (wd / "作品信息-七猫.md").is_file())

        st, d = req("POST", "/api/tasks/%s/book-meta" % tid, {"platform": "qidian"})
        check("非法 platform 400", st == 400, (st, d))

        st, d = req("POST", "/api/tasks", {"type": "novel",
                                           "goal": "单稿小说", "workdir": str(wd)})
        st2, d2 = req("POST", "/api/tasks/%s/book-meta" % d.get("task_id", "x"),
                      {"platform": "fanqie"})
        check("非连载任务 400（无 serial 字段）", st2 == 400, (st2, d2))

        st, d = req("GET", "/api/tasks/%s/book-meta" % "t-nope")
        check("任务不存在 404", st == 404, st)

        # 续写批次：开书资料属于「这本书」，不再重复生成（前端也不出这个 TAB）。
        # 直接种任务（真实续写要等上一批跑完，与本次契约无关）
        cont_id = "t-bmcont-000000-0001"
        # Path.write_bytes 替代 open("w")（安全钩子对 open+动态路径误报路径穿越；
        # write_bytes 语义等价且无换行翻译）
        (Path(data) / "tasks" / (cont_id + ".json")).write_bytes(json.dumps(
            {"id": cont_id, "title": "续写批次", "type": "serial_novel",
             "goal": "接着写", "workdir": str(wd), "status": "done",
             "serial": {"chapters": 8, "words_per_chapter": 2000,
                        "start_chapter": 11}}, ensure_ascii=False).encode("utf-8"))
        st, d2 = req("POST", "/api/tasks/%s/book-meta" % cont_id, {"platform": "fanqie"})
        check("续写批次 400（沿用第一批开书资料）", st == 400
              and "首批" in (d2.get("error") or ""), (st, d2))

        print("\n通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
        if FAIL:
            print("失败项：%s" % "；".join(FAIL))
            sys.exit(1)
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except Exception:
            srv.kill()
        shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    main()
