# -*- coding: utf-8 -*-
"""run_end 钩子：任务完成后 AI 自动复查改动，没问题就 commit + push（2026-09-23 用户立项）。

两段式设计（钩子有 120s 硬超时，AI 复查经常不止）：
- 钩子进程（stdin 收事件 JSON）：过滤 run_end + status==done，解析任务工作目录，
  派生一个脱离控制台的 worker 子进程后立即退出——绝不让复查拖住收尾路径。
- worker 进程：加锁 → 收集 git diff（含新增文件）→ 内置智能体直连复查
  （明显 bug / 破坏性删除 / 敏感信息泄漏 / 乱码半成品）→ 判 VERDICT:OK 才
  git add -A + commit + push，BAD 则原样保留并通知原因。

安全边界：
- failed/cancelled 不触发；非 git 目录、无改动、CodeBee 自家仓库一律跳过。
- 提交身份缺失时以 CodeBee 名义兜底（-c user.name/email），不污染用户配置。
- 全程日志落 DATA_DIR/logs/hook_autocommit.log；任何异常只记日志不外抛。

钩子注册（DATA_DIR/hooks.json）：
  {"event": "run_end", "cmd": "python \\"<本文件绝对路径>\\"", "timeout_s": 15}
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DIFF_MAX_CHARS = 48000          # 给模型的 diff 上限（防撑爆上下文）
AI_TIMEOUT = 240                # 复查单轮上限（秒）
LOCK_STALE_S = 15 * 60          # 锁超时：超过视为残留，直接接管
VERDICT_RE = re.compile(r"VERDICT\s*[:：]\s*(OK|BAD)", re.IGNORECASE)
COMMIT_RE = re.compile(r"^\s*COMMIT\s*[:：]\s*(.+)$", re.MULTILINE)

_log_fh = None


def log(msg):
    """追加一行到 DATA_DIR/logs/hook_autocommit.log；失败静默。"""
    global _log_fh
    try:
        if _log_fh is None:
            from app.core import paths
            d = paths.DATA_DIR / "logs"
            d.mkdir(parents=True, exist_ok=True)
            _log_fh = open(d / "hook_autocommit.log", "ab")
        line = "%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
        _log_fh.write(line.encode("utf-8", "replace"))
        _log_fh.flush()
    except Exception:
        pass


# ---------------------------------------------------------------- 钩子入口

def dispatch():
    """钩子模式：stdin 收事件 JSON，过滤后派生 worker，立即返回。"""
    try:
        raw = sys.stdin.buffer.read()
        payload = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except Exception:
        return 0
    if payload.get("event") != "run_end":
        return 0
    ctx = payload.get("ctx") or {}
    if str(ctx.get("status") or "") != "done":
        return 0                      # 只在成功完成时复查提交
    task_id = str(payload.get("task_id") or "")
    if not task_id:
        return 0

    workdir = ""
    try:
        from app.core import paths
        tf = paths.DATA_DIR / "tasks" / (task_id + ".json")
        task = json.loads(tf.read_text(encoding="utf-8"))
        workdir = str(task.get("workdir") or "")
    except Exception:
        return 0
    if not workdir or not Path(workdir).is_dir():
        return 0

    argv = [sys.executable, str(Path(__file__).resolve()),
            "--worker", "--task", task_id,
            "--run", str(payload.get("run_id") or "")]
    flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags |= subprocess.DETACHED_PROCESS
    try:
        logf = open(paths.DATA_DIR / "logs" / "hook_autocommit.log", "ab")
    except Exception:
        logf = subprocess.DEVNULL
    try:
        subprocess.Popen(argv, stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
                         cwd=str(ROOT), creationflags=flags)
    except Exception as e:
        log("dispatch 派生 worker 失败 task=%s: %s" % (task_id[:12], e))
    return 0


# ---------------------------------------------------------------- worker

def _git(wd, args, timeout=60):
    """跑一条 git 命令，返回 (ok, stdout, stderr)。"""
    from app.core import runner
    r = runner.run_process(argv=["git", "-C", str(wd)] + list(args), timeout=timeout)
    return bool(r["ok"]), r.get("stdout") or "", r.get("stderr") or ""


def _load_lock(wd):
    return Path(wd) / ".git" / "codebee-autocommit.lock"


def _acquire_lock(wd):
    """锁文件放 .git/ 里（永不入库）。活着直接跳过；超时残留接管。"""
    lock = _load_lock(wd)
    try:
        if lock.exists():
            age = time.time() - lock.stat().st_mtime
            if age < LOCK_STALE_S:
                return False
        lock.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except Exception:
        return False


def _build_prompt(title, wd, diff, stats):
    return (
        "你是代码审查门卫。任务《%s》刚在仓库 %s 完成执行，下面是本轮产生的未提交改动。"
        "只做检查：不要修改任何文件，不要执行任何 git 写操作。\n"
        "重点检查：\n"
        "1) 明显 bug、语法错误、逻辑断裂；\n"
        "2) 误删或破坏既有功能；\n"
        "3) 敏感信息泄漏（密钥、token、密码、内网地址）；\n"
        "4) 乱码文件、明显的半成品或调试残留。\n"
        "没有发现问题：倒数第二行输出 COMMIT:一句简洁的中文提交说明，最后一行单独输出 VERDICT:OK。\n"
        "发现问题：最后一行单独输出 VERDICT:BAD，并用几句话说明问题所在。\n\n"
        "改动统计：\n%s\n\n改动内容：\n%s" % (title, wd, stats, diff)
    )


def _parse_verdict(text):
    """取最后一次出现的判定；返回 ("OK"|"BAD"|"", commit_msg)。"""
    verdict = ""
    for m in VERDICT_RE.finditer(text or ""):
        verdict = m.group(1).upper()
    msg = ""
    m = COMMIT_RE.search(text or "")
    if m:
        msg = m.group(1).strip().strip("`*「」\"'").strip()
    return verdict, msg[:100]


def _commit_identity_args(wd):
    """仓库没配提交身份时以 CodeBee 兜底，避免 commit 因 identity 报错。"""
    ok, out, _ = _git(wd, ["config", "user.email"])
    if ok and (out or "").strip():
        return []
    return ["-c", "user.name=CodeBee", "-c", "user.email=codebee@local"]


def worker(task_id, run_id):
    log("== worker 启动 task=%s run=%s" % (task_id[:12], run_id[:12]))
    from app.core import paths
    try:
        task = json.loads((paths.DATA_DIR / "tasks" / (task_id + ".json")).read_text(encoding="utf-8"))
    except Exception as e:
        log("任务文件读取失败: %s" % e)
        return 0
    wd = task.get("workdir") or ""
    title = task.get("goal") or task.get("title") or task_id
    if not wd or not Path(wd).is_dir():
        log("工作目录不可用: %r" % wd)
        return 0
    if Path(wd).resolve() == ROOT:
        log("工作目录是 CodeBee 自家仓库，跳过（防并行在制品误提交）")
        return 0

    if not _acquire_lock(wd):
        log("同目录已有复查在跑（锁未超时），跳过: %s" % wd)
        return 0
    try:
        return _worker_body(task_id, run_id, title, wd)
    finally:
        try:
            _load_lock(wd).unlink()
        except Exception:
            pass


def _worker_body(task_id, run_id, title, wd):
    ok, out, err = _git(wd, ["rev-parse", "--is-inside-work-tree"])
    if not ok or out.strip() != "true":
        log("非 git 仓库，跳过: %s (%s)" % (wd, err.strip()[:80]))
        return 0

    _ok, status, _e = _git(wd, ["status", "--porcelain"])
    files = [ln for ln in (status or "").splitlines() if ln.strip()]
    if not files:
        log("无改动，跳过: %s" % wd)
        return 0

    has_head = _git(wd, ["rev-parse", "--verify", "-q", "HEAD"])[0]
    ok, _, err = _git(wd, ["add", "-A", "-N"])
    if not ok:
        log("git add -N 失败: %s" % err.strip()[:200])
        return 0
    diff_cmd = ["diff", "HEAD"] if has_head else ["diff"]
    _, diff, _e = _git(wd, diff_cmd)
    _, stats, _e2 = _git(wd, ["diff", "--stat"])
    if not (diff or "").strip():
        log("diff 为空（可能只有二进制/忽略项），跳过")
        return 0
    truncated = ""
    if len(diff) > DIFF_MAX_CHARS:
        diff = diff[:DIFF_MAX_CHARS]
        truncated = "\n（改动过长已截断，仅展示前 %d 字符）" % DIFF_MAX_CHARS

    # AI 复查：复用内置智能体直连链路（编排者配置优先）
    from app.core import builtin_agent, notify
    bi = builtin_agent.resolve()
    if not bi:
        log("无可用模型供应商，跳过复查（改动保持未提交）")
        notify.push_text("⚠️ CodeBee 自动复查跳过：无可用模型，任务《%s》改动未提交。" % str(title)[:60])
        return 0
    r = builtin_agent.run(bi, _build_prompt(str(title)[:120], wd, diff + truncated, stats or "（无统计）"),
                          wd, timeout=AI_TIMEOUT)
    if not r.get("ok"):
        log("AI 复查失败（%s）：%s" % (bi.get("model"), str(r.get("error"))[:200]))
        notify.push_text("⚠️ CodeBee 自动复查失败：任务《%s》改动未提交（%s）。"
                         % (str(title)[:60], str(r.get("error"))[:80]))
        return 0
    verdict, msg = _parse_verdict(r.get("text") or "")
    tokens = (r.get("usage") or {}).get("total", 0)
    if verdict != "OK":
        log("AI 判定不通过（%s），保持未提交：%s" % (bi.get("model"), (r.get("text") or "")[:400]))
        notify.push_text("🛑 CodeBee 自动复查未通过：任务《%s》改动未提交。原因：%s"
                         % (str(title)[:60], (r.get("text") or "")[:200]))
        return 0
    if not msg:
        msg = "CodeBee 自动提交：任务《%s》成果" % str(title)[:60]

    ok, _, err = _git(wd, ["add", "-A"])
    if not ok:
        log("git add 失败: %s" % err.strip()[:200])
        return 0
    ok, out, err = _git(wd, _commit_identity_args(wd) + ["commit", "-m", msg], timeout=120)
    if not ok:
        log("git commit 失败: %s" % (err or out).strip()[:300])
        notify.push_text("⚠️ CodeBee 复查通过但提交失败：任务《%s》（%s）"
                         % (str(title)[:60], (err or out).strip()[:120]))
        return 0

    n_files = len(files)
    _, remotes, _e = _git(wd, ["remote"])
    if not (remotes or "").strip():
        log("已提交（仓库无远端，未推送）：%d 个文件，%d tokens — %s" % (n_files, tokens, msg))
        notify.push_text("✅ CodeBee 自动复查通过并提交（无远端未推送）：《%s》%d 个文件 — %s"
                         % (str(title)[:60], n_files, msg))
        return 0
    ok, out, err = _git(wd, ["push"], timeout=180)
    if not ok:
        ok, out, err = _git(wd, ["push", "-u", "origin", "HEAD"], timeout=180)
    if ok:
        log("已提交并推送：%d 个文件，%d tokens — %s" % (n_files, tokens, msg))
        notify.push_text("✅ CodeBee 自动复查通过，已提交并推送：《%s》%d 个文件 — %s"
                         % (str(title)[:60], n_files, msg))
    else:
        log("已提交但推送失败：%s — %s" % (msg, (err or out).strip()[:200]))
        notify.push_text("⚠️ CodeBee 已提交但推送失败：《%s》— %s"
                         % (str(title)[:60], (err or out).strip()[:120]))
    return 0


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "--worker":
        task_id = argv[argv.index("--task") + 1] if "--task" in argv else ""
        run_id = argv[argv.index("--run") + 1] if "--run" in argv else ""
        try:
            return worker(task_id, run_id)
        except Exception as e:
            log("worker 异常: %r" % e)
            return 0
    return dispatch()


if __name__ == "__main__":
    sys.exit(main())
