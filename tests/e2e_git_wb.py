# -*- coding: utf-8 -*-
"""GIT 工作台 HTTP 端到端（临时数据目录 + 独立端口 18871，不碰真实 data/ 与 8765）。

流程：起服务 → 种 git 仓库 → 建任务 → GET /api/tasks/<id>/git 全貌 →
stage/commit/checkout/discard/delete 全链路 → diff 端点 → 路径越界拒绝 →
运行中任务 409 守卫。请求全部发往字面量 127.0.0.1。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 18871
PY = sys.executable

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (("　— " + str(detail)[:200]) if (detail and not cond) else ""))


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


def git(*args, **kw):
    """固定连本机 git，cwd 可选（缺省种子仓库 work）。"""
    r = subprocess.run(["git", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60,
                       cwd=kw.get("cwd"))
    return r


def main():
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="tutti-gitwb-"))
    data_dir = tmp / "data"
    work = tmp / "work"
    work.mkdir(parents=True)
    # 种仓库：baseline 提交 + 预建 dev 分支
    git("init", cwd=str(work))
    git("config", "user.name", "Tester", cwd=str(work))
    git("config", "user.email", "t@local", cwd=str(work))
    (work / "README.md").write_text("baseline\n", encoding="utf-8")
    git("add", "-A", cwd=str(work))
    git("commit", "-m", "baseline", cwd=str(work))
    git("branch", "dev", cwd=str(work))

    env = dict(os.environ, TUTTI_DATA=str(data_dir), PYTHONPATH=str(ROOT))
    # 任务直接种盘（status=done）：建任务接口会自动入队一次运行，无智能体时
    # 终态时间不可控（并行负载下曾卡 queued 60s+）；种盘让写操作守卫的
    # 「任务空闲」前置完全确定。
    (data_dir / "tasks").mkdir(parents=True, exist_ok=True)
    tid = "te2egitwb"
    (data_dir / "tasks" / (tid + ".json")).write_text(json.dumps({
        "id": tid, "type": "code", "engine": "code", "title": "工作台任务",
        "goal": "改代码", "workdir": str(work), "status": "done",
        "created_at": "2026-09-16 10:00:00", "attachments": [],
    }, ensure_ascii=False), encoding="utf-8")
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

        # ---- GET git 全貌
        st, d = req("GET", "/api/tasks/%s/git" % tid)
        check("status：repo 且分支 master", st == 200 and d.get("repo") and d.get("branch") == "master", d)
        check("status：隔离态字段在场", "isolation" in d and d["isolation"].get("branch") == "tutti/" + tid, d.get("isolation"))

        # ---- 变更 → 三组分类 → diff
        (work / "README.md").write_text("baseline\n追加一行\n", encoding="utf-8")
        (work / "new.md").write_text("新文件\n", encoding="utf-8")
        st, d = req("GET", "/api/tasks/%s/git" % tid)
        check("status：未暂存 1 + 未跟踪 1",
              [f["path"] for f in d.get("unstaged", [])] == ["README.md"] and
              [f["path"] for f in d.get("untracked", [])] == ["new.md"], d)
        st, d = req("GET", "/api/tasks/%s/git/diff?path=README.md" % tid)
        check("diff：已跟踪文件实时内容", st == 200 and "+追加一行" in d.get("diff", ""), d)
        st, d = req("GET", "/api/tasks/%s/git/diff?path=new.md" % tid)
        check("diff：未跟踪文件拼 new-file 段", st == 200 and "new file mode" in d.get("diff", ""), d)
        st, d = req("GET", "/api/tasks/%s/git/diff?path=../escape" % tid)
        check("diff：越界路径拒绝", st == 400, (st, d))

        # ---- stage → commit
        st, d = req("POST", "/api/tasks/%s/git" % tid,
                    {"action": "stage", "path": "README.md"})
        check("stage 单文件", st == 200 and d.get("ok"), d)
        st, d = req("GET", "/api/tasks/%s/git" % tid)
        check("status：暂存组 1 条", [f["path"] for f in d.get("staged", [])] == ["README.md"], d)
        st, d = req("POST", "/api/tasks/%s/git" % tid, {"action": "commit", "message": ""})
        check("commit：空信息拒绝", st == 400, (st, d))
        st, d = req("POST", "/api/tasks/%s/git" % tid, {"action": "commit", "message": "改 README"})
        check("commit 成功带回短哈希", st == 200 and d.get("ok") and d.get("commit"), d)
        st, d = req("GET", "/api/tasks/%s/git" % tid)
        check("commit 后暂存区清空", d.get("staged") == [], d.get("staged"))
        subj = git("log", "-1", "--format=%s", cwd=str(work)).stdout.strip()
        check("提交落库（信息一致）", subj == "改 README", subj)
        check("最近提交时间线首位是刚才的提交",
              (d.get("recent") or [{}])[0].get("subject") == "改 README", d.get("recent"))

        # ---- checkout
        st, d = req("POST", "/api/tasks/%s/git" % tid, {"action": "checkout", "branch": "dev"})
        check("checkout 本地分支", st == 200 and d.get("ok"), d)
        st, d = req("GET", "/api/tasks/%s/git" % tid)
        check("status 分支已切到 dev", d.get("branch") == "dev", d.get("branch"))
        st, d = req("POST", "/api/tasks/%s/git" % tid, {"action": "checkout", "branch": "nope"})
        check("checkout 不存在分支拒绝", st == 400, (st, d))
        st, d = req("POST", "/api/tasks/%s/git" % tid, {"action": "checkout", "branch": "master"})
        check("切回 master", st == 200 and d.get("ok"), d)

        # ---- discard / delete 确认闸
        (work / "README.md").write_text("baseline\n改坏\n", encoding="utf-8")
        st, d = req("POST", "/api/tasks/%s/git" % tid, {"action": "discard", "path": "README.md"})
        check("discard 无 confirm 拒绝", st == 400 and "confirm" in d.get("error", ""), (st, d))
        st, d = req("POST", "/api/tasks/%s/git" % tid,
                    {"action": "discard", "path": "README.md", "confirm": True})
        check("discard 还原到 HEAD", st == 200 and d.get("ok") and
              (work / "README.md").read_text(encoding="utf-8") == "baseline\n追加一行\n", (st, d))
        st, d = req("POST", "/api/tasks/%s/git" % tid,
                    {"action": "delete", "path": "new.md", "confirm": True})
        check("delete 未跟踪文件", st == 200 and d.get("ok") and not (work / "new.md").exists(), (st, d))

        # ---- 路径越界与附件
        st, d = req("POST", "/api/tasks/%s/git" % tid, {"action": "stage", "path": "../x"})
        check("stage 越界路径拒绝", st == 400, (st, d))
        att = work / "_attachments"
        att.mkdir(exist_ok=True)
        (att / "a.txt").write_text("素材\n", encoding="utf-8")
        st, d = req("POST", "/api/tasks/%s/git" % tid, {"action": "stage", "path": "_attachments/a.txt"})
        check("stage 附件目录拒绝", st == 400, (st, d))
        st, d = req("GET", "/api/tasks/%s/git" % tid)
        check("status 不列附件", all(not f["path"].startswith("_attachments")
                                      for f in d.get("untracked", [])), d.get("untracked"))

        # ---- stash 收起还原（HTTP 链）
        (work / "wip.txt").write_text("半成品\n", encoding="utf-8")
        st, d = req("POST", "/api/tasks/%s/git" % tid, {"action": "stash_push"})
        check("stash 收起", st == 200 and d.get("ok"), (st, d))
        st, d = req("GET", "/api/tasks/%s/git" % tid)
        refs = [s["ref"] for s in d.get("stashes", [])]
        check("stash 列表 1 条", len(refs) == 1, refs)
        st, d = req("POST", "/api/tasks/%s/git" % tid, {"action": "stash_pop", "ref": refs[0]})
        check("stash 还原", st == 200 and d.get("ok") and (work / "wip.txt").exists(), (st, d))

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    print()
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()
