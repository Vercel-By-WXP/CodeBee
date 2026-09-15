# -*- coding: utf-8 -*-
"""bee.py 端到端验证（临时服务 18817 + 种子 git 仓库/任务分支）。

流程：status → pending（含变更文件与分支）→ diff 非空 → approve（原分支收到产物、
状态变 merged）→ discard 另一任务分支 → 守卫（找不到任务/歧义标题/丢弃确认）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = "18817"
BEE = str(ROOT / "tools" / "bee.py")

results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else "　— " + str(detail)[:260]))


def bee(*a, **kw):
    env = dict(os.environ, CODEBEE_PORT=PORT)
    return subprocess.run([sys.executable, BEE, *a], capture_output=True, text=True,
                          encoding="utf-8", timeout=60, env=env, **kw)


def seed_task(data_dir, work, tid, run_id, title, with_branch=True):
    import subprocess as sp

    def g(*a):
        return sp.run(["git", *a], cwd=work, capture_output=True, text=True, encoding="utf-8")
    if with_branch:
        g("checkout", "-q", "-b", "tutti/" + tid)
        (Path(work) / ("art-%s.md" % tid[-4:])).write_text("产物\n", encoding="utf-8")
        g("add", "-A")
        g("-c", "user.name=T", "-c", "user.email=t@l", "commit", "-m", "artifacts")
        g("checkout", "-q", "master")
    (data_dir / "tasks").mkdir(parents=True, exist_ok=True)
    (data_dir / "runs" / run_id).mkdir(parents=True, exist_ok=True)
    (data_dir / "tasks" / (tid + ".json")).write_text(json.dumps({
        "id": tid, "type": "code", "engine": "code", "title": title, "goal": "g",
        "workdir": work, "git_rev": "HEAD", "git_state": "isolated", "status": "done",
        "created_at": "2026-09-14 10:00:00", "attachments": [],
    }, ensure_ascii=False), encoding="utf-8")
    (data_dir / "runs" / run_id / "run.json").write_text(json.dumps({
        "id": run_id, "kind": "orchestration", "title": title, "task_id": tid,
        "status": "done", "steps": [], "messages": [],
        "created_at": "2026-09-14 10:00:01", "started_at": "2026-09-14 10:00:01",
        "ended_at": "2026-09-14 10:05:00", "cost_usd": 0, "tokens": 0, "error": "",
        "verdict": None, "summary": "",
        "git": {"rev": "HEAD", "branch": "tutti/" + tid, "commit": "abc1234",
                "from_branch": "master", "base_commit": "abc1234",
                "restored": True, "restore_error": ""},
        "changes": {"files": [{"status": "A", "path": "art.md"}],
                    "diff": "diff --git a/art.md b/art.md\n+产物\n"},
    }, ensure_ascii=False), encoding="utf-8")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="bee-cli-"))
    data_dir = tmp / "data"
    work = str(tmp / "repo")
    (tmp / "repo").mkdir()
    import subprocess as sp
    sp.run(["git", "init"], cwd=work, capture_output=True)
    sp.run(["git", "config", "user.name", "T"], cwd=work, capture_output=True)
    sp.run(["git", "config", "user.email", "t@l"], cwd=work, capture_output=True)
    (tmp / "repo" / "README.md").write_text("base\n", encoding="utf-8")
    sp.run(["git", "add", "-A"], cwd=work, capture_output=True)
    sp.run(["git", "-c", "user.name=T", "-c", "user.email=t@l", "commit", "-m", "base"], cwd=work, capture_output=True)

    t1, r1 = "tb1seed01", "rb1seed01"
    t2, r2 = "tb2seed02", "rb2seed02"
    seed_task(data_dir, work, t1, r1, "裁决组一")
    seed_task(data_dir, work, t2, r2, "裁决组二")

    svc = subprocess.Popen([sys.executable, "-X", "utf8", str(ROOT / "app" / "main.py"),
                            "--port", PORT, "--no-browser", "--host", "127.0.0.1"],
                           env=dict(os.environ, TUTTI_DATA=str(data_dir), PYTHONPATH=str(ROOT)),
                           cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        up = False
        for _ in range(40):
            time.sleep(0.5)
            try:
                import http.client
                c = http.client.HTTPConnection("127.0.0.1", int(PORT), timeout=3)
                c.request("GET", "/api/state")
                up = c.getresponse().status == 200
                c.close()
            except OSError:
                pass
            if up:
                break
        check("临时服务启动", up)

        out = bee("status").stdout
        check("status 列出 2 任务 + 待裁决标记", "裁决组一" in out and out.count("⚑待裁决") == 2, out[:200])
        out = bee("pending").stdout
        check("pending 列分支与变更文件", "tutti/" + t1 in out and "art.md" in out, out[:300])
        out = bee("diff", t1).stdout
        check("diff 输出变更集", "diff --git a/art.md" in out, out[:120])

        # approve：控制权空闲自动接管 → 合并 → 原分支收到产物
        r = bee("approve", t1)
        check("approve 成功", r.returncode == 0 and "已合并" in r.stdout, (r.stdout + r.stderr)[:260])
        cur = sp.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=work,
                     capture_output=True, text=True).stdout.strip()
        ls = sp.run(["git", "ls-tree", "-r", "--name-only", cur], cwd=work,
                    capture_output=True, text=True).stdout
        check("产物已并入 master", "art-%s.md" % t1[-4:] in ls, cur + " | " + ls[:120])
        st = json.loads(sp.run([sys.executable, "-c",
                                "import urllib.request,json;print(urllib.request.urlopen('http://127.0.0.1:%s/api/state').read().decode())" % PORT],
                               capture_output=True, text=True).stdout)
        t1v = next(t for t in st["tasks"] if t["id"] == t1)
        check("任务状态变 merged", t1v.get("git_state") == "merged", t1v.get("git_state"))

        # pending 只剩 1 个
        out = bee("pending").stdout
        check("pending 只剩裁决组二", out.count("tutti/") == 1 and "裁决组二" in out, out[:160])

        # discard：交互确认（no 取消 / yes 丢弃）
        r = bee("discard", t2, input="no\n")
        check("discard 输 no → 取消且分支仍在", "已取消" in (r.stdout + r.stderr) and
              sp.run(["git", "rev-parse", "--verify", "--quiet", "refs/heads/tutti/" + t2],
                     cwd=work, capture_output=True).returncode == 0, r.stdout[:160])
        r = bee("discard", t2, input="yes\n")
        check("discard 输 yes → 分支删除", r.returncode == 0 and "已丢弃" in r.stdout, (r.stdout + r.stderr)[:200])

        # 守卫：找不到 / 歧义
        r = bee("diff", "不存在")
        check("找不到任务显式报错", r.returncode != 0 and "找不到任务" in r.stderr, r.stderr[:140])
        seed_task(data_dir, work, "tb3seed03", "rb3seed03", "裁决组", with_branch=False)
        r = bee("diff", "裁决组")
        check("标题歧义拒绝猜测", r.returncode != 0 and "匹配" in r.stderr, r.stderr[:160])
    finally:
        svc.terminate()
        try:
            svc.wait(timeout=10)
        except Exception:
            svc.kill()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    bad = results.count(False)
    print("\n%s %d/%d" % ("ALL PASS" if not bad else "FAILED", len(results) - bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
